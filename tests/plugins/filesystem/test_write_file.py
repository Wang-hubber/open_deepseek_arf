"""Tests for write_file tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.write_file.function import execute


def test_write_new_file(tmp_path):
    f = tmp_path / "new.txt"
    result = asyncio.run(execute(path=str(f), content="hello world"))
    assert result["ok"] is True
    assert result["bytes_written"] == 11
    assert f.read_text() == "hello world"


def test_overwrite_existing(tmp_path):
    f = tmp_path / "existing.txt"
    f.write_text("old")
    result = asyncio.run(execute(path=str(f), content="new content"))
    assert result["ok"] is True
    assert f.read_text() == "new content"


def test_create_with_parent_dirs(tmp_path):
    f = tmp_path / "a" / "b" / "file.txt"
    result = asyncio.run(execute(path=str(f), content="nested"))
    assert result["ok"] is True
    assert f.read_text() == "nested"
