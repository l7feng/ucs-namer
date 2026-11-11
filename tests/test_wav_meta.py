from __future__ import annotations

import struct

import pytest

from tests.conftest import make_bext, make_ixml
from ucs_namer.audio import wav_meta


def _chunks(path):
    result = {}
    with path.open("rb") as handle:
        for chunk in wav_meta._scan(path):
            handle.seek(chunk.data_offset)
            result[chunk.chunk_id] = handle.read(chunk.size)
    return result


def test_read_metadata_without_chunks(wav_factory):
    path = wav_factory()
    meta = wav_meta.read_metadata(path)
    assert meta["ok"] is True
    assert meta["has_bext"] is False
    assert meta["has_ixml"] is False
    assert meta["description"] == ""


def test_patch_creates_bext_and_ixml_and_preserves_other_chunks(wav_factory):
    path = wav_factory(junk=b"junk!")
    before = _chunks(path)
    values = {"SW_UCS": "GUNAuto_Pistol_Cock", "SW_CATEGORY": "GUNAuto"}
    result = wav_meta.patch_metadata(path, "GUNAuto_Pistol_Cock", values)

    assert result["bext_written"] is True
    assert result["ixml_written"] is True
    meta = wav_meta.read_metadata(path)
    assert meta["has_bext"] is True
    assert meta["has_ixml"] is True
    assert meta["description"] == "GUNAuto_Pistol_Cock"
    assert meta["ixml"]["SW_UCS"] == "GUNAuto_Pistol_Cock"
    assert meta["ixml"]["SW_CATEGORY"] == "GUNAuto"

    after = _chunks(path)
    assert after[b"fmt "] == before[b"fmt "]
    assert after[b"JUNK"] == before[b"JUNK"]
    assert after[b"data"] == before[b"data"]
    order = [chunk.chunk_id for chunk in wav_meta._scan(path)]
    assert order.index(b"bext") == order.index(b"fmt ") + 1


def test_patch_preserves_other_bext_fields(wav_factory):
    original = make_bext("OLD", originator="MY_ORIGINATOR")
    path = wav_factory(bext=original)
    wav_meta.patch_metadata(path, "NEW_NAME", {"SW_UCS": "NEW_NAME"})
    raw = _chunks(path)[b"bext"]
    assert raw[0:256].split(b"\x00", 1)[0] == b"NEW_NAME"
    assert raw[256:288].rstrip(b"\x00") == b"MY_ORIGINATOR"
    assert raw[288:320].rstrip(b"\x00") == b"REFERENCE"
    assert raw[320:330] == b"2026-01-02"
    assert raw[330:338] == b"03:04:05"


def test_patch_preserves_existing_ixml_tags(wav_factory):
    path = wav_factory(ixml=make_ixml("  <TAPE>001</TAPE>\n  <SW_UCS>OLD</SW_UCS>\n"))
    wav_meta.patch_metadata(path, "NEW", {"SW_UCS": "NEW", "SW_CATEGORY": "AMBMisc"})
    ixml = wav_meta.read_metadata(path)["ixml"]
    assert ixml["PROJECT"] == "OLD_PROJECT"
    assert ixml["TAPE"] == "001"
    assert ixml["SW_UCS"] == "NEW"
    assert ixml["SW_CATEGORY"] == "AMBMisc"


def test_ixml_replacement_is_escaped(wav_factory):
    path = wav_factory()
    wav_meta.patch_metadata(path, "A&B", {"SW_UCS": "A&B <C>"})
    assert wav_meta.read_metadata(path)["ixml"]["SW_UCS"] == "A&B <C>"


def test_restore_returns_byte_identical_file(wav_factory):
    path = wav_factory(bext=make_bext("OLD"), ixml=make_ixml())
    original = path.read_bytes()
    result = wav_meta.patch_metadata(
        path, "NEW", {"SW_UCS": "NEW", "SW_CATEGORY": "AMBMisc"}
    )
    assert path.read_bytes() != original
    wav_meta.restore_chunks(path, result["original_bext"], result["original_ixml"])
    assert path.read_bytes() == original


def test_restore_removes_chunks_that_did_not_exist(wav_factory):
    path = wav_factory()
    original = path.read_bytes()
    result = wav_meta.patch_metadata(path, "NEW", {"SW_UCS": "NEW"})
    assert wav_meta.read_metadata(path)["has_bext"] is True
    wav_meta.restore_chunks(path, result["original_bext"], result["original_ixml"])
    assert path.read_bytes() == original
    assert wav_meta.read_metadata(path)["has_bext"] is False


def test_overlong_description_skips_bext_but_writes_ixml(wav_factory):
    path = wav_factory()
    description = "X" * 300
    result = wav_meta.patch_metadata(path, description, {"SW_UCS": description})
    assert result["bext_written"] is False
    assert result["ixml_written"] is True
    assert any("255" in message for message in result["warnings"])
    meta = wav_meta.read_metadata(path)
    assert meta["has_bext"] is False
    assert meta["ixml"]["SW_UCS"] == description


def test_read_rejects_rf64(wav_factory):
    path = wav_factory()
    raw = bytearray(path.read_bytes())
    raw[0:4] = b"RF64"
    path.write_bytes(bytes(raw))
    with pytest.raises(wav_meta.WavMetaError):
        wav_meta.read_metadata(path)


def test_read_rejects_truncated_chunk(wav_factory):
    path = wav_factory()
    raw = bytearray(path.read_bytes())
    struct.pack_into("<I", raw, 16, 999999)
    path.write_bytes(bytes(raw))
    with pytest.raises(wav_meta.WavMetaError):
        wav_meta.read_metadata(path)


def test_read_rejects_non_riff(wav_factory):
    path = wav_factory()
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00")
    with pytest.raises(wav_meta.WavMetaError):
        wav_meta.read_metadata(path)
