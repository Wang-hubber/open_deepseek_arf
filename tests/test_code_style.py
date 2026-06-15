"""Verify code style consistency across the ARF framework."""
import ast
from pathlib import Path


ARF_DIR = Path(__file__).parent.parent / "arf"


def _py_files():
    return [p for p in ARF_DIR.rglob("*.py") if "__pycache__" not in str(p)]


class TestFileIOMustSpecifyEncoding:
    def test_write_text_has_encoding(self):
        """All Path.write_text() calls must specify encoding='utf-8'.

        On Windows, Python defaults to the system locale encoding (e.g. GBK),
        which cannot encode emoji or Chinese characters. Always pass
        encoding='utf-8' explicitly.
        """
        import ast
        violations = []
        for p in ARF_DIR.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text":
                    has_encoding = any(
                        kw.arg == "encoding" for kw in node.keywords
                    )
                    if not has_encoding:
                        violations.append(f"{p.relative_to(ARF_DIR.parent)}:{node.lineno}")
        assert violations == [], (
            f"Found Path.write_text() calls missing encoding='utf-8':\n"
            + "\n".join(violations)
        )

    def test_read_text_has_encoding(self):
        """All Path.read_text() calls must specify encoding='utf-8'.

        On Windows, Python defaults to the system locale encoding (e.g. GBK),
        which cannot decode Chinese characters in source/config files.
        """
        import ast
        violations = []
        for p in ARF_DIR.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read_text":
                    has_encoding = any(
                        kw.arg == "encoding" for kw in node.keywords
                    )
                    if not has_encoding:
                        violations.append(f"{p.relative_to(ARF_DIR.parent)}:{node.lineno}")
        assert violations == [], (
            f"Found Path.read_text() calls missing encoding='utf-8':\n"
            + "\n".join(violations)
        )

    def test_open_text_mode_has_encoding(self):
        """All open() calls in text mode must specify encoding='utf-8'."""
        import ast
        violations = []
        for p in ARF_DIR.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                    args = node.args
                    has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
                    if has_encoding:
                        continue
                    # binary mode: if "b" in mode string, no encoding needed
                    mode_arg = args[1] if len(args) > 1 else None
                    is_binary = False
                    for kw in node.keywords:
                        if kw.arg == "mode" and "b" in (kw.value.value if isinstance(kw.value, ast.Constant) else ""):
                            is_binary = True
                    if mode_arg and isinstance(mode_arg, ast.Constant) and "b" in mode_arg.value:
                        is_binary = True
                    if not is_binary:
                        violations.append(f"{p.relative_to(ARF_DIR.parent)}:{node.lineno}")
        assert violations == [], (
            f"Found open() calls in text mode missing encoding='utf-8':\n"
            + "\n".join(violations)
        )


class TestModuleDocstrings:
    def test_all_modules_have_docstrings(self):
        missing = []
        for p in _py_files():
            tree = ast.parse(p.read_text(encoding="utf-8"))
            docstring = ast.get_docstring(tree)
            if docstring is None:
                missing.append(str(p.relative_to(ARF_DIR.parent)))
        assert missing == [], f"Modules missing docstrings: {missing}"

    def test_no_bare_dict_in_signatures(self):
        """Core files should not use bare 'dict' in function signatures."""
        core_files = [
            ARF_DIR / "engine" / "graph.py",
            ARF_DIR / "agent" / "base.py",
        ]
        bare = []
        for p in core_files:
            if not p.exists():
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg in node.args.args:
                        if arg.annotation and isinstance(arg.annotation, ast.Name) and arg.annotation.id == "dict":
                            bare.append(f"{p.name}:{node.lineno} def {node.name}({arg.arg}: dict)")
        assert bare == [], f"Bare 'dict' type annotations should use specific types: {bare}"
