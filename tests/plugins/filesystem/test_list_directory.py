"""Tests for list_directory and list_directory_with_sizes tools."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.list_directory.function import execute


def test_list_directory(tmp_path):
    (tmp_path / "file.txt").write_text("")
    (tmp_path / "subdir").mkdir()
    result = asyncio.run(execute(path=str(tmp_path)))
    assert result["ok"] is True
    assert result["count"] == 2
    assert "[FILE] file.txt" in result["entries"]
    assert "[DIR] subdir" in result["entries"]


def test_empty_directory(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path)))
    assert result["ok"] is True
    assert result["count"] == 0


def test_not_a_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is False


def test_list_with_sizes(tmp_path):
    from arf.plugins.filesystem.tools.list_directory_with_sizes.function import execute as exec_sizes

    f = tmp_path / "data.txt"
    f.write_text("hello world")
    (tmp_path / "sub").mkdir()
    result = asyncio.run(exec_sizes(path=str(tmp_path), sortBy="size"))
    assert result["ok"] is True
    assert result["total_files"] == 1
    assert result["total_dirs"] == 1
    assert result["combined_size"] == 11


def test_list_with_sizes_sort_by_name(tmp_path):
    from arf.plugins.filesystem.tools.list_directory_with_sizes.function import execute as exec_sizes

    (tmp_path / "b.txt").write_text("")
    (tmp_path / "a.txt").write_text("")
    result = asyncio.run(exec_sizes(path=str(tmp_path), sortBy="name"))
    assert result["ok"] is True
