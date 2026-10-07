"""Temporary extractor for Easy Apply markup around Next buttons."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data"
TARGET = ROOT / "easy_apply_failure_4468860218_20260918T033923104055Z.html"
PATTERNS = [
    r'aria-label=["\']Next["\']',
    r"interop-shadowdom",
    r"Apply to ",
    r"aria-valuenow",
    r"Contact info",
    r'role=["\']dialog["\']',
    r'data-test-modal',
    r"artdeco-modal",
    r"<footer",
    r'type=["\']tel["\']',
]


def main() -> None:
    text = TARGET.read_text(encoding="utf-8", errors="replace")
    for pat in PATTERNS:
        matches = list(re.finditer(pat, text, re.IGNORECASE))
        print("PATTERN", pat, "count", len(matches))
        for match in matches[:2]:
            start = max(0, match.start() - 400)
            end = min(len(text), match.end() + 500)
            snippet = text[start:end].replace("\n", " ")
            print("---")
            print(snippet[:1200])
            print()


if __name__ == "__main__":
    main()
