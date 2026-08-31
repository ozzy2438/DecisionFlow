import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest

import decisionflow.download as download_module
from decisionflow.constants import (
    BPI_2019_DOI,
    BPI_2019_EXPECTED_ACTIVITIES,
    BPI_2019_EXPECTED_CASES,
    BPI_2019_EXPECTED_EVENTS,
    BPI_2019_MD5,
    BPI_2019_SIZE_BYTES,
)
from decisionflow.download import download_bpi_2019, md5_file


def test_official_source_contract_is_versioned() -> None:
    assert BPI_2019_DOI == "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
    assert BPI_2019_MD5 == "4eb909242351193a61e1c15b9c3cc814"
    assert BPI_2019_SIZE_BYTES == 728_558_522
    assert (BPI_2019_EXPECTED_CASES, BPI_2019_EXPECTED_EVENTS) == (251_734, 1_595_923)
    assert BPI_2019_EXPECTED_ACTIVITIES == 42


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._stream = BytesIO(payload)
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self._stream.read(size)


def _patch_tiny_source(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(download_module, "BPI_2019_SIZE_BYTES", len(payload))
    monkeypatch.setattr(download_module, "BPI_2019_MD5", digest)
    monkeypatch.setattr(download_module, "urlopen", lambda *_args, **_kwargs: FakeResponse(payload))


def test_download_is_atomic_verified_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"tiny-xes-payload"
    _patch_tiny_source(monkeypatch, payload)
    destination = tmp_path / "sample.xes"

    first = download_bpi_2019(destination)
    monkeypatch.setattr(
        download_module,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("idempotent download should not call network"),
    )
    second = download_bpi_2019(destination)

    assert first["downloaded"] is True
    assert second["downloaded"] is False
    assert destination.read_bytes() == payload
    assert md5_file(destination) == hashlib.md5(payload, usedforsecurity=False).hexdigest()
    metadata = json.loads(destination.with_suffix(".xes.metadata.json").read_text())
    assert metadata["doi"] == BPI_2019_DOI


def test_download_rejects_wrong_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"short"
    _patch_tiny_source(monkeypatch, payload)
    monkeypatch.setattr(download_module, "BPI_2019_SIZE_BYTES", len(payload) + 1)

    with pytest.raises(ValueError, match="size mismatch"):
        download_bpi_2019(tmp_path / "wrong-size.xes")


def test_force_replaces_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"replacement"
    _patch_tiny_source(monkeypatch, payload)
    destination = tmp_path / "sample.xes"
    destination.write_bytes(b"stale")
    destination.with_suffix(".xes.part").write_bytes(b"partial")

    result = download_bpi_2019(destination, force=True)

    assert result["downloaded"] is True
    assert destination.read_bytes() == payload
