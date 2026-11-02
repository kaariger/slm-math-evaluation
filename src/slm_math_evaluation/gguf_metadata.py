"""Read the embedded chat template without loading GGUF tensor data."""

from __future__ import annotations

from pathlib import Path
import struct
from typing import BinaryIO


_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4,
                 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_MAX_METADATA_BYTES = 32 * 1024 * 1024


def _read(stream: BinaryIO, size: int) -> bytes:
    if size < 0 or size > _MAX_METADATA_BYTES:
        raise ValueError("GGUF metadata value is too large")
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("truncated GGUF metadata")
    return value


def _number(stream: BinaryIO, code: str) -> int:
    return struct.unpack("<" + code, _read(stream, struct.calcsize(code)))[0]


def _string(stream: BinaryIO) -> str:
    return _read(stream, _number(stream, "Q")).decode("utf-8")


def _skip_value(stream: BinaryIO, value_type: int) -> None:
    if value_type in _SCALAR_SIZES:
        _read(stream, _SCALAR_SIZES[value_type])
    elif value_type == 8:  # string
        _string(stream)
    elif value_type == 9:  # array
        element_type = _number(stream, "I")
        count = _number(stream, "Q")
        if count > _MAX_METADATA_BYTES:
            raise ValueError("GGUF metadata array is too large")
        for _ in range(count):
            _skip_value(stream, element_type)
    else:
        raise ValueError("unsupported GGUF metadata type")


def chat_template(path: Path) -> str:
    with path.open("rb") as stream:
        if _read(stream, 4) != b"GGUF":
            raise ValueError("model is not GGUF")
        version = _number(stream, "I")
        if version not in (2, 3):
            raise ValueError("unsupported GGUF version")
        _number(stream, "Q")  # tensor count; tensor data is never read.
        count = _number(stream, "Q")
        if count > 100_000:
            raise ValueError("GGUF metadata count is too large")
        for _ in range(count):
            key = _string(stream)
            value_type = _number(stream, "I")
            if key == "tokenizer.chat_template":
                if value_type != 8:
                    raise ValueError("GGUF chat template is not a string")
                return _string(stream)
            _skip_value(stream, value_type)
    raise ValueError("GGUF chat template is missing")
