"""Tests for read_multiple_files tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.read_multiple_files.function import execute


def test_read_multiple_success(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello")
    f2.write_text("world")
    result = asyncio.run(execute(paths=[str(f1), str(f2)]))
    assert result["ok"] is True
    assert result["total"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0


def test_mixed_valid_invalid(tmp_path):
    f1 = tmp_path / "exists.txt"
    f1.write_text("data")
    result = asyncio.run(execute(paths=[str(f1), str(tmp_path / "missing.txt")]))
    assert result["ok"] is True
    assert result["succeeded"] == 1
    assert result["failed"] == 1


def test_all_fail(tmp_path):
    result = asyncio.run(execute(paths=[str(tmp_path / "x.txt"), str(tmp_path / "y.txt")]))
    assert result["ok"] is True
    assert result["succeeded"] == 0
    assert result["failed"] == 2


def test_empty_paths():
    result = asyncio.run(execute(paths=[]))
    assert result["ok"] is False
