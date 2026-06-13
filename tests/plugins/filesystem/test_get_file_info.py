"""Tests for get_file_info tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.get_file_info.function import execute


def test_file_info(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is True
    assert result["size"] == 5
    assert result["isFile"] is True
    assert result["isDirectory"] is False
    assert "permissions" in result


def test_directory_info(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    result = asyncio.run(execute(path=str(d)))
    assert result["ok"] is True
    assert result["isDirectory"] is True
    assert result["isFile"] is False


def test_nonexistent(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path / "nope")))
    assert result["ok"] is False
