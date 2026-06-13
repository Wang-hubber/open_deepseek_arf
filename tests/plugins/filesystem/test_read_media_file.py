"""Tests for read_media_file tool."""
import asyncio
import base64
import pytest
from arf.plugins.filesystem.tools.read_media_file.function import execute


def test_read_png(tmp_path):
    f = tmp_path / "test.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake png data")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is True
    assert result["type"] == "image"
    assert result["mimeType"] == "image/png"
    assert "data" in result
    decoded = base64.b64decode(result["data"])
    assert decoded == b"\x89PNG\r\n\x1a\nfake png data"


def test_read_jpg(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"jpeg data")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is True
    assert result["type"] == "image"
    assert result["mimeType"] == "image/jpeg"


def test_unknown_extension_is_blob(tmp_path):
    f = tmp_path / "data.xyz"
    f.write_bytes(b"binary")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is True
    assert result["type"] == "blob"
    assert result["mimeType"] == "application/octet-stream"


def test_nonexistent_file(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path / "nope.png")))
    assert result["ok"] is False
