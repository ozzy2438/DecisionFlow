"""Checksum-pinned downloader for the public BPI Challenge 2019 artifact."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from decisionflow.constants import (
    BPI_2019_ARTICLE_URL,
    BPI_2019_DOI,
    BPI_2019_DOWNLOAD_URL,
    BPI_2019_MD5,
    BPI_2019_SIZE_BYTES,
)

CHUNK_SIZE = 8 * 1024 * 1024


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_existing(path: Path) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == BPI_2019_SIZE_BYTES
        and md5_file(path) == BPI_2019_MD5
    )


def download_bpi_2019(destination: Path, *, force: bool = False) -> dict[str, Any]:
    """Download, resume, and atomically publish the official BPI 2019 XES file."""

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    if force:
        destination.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    elif _validated_existing(destination):
        return _result(destination, downloaded=False)

    resume_at = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "DecisionFlow/0.1 (BPI-2019 research data ingestion)"}
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"

    request = Request(BPI_2019_DOWNLOAD_URL, headers=headers)
    with urlopen(request, timeout=120) as response:
        status = getattr(response, "status", 200)
        if resume_at and status != 206:
            resume_at = 0
        mode = "ab" if resume_at else "wb"
        with partial.open(mode) as handle:
            while chunk := response.read(CHUNK_SIZE):
                handle.write(chunk)

    actual_size = partial.stat().st_size
    if actual_size != BPI_2019_SIZE_BYTES:
        raise ValueError(
            f"BPI 2019 download size mismatch: expected {BPI_2019_SIZE_BYTES}, got {actual_size}"
        )
    actual_md5 = md5_file(partial)
    if actual_md5 != BPI_2019_MD5:
        raise ValueError(f"BPI 2019 checksum mismatch: expected {BPI_2019_MD5}, got {actual_md5}")

    os.replace(partial, destination)
    metadata_path = destination.with_suffix(destination.suffix + ".metadata.json")
    metadata_path.write_text(
        json.dumps(
            {
                "doi": BPI_2019_DOI,
                "landing_page": BPI_2019_ARTICLE_URL,
                "download_url": BPI_2019_DOWNLOAD_URL,
                "size_bytes": BPI_2019_SIZE_BYTES,
                "md5": BPI_2019_MD5,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return _result(destination, downloaded=True)


def _result(path: Path, *, downloaded: bool) -> dict[str, Any]:
    return {
        "path": str(path),
        "downloaded": downloaded,
        "size_bytes": path.stat().st_size,
        "md5": BPI_2019_MD5,
        "doi": BPI_2019_DOI,
    }
