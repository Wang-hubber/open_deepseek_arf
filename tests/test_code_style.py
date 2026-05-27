"""Verify code style consistency across the ARF framework."""
import ast
from pathlib import Path


ARF_DIR = Path(__file__).parent.parent / "arf"


def _py_files():
    return [p for p in ARF_DIR.rglob("*.py") if "__pycache__" not in str(p)]


class TestModuleDocstrings:
    def test_all_modules_have_docstrings(self):
        missing = []
        for p in _py_files():
            tree = ast.parse(p.read_text())
            docstring = ast.get_docstring(tree)
            if docstring is None:
                missing.append(str(p.relative_to(ARF_DIR.parent)))
        assert missing == [], f"Modules missing docstrings: {missing}"

    def test_no_bare_dict_in_signatures(self):
        """Core files should not use bare 'dict' in function signatures."""
        core_files = [
            ARF_DIR / "engine" / "graph.py",
            ARF_DIR / "agent" / "base.py",
            ARF_DIR / "engine" / "loop_strategies" / "planner.py",
        ]
        bare = []
        for p in core_files:
            if not p.exists():
                continue
            tree = ast.parse(p.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg in node.args.args:
                        if arg.annotation and isinstance(arg.annotation, ast.Name) and arg.annotation.id == "dict":
                            bare.append(f"{p.name}:{node.lineno} def {node.name}({arg.arg}: dict)")
        assert bare == [], f"Bare 'dict' type annotations should use specific types: {bare}"
