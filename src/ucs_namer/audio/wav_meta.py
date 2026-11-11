"""Lossless BWF (bext) and iXML metadata writing for RIFF/WAVE files.

Only the bext and iXML chunks are replaced; every other chunk is copied
byte-for-byte, including odd-size padding. Writes go to a same-directory temp
file, are read back, and only then replace the original atomically.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from ..naming import ascii_text

MAX_BEXT_DESCRIPTION = 255
COPY_BLOCK = 1024 * 1024


class WavMetaError(RuntimeError):
    """A WAV file cannot be safely read or written."""


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: bytes
    offset: int
    size: int
    data_offset: int
    total: int


def _read_exact(handle, count: int) -> bytes:
    data = handle.read(count)
    if len(data) != count:
        raise WavMetaError("文件在 chunk 声明长度处被截断")
    return data


def _scan(path: Path) -> list[Chunk]:
    with path.open("rb") as handle:
        header = _read_exact(handle, 12)
        if header[:4] == b"RF64":
            raise WavMetaError("RF64 文件只读，不写入元数据")
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise WavMetaError("不是 RIFF/WAVE 文件")
        file_size = path.stat().st_size
        offset = 12
        chunks: list[Chunk] = []
        while offset + 8 <= file_size:
            handle.seek(offset)
            chunk_id = _read_exact(handle, 4)
            (size,) = struct.unpack("<I", _read_exact(handle, 4))
            data_offset = offset + 8
            total = 8 + size + (size & 1)
            if data_offset + size > file_size:
                raise WavMetaError("chunk 声明长度超过文件实际长度，疑似截断")
            chunks.append(Chunk(chunk_id, offset, size, data_offset, total))
            offset += total
        return chunks


def _find(chunks: list[Chunk], name: str) -> Chunk | None:
    target = name.lower().encode("ascii")
    for chunk in chunks:
        if chunk.chunk_id.lower() == target:
            return chunk
    return None


def _read_chunk(path: Path, chunk: Chunk, limit: int | None = None) -> bytes:
    size = chunk.size if limit is None else min(chunk.size, limit)
    with path.open("rb") as handle:
        handle.seek(chunk.data_offset)
        return _read_exact(handle, size)


def _read_raw(path: Path, chunk: Chunk) -> bytes:
    with path.open("rb") as handle:
        handle.seek(chunk.offset)
        return _read_exact(handle, chunk.total)


def _parse_ixml(raw: bytes) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        root = ET.fromstring(raw.decode("utf-8", "replace"))
    except ET.ParseError as error:
        raise WavMetaError(f"iXML 解析失败：{error}") from error
    values: dict[str, str] = {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if element.text is not None:
            values[tag] = element.text
    return values


def _bext_description(raw: bytes) -> str:
    return raw[:256].split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def _bext_text(data: bytes, start: int, size: int) -> str:
    return data[start : start + size].split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def _bext_number(data: bytes, start: int, fmt: str, unset=(0,)) -> str:
    (value,) = struct.unpack_from(fmt, data, start)
    return "" if value in unset else str(value)


def _parse_bext(raw: bytes) -> dict[str, str]:
    """Decode the standard 602-byte bext body plus its coding history."""
    data = bytes(raw)
    if len(data) < 602:
        data = data.ljust(602, b"\x00")
    umid = data[348:412]
    return {
        "Description": _bext_text(data, 0, 256),
        "Originator": _bext_text(data, 256, 32),
        "OriginatorReference": _bext_text(data, 288, 32),
        "OriginationDate": _bext_text(data, 320, 10),
        "OriginationTime": _bext_text(data, 330, 8),
        "TimeReference": _bext_number(data, 338, "<Q"),
        "Version": _bext_number(data, 346, "<H"),
        "UMID": umid.hex().upper() if any(umid) else "",
        "LoudnessValue": _bext_number(data, 412, "<h", (0, 32767)),
        "LoudnessRange": _bext_number(data, 414, "<h", (0, 32767)),
        "MaxTruePeakLevel": _bext_number(data, 416, "<h", (0, 32767)),
        "MaxMomentaryLoudness": _bext_number(data, 418, "<h", (0, 32767)),
        "MaxShortTermLoudness": _bext_number(data, 420, "<h", (0, 32767)),
        "CodingHistory": data[602:].split(b"\x00", 1)[0].decode("ascii", "replace").strip(),
    }


def read_metadata(path: Path) -> dict:
    """Read bext/iXML metadata without decoding audio."""
    path = Path(path)
    chunks = _scan(path)
    bext = _find(chunks, "bext")
    ixml = _find(chunks, "iXML")
    result = {
        "ok": True,
        "has_bext": bext is not None,
        "has_ixml": ixml is not None,
        "description": "",
        "bext": {},
        "ixml": {},
        "error": "",
    }
    if bext is not None:
        raw = _read_chunk(path, bext)
        result["description"] = _bext_description(raw)
        result["bext"] = _parse_bext(raw)
    if ixml is not None:
        try:
            result["ixml"] = _parse_ixml(_read_chunk(path, ixml))
        except WavMetaError as error:
            result["error"] = str(error)
    return result


def _new_bext(description: str) -> tuple[bytes | None, str]:
    text = ascii_text(description)
    if not text:
        return None, "bext.description 为空或无法转为 ASCII"
    encoded = text.encode("ascii", "ignore")
    if len(encoded) > MAX_BEXT_DESCRIPTION:
        return None, f"bext.description 超过 {MAX_BEXT_DESCRIPTION} 字节，已跳过 bext，完整名称写入 iXML"
    data = bytearray(602)
    data[0:256] = encoded.ljust(256, b"\x00")[:256]
    today = date.today().isoformat().encode("ascii")
    now = datetime.now().strftime("%H:%M:%S").encode("ascii")
    data[320:330] = today.ljust(10, b"\x00")[:10]
    data[330:338] = now.ljust(8, b"\x00")[:8]
    struct.pack_into("<H", data, 346, 1)  # BWF version 1
    return bytes(data), ""


def _update_bext(existing: bytes, description: str) -> tuple[bytes | None, str]:
    text = ascii_text(description)
    encoded = text.encode("ascii", "ignore")
    if len(encoded) > MAX_BEXT_DESCRIPTION:
        return None, f"bext.description 超过 {MAX_BEXT_DESCRIPTION} 字节，已跳过 bext，完整名称写入 iXML"
    data = bytearray(existing)
    if len(data) < 602:
        data.extend(b"\x00" * (602 - len(data)))
    data[0:256] = encoded.ljust(256, b"\x00")[:256]
    return bytes(data), ""


_TAG_CACHE: dict[str, re.Pattern] = {}


def _tag_pattern(tag: str) -> re.Pattern:
    pattern = _TAG_CACHE.get(tag)
    if pattern is None:
        safe = re.escape(tag)
        pattern = re.compile(
            rf"<{safe}(?:\s[^>]*)?>.*?</{safe}>|<{safe}\s*/>", re.DOTALL
        )
        _TAG_CACHE[tag] = pattern
    return pattern


def _set_ixml_text(text: str, values: dict[str, str]) -> str:
    if not text.strip():
        body = "\n".join(f"  <{tag}>{escape(value)}</{tag}>" for tag, value in values.items())
        text = '<?xml version="1.0" encoding="UTF-8"?>\n<BWFXML>\n' + body + "\n</BWFXML>\n"
    else:
        for tag, value in values.items():
            replacement = f"<{tag}>{escape(str(value))}</{tag}>"
            pattern = _tag_pattern(tag)
            if pattern.search(text):
                text = pattern.sub(lambda _match, value=replacement: value, text, count=1)
            else:
                closing = text.rfind("</BWFXML>")
                if closing < 0:
                    raise WavMetaError("iXML 缺少 BWFXML 根节点，未写入")
                text = text[:closing] + f"  {replacement}\n" + text[closing:]
    try:
        ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as error:
        raise WavMetaError(f"写入后的 iXML 无法解析：{error}") from error
    return text


def _copy_raw(source, target, offset: int, size: int) -> None:
    source.seek(offset)
    remaining = size
    while remaining > 0:
        block = source.read(min(COPY_BLOCK, remaining))
        if not block:
            raise WavMetaError("复制时文件被截断")
        target.write(block)
        remaining -= len(block)


def _write_chunk(target, chunk_id: bytes, data: bytes) -> int:
    target.write(chunk_id)
    target.write(struct.pack("<I", len(data)))
    target.write(data)
    if len(data) & 1:
        target.write(b"\x00")
    return 8 + len(data) + (len(data) & 1)


def _rewrite(
    path: Path,
    replacements: dict[str, tuple[bytes | None, bytes | None]],
    insert_after_fmt: list[tuple[bytes, bytes]],
    append: list[tuple[bytes, bytes]],
    verify=None,
) -> dict:
    """Rewrite chunks. replacements maps lower id -> (new id, new data or None)."""
    chunks = _scan(path)
    before = {name: _read_raw(path, _find(chunks, name)) for name in ("bext", "iXML") if _find(chunks, name)}
    fmt_seen = False
    wrote_bext = False
    wrote_ixml = False
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb", delete=False, dir=path.parent, prefix=path.stem + ".", suffix=".tmp"
        ) as handle:
            temp_name = handle.name
            handle.write(b"RIFF\x00\x00\x00\x00WAVE")
            total = 12
            with path.open("rb") as source:
                for chunk in chunks:
                    lower = chunk.chunk_id.lower().decode("ascii", "replace")
                    if lower in replacements:
                        new_id, new_data = replacements[lower]
                        if new_data is None:
                            continue
                        total += _write_chunk(handle, new_id or chunk.chunk_id, new_data)
                        if lower == "bext":
                            wrote_bext = True
                        if lower == "ixml":
                            wrote_ixml = True
                    else:
                        _copy_raw(source, handle, chunk.offset, chunk.total)
                        total += chunk.total
                    if lower == "fmt ":
                        fmt_seen = True
                        for chunk_id, data in insert_after_fmt:
                            total += _write_chunk(handle, chunk_id, data)
                            wrote_bext = wrote_bext or chunk_id.lower() == b"bext"
                if insert_after_fmt and not fmt_seen:
                    raise WavMetaError("WAVE 缺少 fmt chunk，无法插入 bext")
                for chunk_id, data in append:
                    total += _write_chunk(handle, chunk_id, data)
                    wrote_ixml = wrote_ixml or chunk_id.lower() == b"ixml"
            if total - 8 > 0xFFFFFFFF:
                raise WavMetaError("写入后文件超过 RIFF 4GB 上限")
            handle.seek(4)
            handle.write(struct.pack("<I", total - 8))
            handle.flush()
            os.fsync(handle.fileno())

        # Re-read the temp file before replacing the original.
        temp_path = Path(temp_name)
        _scan(temp_path)
        if verify is not None:
            verify(temp_path)
        shutil.copystat(path, temp_path)
        os.replace(temp_path, path)
        return {
            "ok": True,
            "bext_written": wrote_bext,
            "ixml_written": wrote_ixml,
            "original_bext": before.get("bext"),
            "original_ixml": before.get("iXML"),
        }
    except Exception:
        if temp_name:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
        raise


def patch_metadata(path: Path, description: str, values: dict[str, str]) -> dict:
    """Write bext.description and iXML SW_* fields; return original chunks."""
    path = Path(path)
    chunks = _scan(path)
    bext = _find(chunks, "bext")
    ixml = _find(chunks, "iXML")
    warnings: list[str] = []
    replacements: dict[str, tuple[bytes | None, bytes | None]] = {}
    insert_after_fmt: list[tuple[bytes, bytes]] = []
    append: list[tuple[bytes, bytes]] = []

    if bext is None:
        bext_data, warning = _new_bext(description)
        if bext_data is not None:
            insert_after_fmt.append((b"bext", bext_data))
    else:
        bext_data, warning = _update_bext(_read_chunk(path, bext), description)
        if bext_data is not None:
            replacements["bext"] = (b"bext", bext_data)
    if warning:
        warnings.append(warning)

    if ixml is None:
        ixml_text = _set_ixml_text("", values)
        append.append((b"iXML", ixml_text.encode("utf-8")))
    else:
        existing = _read_chunk(path, ixml).decode("utf-8", "replace")
        ixml_text = _set_ixml_text(existing, values)
        replacements["ixml"] = (b"iXML", ixml_text.encode("utf-8"))
    expected_description = ascii_text(description)[:MAX_BEXT_DESCRIPTION] if bext_data else None

    def verify(candidate: Path) -> None:
        meta = read_metadata(candidate)
        if expected_description and meta.get("description") != expected_description:
            raise WavMetaError("回读 bext.description 与预期不一致")
        for tag, value in values.items():
            if meta.get("ixml", {}).get(tag) != value:
                raise WavMetaError(f"回读 iXML {tag} 与预期不一致")

    result = _rewrite(path, replacements, insert_after_fmt, append, verify=verify)
    result["warnings"] = warnings
    return result


def restore_chunks(path: Path, bext_raw: bytes | None, ixml_raw: bytes | None) -> dict:
    """Restore original raw bext/iXML chunks (or remove them) after an undo."""
    path = Path(path)
    chunks = _scan(path)
    replacements: dict[str, tuple[bytes | None, bytes | None]] = {}
    insert_after_fmt: list[tuple[bytes, bytes]] = []
    append: list[tuple[bytes, bytes]] = []

    current_bext = _find(chunks, "bext")
    current_ixml = _find(chunks, "iXML")
    if bext_raw is None:
        if current_bext is not None:
            replacements["bext"] = (b"bext", None)
    else:
        data = bext_raw[8 : 8 + struct.unpack("<I", bext_raw[4:8])[0]]
        if current_bext is None:
            insert_after_fmt.append((b"bext", data))
        else:
            replacements["bext"] = (b"bext", data)
    if ixml_raw is None:
        if current_ixml is not None:
            replacements["ixml"] = (b"iXML", None)
    else:
        data = ixml_raw[8 : 8 + struct.unpack("<I", ixml_raw[4:8])[0]]
        if current_ixml is None:
            append.append((b"iXML", data))
        else:
            replacements["ixml"] = (b"iXML", data)
    return _rewrite(path, replacements, insert_after_fmt, append)
