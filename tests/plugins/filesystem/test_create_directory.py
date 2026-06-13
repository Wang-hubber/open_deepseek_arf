"""Tests for create_directory tool."""
import asyncio
import pytest
from arf.plugins.filesystem.tools.create_directory.function import execute


def test_create_new_directory(tmp_path):
    d = tmp_path / "newdir"
    result = asyncio.run(execute(path=str(d)))
    assert result["ok"] is True
    assert result["created"] is True
    assert d.is_dir()


def test_create_nested(tmp_path):
    d = tmp_path / "a" / "b" / "c"
    result = asyncio.run(execute(path=str(d)))
    assert result["ok"] is True
    assert d.is_dir()


def test_already_exists_silent(tmp_path):
    d = tmp_path / "existing"
    d.mkdir()
    result = asyncio.run(execute(path=str(d)))
    assert result["ok"] is True
    assert result["created"] is False
