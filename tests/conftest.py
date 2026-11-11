"""Shared fixtures: synthetic WAV files and isolated AppData."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


def _chunk(chunk_id: bytes, data: bytes) -> bytes:
    body = chunk_id + struct.pack("<I", len(data)) + data
    if len(data) & 1:
        body += b"\x00"
    return body


def make_wav(
    path: Path,
    *,
    bext: bytes | None = None,
    ixml: bytes | None = None,
    data: bytes = b"\x01\x02\x03\x04" * 25,
    junk: bytes | None = b"junk!",
) -> Path:
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    chunks = [_chunk(b"fmt ", fmt)]
    if junk is not None:
        chunks.append(_chunk(b"JUNK", junk))
    if bext is not None:
        chunks.append(_chunk(b"bext", bext))
    chunks.append(_chunk(b"data", data))
    if ixml is not None:
        chunks.append(_chunk(b"iXML", ixml))
    body = b"WAVE" + b"".join(chunks)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def make_bext(description: str = "OLD", originator: str = "TEST") -> bytes:
    data = bytearray(602)
    data[0:256] = description.encode("ascii", "ignore").ljust(256, b"\x00")[:256]
    data[256:288] = originator.encode("ascii", "ignore").ljust(32, b"\x00")[:32]
    data[288:320] = b"REFERENCE".ljust(32, b"\x00")
    data[320:330] = b"2026-01-02"
    data[330:338] = b"03:04:05"
    struct.pack_into("<H", data, 346, 1)
    return bytes(data)


def make_ixml(body: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<BWFXML>\n"
        "  <PROJECT>OLD_PROJECT</PROJECT>\n"
        f"{body}"
        "</BWFXML>\n"
    ).encode()


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Keep settings and journals out of the real user profile."""
    appdata = tmp_path / "appdata"
    localappdata = tmp_path / "localappdata"
    appdata.mkdir()
    localappdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    return {"appdata": appdata, "localappdata": localappdata}


@pytest.fixture
def wav_factory(tmp_path):
    counter = 0

    def build(name: str = "sample.wav", **kwargs) -> Path:
        nonlocal counter
        counter += 1
        folder = tmp_path / "audio" / f"set{counter}"
        folder.mkdir(parents=True, exist_ok=True)
        return make_wav(folder / name, **kwargs)

    return build
