"""Tests for search_files tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.search_files.function import execute


def test_search_py_files(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("")

    result = asyncio.run(execute(path=str(tmp_path), pattern="*.py"))
    assert result["ok"] is True
    assert result["count"] == 1  # only top-level


def test_search_recursive(tmp_path):
    (tmp_path / "a.py").write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("")

    result = asyncio.run(execute(path=str(tmp_path), pattern="**/*.py"))
    assert result["ok"] is True
    assert result["count"] == 2


def test_no_matches(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path), pattern="*.xyz"))
    assert result["ok"] is True
    assert result["count"] == 0


def test_exclude_pattern(tmp_path):
    (tmp_path / "keep.py").write_text("")
    (tmp_path / "test_skip.py").write_text("")

    result = asyncio.run(execute(path=str(tmp_path), pattern="*.py", excludePatterns=["test_*.py"]))
    assert result["ok"] is True
    names = [p for p in result["matches"]]
    assert any("keep.py" in p for p in names)
    assert not any("test_skip.py" in p for p in names)


def test_not_a_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("")
    result = asyncio.run(execute(path=str(f), pattern="*"))
    assert result["ok"] is False
