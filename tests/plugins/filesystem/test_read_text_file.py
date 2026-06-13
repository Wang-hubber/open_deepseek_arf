"""Tests for read_text_file tool."""
import asyncio

from arf.plugins.filesystem.tools.read_text_file.function import execute


def test_read_entire_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\n")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is True
    assert result["lines_read"] == 3
    assert result["total_lines"] == 3
    assert "line1" in result["content"]
    assert "line3" in result["content"]


def test_head(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    result = asyncio.run(execute(path=str(f), head=2))
    assert result["ok"] is True
    assert result["lines_read"] == 2
    assert "a" in result["content"]
    assert "c" not in result["content"]


def test_tail(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    result = asyncio.run(execute(path=str(f), tail=2))
    assert result["ok"] is True
    assert result["lines_read"] == 2
    assert "d" in result["content"]
    assert "e" in result["content"]


def test_head_tail_mutually_exclusive(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("a\nb\n")
    result = asyncio.run(execute(path=str(f), head=1, tail=1))
    assert result["ok"] is False


def test_binary_file_returns_error(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02\xff")
    result = asyncio.run(execute(path=str(f)))
    assert result["ok"] is False


def test_not_a_file(tmp_path):
    result = asyncio.run(execute(path=str(tmp_path / "nonexistent.txt")))
    assert result["ok"] is False
