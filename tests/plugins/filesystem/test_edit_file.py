"""Tests for edit_file tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.edit_file.function import execute


def test_simple_replacement(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\n")
    result = asyncio.run(
        execute(path=str(f), edits=[{"oldText": "line2", "newText": "modified"}])
    )
    assert result["ok"] is True
    assert result["applied"] is True
    assert "modified" in f.read_text()
    assert "line2" not in f.read_text()


def test_dry_run(tmp_path):
    f = tmp_path / "test.txt"
    original = "line1\nline2\nline3\n"
    f.write_text(original)
    result = asyncio.run(
        execute(
            path=str(f),
            edits=[{"oldText": "line2", "newText": "changed"}],
            dryRun=True,
        )
    )
    assert result["ok"] is True
    assert result["applied"] is False
    assert "diff" in result
    assert f.read_text() == original  # unchanged


def test_multiple_edits(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("a\nb\nc\n")
    result = asyncio.run(
        execute(
            path=str(f),
            edits=[
                {"oldText": "a", "newText": "first"},
                {"oldText": "c", "newText": "third"},
            ],
        )
    )
    assert result["ok"] is True
    content = f.read_text()
    assert "first" in content
    assert "third" in content


def test_whitespace_flexible_match(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("  line1\n    line2\n  line3\n")
    result = asyncio.run(
        execute(path=str(f), edits=[{"oldText": "line2", "newText": "modified line2"}])
    )
    assert result["ok"] is True
    content = f.read_text()
    assert "    modified line2" in content


def test_no_match_returns_error(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world\n")
    result = asyncio.run(
        execute(
            path=str(f), edits=[{"oldText": "nonexistent", "newText": "replacement"}]
        )
    )
    assert result["ok"] is False


def test_crlf_normalization(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\r\nline2\r\nline3\r\n")
    result = asyncio.run(
        execute(path=str(f), edits=[{"oldText": "line2", "newText": "changed"}])
    )
    assert result["ok"] is True
    content = f.read_text()
    assert "changed" in content
    assert "\r\n" not in content  # normalized to LF


def test_multi_line_edit(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def foo():\n    old_impl()\n    return 1\n")
    result = asyncio.run(
        execute(
            path=str(f),
            edits=[
                {
                    "oldText": "    old_impl()\n    return 1",
                    "newText": "    new_impl()\n    return 2",
                }
            ],
        )
    )
    assert result["ok"] is True
    content = f.read_text()
    assert "new_impl()" in content
    assert "old_impl()" not in content


def test_diff_output_format(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("original\n")
    result = asyncio.run(
        execute(path=str(f), edits=[{"oldText": "original", "newText": "modified"}])
    )
    assert result["ok"] is True
    assert "```" in result["diff"]
    assert "---" in result["diff"]
    assert "+++" in result["diff"]


def test_binary_file_rejected(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02")
    result = asyncio.run(
        execute(path=str(f), edits=[{"oldText": "x", "newText": "y"}])
    )
    assert result["ok"] is False
