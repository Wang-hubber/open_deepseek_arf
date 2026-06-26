"""Phase 0: verify ARF package is importable and version is set."""


def test_import():
    from arf import __version__

    assert __version__ == "1.0.0-alpha.0"


def test_example_runs():
    """Verify the teaching example runs without error."""
    import runpy
    from pathlib import Path

    example = Path(__file__).parent.parent / "python" / "arf" / "examples" / "phase0_hello.py"
    result = runpy.run_path(str(example))
    assert result is not None
