from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import Request, urlopen


def _is_verified(path: Path, expected_bytes: int | None, expected_sha256: str) -> bool:
    if not path.is_file() or (
        expected_bytes is not None and path.stat().st_size != expected_bytes
    ):
        return False
    digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
    return digest == expected_sha256


def ensure_cached_file(
    *, url: str, destination: Path, expected_bytes: int | None, expected_sha256: str
) -> Path:
    """Reuse only verified bytes; atomically replace missing or invalid cache entries."""
    if _is_verified(destination, expected_bytes, expected_sha256):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = Request(url, headers={"User-Agent": "ai-analytics-poc-dataset-spike/0.1"})
    try:
        with urlopen(request, timeout=60) as response, partial.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if not _is_verified(partial, expected_bytes, expected_sha256):
            raise ValueError(
                f"Downloaded artifact failed size or SHA-256 verification: {url}"
            )
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination
