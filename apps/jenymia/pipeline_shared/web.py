"""Web pages: fetch once, take three views of the same HTML.

1. httpx fetches the raw HTML and reports status, final URL after redirects
   and the time of the fetch - needed for error analysis and re-crawls.
2. trafilatura pulls out the article text without navigation and footers.
3. BeautifulSoup reads head data and the heading outline, extruct reads the
   structured data the page publishes about itself (JSON-LD, OpenGraph).

The result is a dataclass whose field names are the WebUrl columns. This
module knows no models, and the models know no parser.

Hardening: the worker sits inside the private network (database, Redis),
so a page that redirects to http://127.0.0.1:... or a cloud metadata
address would turn the fetcher into a proxy into that network. Every hop
is checked before it is requested, only http(s) is followed, the body is
read in chunks with a size cap, and non-HTML is rejected. What is not
covered yet, and must be before URLs come from users (backlog 5.6): DNS
rebinding between the check and the connect, and an egress allowlist.
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import extruct
import httpx
import trafilatura
from bs4 import BeautifulSoup
from django.utils import timezone

from .errors import RejectedUrlError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_HEADINGS = 200
MAX_STRUCTURED_DATA_CHARS = 200_000
ALLOWED_SCHEMES = ("http", "https")
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
# Sites serve different markup to unknown clients; identify honestly.
USER_AGENT = "jenymia-bot/1.0 (+https://jenymia.de)"


@dataclass
class WebSource:
    raw_text: str
    final_url: str
    http_status: int
    fetched_at: datetime
    title: str
    meta_description: str
    canonical_url: str
    site_name: str
    author: str
    published_at: datetime | None
    modified_at: datetime | None
    language: str
    headings: list[dict] = field(default_factory=list)
    jsonld: list[dict] = field(default_factory=list)
    opengraph: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def fetch(url: str) -> WebSource:
    html, final_url, status, content_type = _get(url)

    soup = BeautifulSoup(html, "lxml")
    canonical = _link(soup, "canonical")
    structured = extruct.extract(
        html, base_url=final_url, syntaxes=["json-ld", "opengraph"]
    )
    jsonld = structured.get("json-ld", [])
    if len(str(jsonld)) > MAX_STRUCTURED_DATA_CHARS:
        logger.warning("Dropping oversized JSON-LD from %s", final_url)
        jsonld = []
    opengraph: dict[str, Any] = {}
    for entry in structured.get("opengraph", []):
        opengraph.update(entry)

    return WebSource(
        raw_text=trafilatura.extract(html) or "",
        final_url=final_url[:1000],
        http_status=status,
        fetched_at=timezone.now(),
        title=(_meta(soup, "og:title") or _title(soup))[:300],
        meta_description=_meta(soup, "description") or _meta(soup, "og:description"),
        canonical_url=urljoin(final_url, canonical)[:1000] if canonical else "",
        site_name=_meta(soup, "og:site_name")[:200],
        author=(_meta(soup, "author") or _meta(soup, "article:author"))[:200],
        published_at=_parse_datetime(_meta(soup, "article:published_time")),
        modified_at=_parse_datetime(_meta(soup, "article:modified_time")),
        language=(soup.html.get("lang", "") if soup.html else "")[:10],
        headings=[
            {"level": tag.name, "text": tag.get_text(strip=True)[:300]}
            for tag in soup.find_all(
                ["h1", "h2", "h3", "h4", "h5", "h6"], limit=MAX_HEADINGS
            )
        ],
        jsonld=jsonld,
        opengraph=opengraph,
        meta={"content_type": content_type},
    )


def _get(url: str) -> tuple[str, str, int, str]:
    """Follow redirects by hand so every hop is checked, stream the body so
    a huge or endless response stops at the cap. Returns (html, final url,
    status, content type)."""
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_target(url)
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                if 400 <= response.status_code < 500:
                    raise RejectedUrlError(f"HTTP {response.status_code} for {url}")
                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if not content_type.startswith(HTML_CONTENT_TYPES):
                    raise RejectedUrlError(
                        f"Not an HTML page: {content_type or 'unknown'}"
                    )

                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RejectedUrlError(
                            f"Page larger than {MAX_RESPONSE_BYTES} bytes"
                        )
                    chunks.append(chunk)
                html = b"".join(chunks).decode(
                    response.encoding or "utf-8", errors="replace"
                )
                return html, str(response.url), response.status_code, content_type
    raise RejectedUrlError(f"More than {MAX_REDIRECTS} redirects")


def _check_target(url: str) -> None:
    """Refuse anything that is not a public http(s) address."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        raise RejectedUrlError(f"Refusing URL {url!r}: only public http(s) is fetched")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as error:
        raise RejectedUrlError(f"Cannot resolve {parsed.hostname}: {error}") from None
    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise RejectedUrlError(
                f"Refusing {parsed.hostname}: resolves to non-public address {ip}"
            )


def _title(soup: BeautifulSoup) -> str:
    return soup.title.string.strip() if soup.title and soup.title.string else ""


def _meta(soup: BeautifulSoup, name: str) -> str:
    """Read a meta tag by name or by property - pages use both spellings."""
    tag = soup.find("meta", attrs={"name": name}) or soup.find(
        "meta", attrs={"property": name}
    )
    return tag.get("content", "").strip() if tag else ""


def _link(soup: BeautifulSoup, rel: str) -> str:
    tag = soup.find("link", attrs={"rel": rel})
    return tag.get("href", "").strip() if tag else ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparsable date in page metadata: %r", value)
        return None
    # A page date without an offset is taken as UTC rather than guessed.
    return parsed if parsed.tzinfo else timezone.make_aware(parsed, UTC)
