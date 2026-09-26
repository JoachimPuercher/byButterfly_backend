"""The prompt files in prompts/ and their versions.

The first line of every prompt file is 'version: <id>'. The file is the
history (git), the version string is what makes it possible to find out
later which products a given prompt produced.
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def read_prompt(name: str) -> tuple[str, str]:
    """Return (body, version) of a prompt file.

    Files that go through str.format (shared/base.md, shared/translate.md)
    may contain
    no braces except their placeholders. The group files (groups/) are
    appended
    unformatted and may use braces freely."""
    print("PROMPT_FILES.READ_PROMPT - STARTED", name)
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    first_line, _, body = text.partition("\n")
    if not first_line.startswith("version:"):
        raise ValueError(f"Prompt {name} has no version line.")
    print("PROMPT_FILES.READ_PROMPT - DONE", name)
    return body.strip(), first_line.split(":", 1)[1].strip()
