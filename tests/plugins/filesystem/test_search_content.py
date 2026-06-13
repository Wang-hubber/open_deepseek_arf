"""Tests for search_content tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.search_content.function import execute


def test_literal_search(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
    result = asyncio.run(execute(pattern="foo", path=str(tmp_path)))
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == "a.py"
    assert result["matches"][0]["line_number"] == 1


def test_regex_search(tmp_path):
    (tmp_path / "a.py").write_text("foo1\nfoo2\nbar\n")
    result = asyncio.run(execute(pattern=r"foo\d", path=str(tmp_path), regex=True))
    assert result["ok"] is True
    assert result["count"] == 2


def test_include_filter(tmp_path):
    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "b.txt").write_text("hello")
    result = asyncio.run(execute(pattern="hello", path=str(tmp_path), include="*.py"))
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == "a.py"


def test_exclude_patterns(tmp_path):
    (tmp_path / "keep.py").write_text("target")
    (tmp_path / "skip.py").write_text("target")
    result = asyncio.run(execute(pattern="target", path=str(tmp_path), excludePatterns=["skip.py"]))
    assert result["ok"] is True
    assert result["count"] == 1


def test_binary_skip(tmp_path):
    (tmp_path / "code.py").write_text("hello")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nhello")
    result = asyncio.run(execute(pattern="hello", path=str(tmp_path)))
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == "code.py"


def test_invalid_regex(tmp_path):
    result = asyncio.run(execute(pattern="[", path=str(tmp_path), regex=True))
    assert result["ok"] is False


def test_not_a_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("content")
    result = asyncio.run(execute(pattern="x", path=str(f)))
    assert result["ok"] is False


def test_truncation(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.py").write_text("match\n")
    result = asyncio.run(execute(pattern="match", path=str(tmp_path), maxResults=5))
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["count"] == 5


def test_no_results(tmp_path):
    result = asyncio.run(execute(pattern="nonexistent_xyz", path=str(tmp_path)))
    assert result["ok"] is True
    assert result["count"] == 0


def test_nested_directories(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("target_here")
    result = asyncio.run(execute(pattern="target_here", path=str(tmp_path)))
    assert result["ok"] is True
    assert result["count"] == 1
    assert "nested.py" in result["matches"][0]["file"]
