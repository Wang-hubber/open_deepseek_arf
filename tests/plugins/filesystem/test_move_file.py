"""Tests for move_file tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.move_file.function import execute


def test_move_file(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("data")
    dst = tmp_path / "dst.txt"
    result = asyncio.run(execute(source=str(src), destination=str(dst)))
    assert result["ok"] is True
    assert not src.exists()
    assert dst.read_text() == "data"


def test_rename_directory(tmp_path):
    src = tmp_path / "oldname"
    src.mkdir()
    dst = tmp_path / "newname"
    result = asyncio.run(execute(source=str(src), destination=str(dst)))
    assert result["ok"] is True
    assert not src.exists()
    assert dst.is_dir()


def test_destination_exists_fails(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("a")
    dst.write_text("b")
    result = asyncio.run(execute(source=str(src), destination=str(dst)))
    assert result["ok"] is False


def test_source_missing(tmp_path):
    result = asyncio.run(execute(source=str(tmp_path / "nope"), destination=str(tmp_path / "dest")))
    assert result["ok"] is False
