"""Tests for delete_file tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.delete_file.function import execute


def test_delete_file(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("data")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is True
    assert not f.exists()


def test_delete_empty_directory(tmp_path):
    d = tmp_path / "emptydir"
    d.mkdir()
    result = asyncio.run(execute(path=str(d)))
    assert result["ok"] is True
    assert not d.exists()


def test_delete_non_empty_without_recursive_fails(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "file.txt").write_text("")
    result = asyncio.run(execute(path=str(d)))
    assert result["ok"] is False


def test_delete_non_empty_recursive(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "file.txt").write_text("")
    result = asyncio.run(execute(path=str(d), recursive=True))
    assert result["ok"] is True
    assert not d.exists()


def test_delete_nonexistent(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path / "missing")))
    assert result["ok"] is False
