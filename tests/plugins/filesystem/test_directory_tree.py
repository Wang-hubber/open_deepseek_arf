"""Tests for directory_tree tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.directory_tree.function import execute


def test_tree_structure(tmp_path):
    (tmp_path / "file.txt").write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("")

    result = asyncio.run(execute(path=str(tmp_path)))
    assert result["ok"] is True
    tree = result["tree"]

    dirs = [e for e in tree if e["type"] == "directory"]
    files = [e for e in tree if e["type"] == "file"]
    assert len(files) == 1
    assert files[0]["name"] == "file.txt"
    assert "children" not in files[0]
    assert len(dirs) == 1
    assert dirs[0]["name"] == "sub"
    assert len(dirs[0]["children"]) == 1


def test_tree_exclude(tmp_path):
    (tmp_path / "keep.txt").write_text("")
    (tmp_path / "skip.log").write_text("")

    result = asyncio.run(execute(path=str(tmp_path), excludePatterns=["*.log"]))
    assert result["ok"] is True
    names = [e["name"] for e in result["tree"]]
    assert "keep.txt" in names
    assert "skip.log" not in names


def test_empty_directory(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path)))
    assert result["ok"] is True
    assert result["tree"] == []


def test_not_a_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is False
