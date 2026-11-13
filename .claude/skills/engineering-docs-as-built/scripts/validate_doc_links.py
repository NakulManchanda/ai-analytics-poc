#!/usr/bin/env python3
"""Validate local Markdown links in provided repo files (stdlib only).

Usage:
    python .claude/skills/engineering-docs-as-built/scripts/validate_doc_links.py docs/inference.md README.md

Checks relative link target existence. Skips external URLs, fragment-only links, and
non-file schemes; this does not validate in-page anchors or remote URLs.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK = re.compile(r"(?<!!)\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[\"'][^\"']+[\"'])?\)")
CODE_FENCE = re.compile(r"^\s*(?:`{3,}|~{3,})")


def validate(path: Path) -> list[str]:
    failures: list[str] = []
    inside_code = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if CODE_FENCE.match(line):
            inside_code = not inside_code
            continue
        if inside_code:
            continue
        for match in LINK.finditer(line):
            raw = match.group(1) or match.group(2)
            url = urlsplit(raw)
            if url.scheme or raw.startswith(("#", "//")):
                continue
            target = unquote(url.path)
            if not target:
                continue
            local = (path.parent / target).resolve()
            if not local.exists():
                failures.append(f"{path}:{lineno}: missing local link {raw}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    errors: list[str] = []
    for path in args.files:
        if not path.is_file():
            errors.append(f"missing markdown input: {path}")
        else:
            errors.extend(validate(path))
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"Local links verified in {len(args.files)} Markdown file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
