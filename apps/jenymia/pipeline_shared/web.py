"""Web sources: HTML pages and PDF datasheets.

1. httpx fetches the raw HTML and reports status, final URL after redirects
   and the time of the fetch - needed for error analysis and re-crawls.
2. trafilatura pulls out the article text without navigation and footers.
3. BeautifulSoup reads head data and the heading outline, extruct reads the
   structured data the page publishes about itself (JSON-LD, OpenGraph).

A PDF has none of that - no head, no canonical, no structured data - so a
datasheet yields its text and little else. Manufacturers publish technical
specifications either way, and those numbers are what makes products
comparable.

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

import io
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
from pypdf import PdfReader

from .errors import RejectedUrlError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 5
# An oversized page is a permanent error and fails the whole order, so
# the cap has to sit above what a manufacturer brochure as PDF weighs -
# 10 to 20 MB is common. The stream still stops at the cap.
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_HEADINGS = 200
MAX_STRUCTURED_DATA_CHARS = 200_000
ALLOWED_SCHEMES = ("http", "https")
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
PDF_CONTENT_TYPE = "application/pdf"
ACCEPTED_CONTENT_TYPES = (*HTML_CONTENT_TYPES, PDF_CONTENT_TYPE)
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
    print("WEB.FETCH - STARTED", url)
    body, encoding, final_url, status, content_type = _get(url)
    if content_type.startswith(PDF_CONTENT_TYPE):
        source = _from_pdf(body, final_url, status, content_type)
        print("WEB.FETCH - DONE", url)
        return source
    html = body.decode(encoding or "utf-8", errors="replace")
    source = _from_html(html, final_url, status, content_type)
    print("WEB.FETCH - DONE", url)
    return source


def _from_pdf(body: bytes, final_url: str, status: int, content_type: str) -> WebSource:
    """A datasheet: text and the little metadata a PDF carries.

    Everything a web page offers through its head - description, canonical,
    OpenGraph, headings - has no equivalent here and stays empty. The site
    name falls back to the host, which is what a citation needs.
    """
    print("WEB._FROM_PDF - STARTED", final_url)
    try:
        reader = PdfReader(io.BytesIO(body))
        pages = len(reader.pages)
        info = reader.metadata
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:  # noqa: BLE001 - pypdf raises a dozen types
        # Encrypted, damaged or not really a PDF. Retrying downloads the same
        # bytes again, so this is permanent.
        raise RejectedUrlError(
            f"Unreadable PDF: {type(error).__name__}: {error}"
        ) from None

    # The metadata is read after the try on purpose. pypdf resolves these
    # entries on access and can raise on a broken reference, but by then the
    # text is already extracted - and one permanently failed source fails the
    # whole order. A title is not worth that; both helpers swallow and log.
    title = _pdf_str(info, "title")
    author = _pdf_str(info, "author")
    filename = urlparse(final_url).path.rsplit("/", 1)[-1]

    print("WEB._FROM_PDF - DONE", final_url)
    return WebSource(
        raw_text=text.strip(),
        final_url=final_url[:1000],
        http_status=status,
        fetched_at=timezone.now(),
        title=(title or filename)[:300],
        meta_description="",
        canonical_url="",
        site_name=(urlparse(final_url).hostname or "")[:200],
        author=author[:200],
        published_at=_aware(_pdf_date(info, "creation_date")),
        modified_at=_aware(_pdf_date(info, "modification_date")),
        language="",
        meta={"content_type": content_type, "pages": pages},
    )


def _pdf_str(info, name: str) -> str:
    """PDF metadata can be a byte string in any encoding, an unresolved
    object, or missing. Anything that is not text becomes empty rather than
    its repr, which would otherwise end up as the public citation label.

    NUL bytes are removed by services on the way into the database, together
    with every other source field."""
    try:
        value = getattr(info, name, None) if info else None
    except Exception:  # noqa: BLE001 - pypdf resolves the entry on access
        logger.warning("Unreadable %s in PDF metadata", name)
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _pdf_date(info, name: str) -> datetime | None:
    """pypdf parses the date on access and raises on the malformed values
    older writers produce - a date is not worth losing the datasheet for."""
    try:
        return getattr(info, name, None) if info else None
    except Exception:  # noqa: BLE001 - ValueError today, anything tomorrow
        logger.warning("Unparsable %s in PDF metadata", name)
        return None


def _from_html(html: str, final_url: str, status: int, content_type: str) -> WebSource:
    print("WEB._FROM_HTML - STARTED", final_url)
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

    print("WEB._FROM_HTML - DONE", final_url)
    return WebSource(
        # Tables stay in explicitly, because on a maker's page that is where
        # the specification sits - material, dimensions, what is in the box.
        # Lists are always kept by trafilatura. favor_recall keeps borderline
        # blocks that the strict default drops.
        raw_text=trafilatura.extract(html, include_tables=True, favor_recall=True)
        or "",
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


def _get(url: str) -> tuple[bytes, str, str, int, str]:
    """Follow redirects by hand so every hop is checked, stream the body so
    a huge or endless response stops at the cap.

    Returns (body, encoding, final url, status, content type). The body stays
    bytes because a PDF is not text; the caller decodes what it knows how to
    read."""
    print("WEB._GET - STARTED", url)
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
                if not content_type.startswith(ACCEPTED_CONTENT_TYPES):
                    raise RejectedUrlError(
                        f"Not an HTML page or PDF: {content_type or 'unknown'}"
                    )

                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RejectedUrlError(
                            f"Page larger than {MAX_RESPONSE_BYTES} bytes"
                        )
                    chunks.append(chunk)
                print("WEB._GET - DONE", url)
                return (
                    b"".join(chunks),
                    response.encoding or "",
                    str(response.url),
                    response.status_code,
                    content_type,
                )
    raise RejectedUrlError(f"More than {MAX_REDIRECTS} redirects")


def _check_target(url: str) -> None:
    """Refuse anything that is not a public http(s) address."""
    print("WEB._CHECK_TARGET - STARTED", url)
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
    print("WEB._CHECK_TARGET - DONE", url)


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
    return _aware(parsed)


def _aware(value: datetime | None) -> datetime | None:
    """A date without an offset is taken as UTC rather than guessed."""
    if value is None:
        return None
    return value if value.tzinfo else timezone.make_aware(value, UTC)
