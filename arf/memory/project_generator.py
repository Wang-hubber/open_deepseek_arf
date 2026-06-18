# arf/memory/project_generator.py
"""ProjectMemoryGenerator — scan codebase + LLM → project.md."""
import logging
from pathlib import Path

logger = logging.getLogger("arf.memory.generator")

_SCAN_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "data", ".claude"}


class ProjectMemoryGenerator:
    """Scan the project root and generate project.md via LLM."""

    def __init__(self, project_root: str, memory_index) -> None:
        self._root = Path(project_root)
        self._index = memory_index

    def needs_generation(self) -> bool:
        """Check whether any project memory exists (group first, then individual)."""
        if self._index._group_dir:
            return not (self._index._group_dir / "project.md").exists()
        return not self._index.has_project_file()

    async def generate(self, call_model) -> str:
        """Scan codebase → build prompt → LLM → return markdown."""
        context = self._scan()
        prompt = self._build_prompt(context)
        resp = await call_model(
            [{"role": "user", "content": prompt}],
            model_name="",
        )
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        # Write to group when shared memory dir is configured, else individual
        if self._index._group_dir:
            self._index.save_group_project(content)
        else:
            self._index.save_project(content)
        return content

    def _scan(self) -> dict:
        return {
            "project_name": self._root.resolve().name,
            "tree": self._tree(),
            "readme": self._read_readme(),
            "deps": self._read_deps(),
        }

    def _tree(self) -> str:
        lines = []
        for d in sorted(self._root.iterdir()):
            if d.name in _SCAN_IGNORE or d.name.startswith("."):
                continue
            if d.is_dir():
                lines.append(f"{d.name}/")
                for sub in sorted(d.iterdir()):
                    if sub.is_dir():
                        lines.append(f"  {sub.name}/")
                        count = min(3, len(list(sub.iterdir())))
                        for f2 in sorted(sub.iterdir())[:count]:
                            if f2.is_file():
                                lines.append(f"    {f2.name}")
            elif d.is_file():
                lines.append(d.name)
        return "\n".join(lines[:100])

    def _read_readme(self) -> str:
        for name in ("README.md", "README.zh-CN.md", "CLAUDE.md"):
            p = self._root / name
            if p.exists():
                return p.read_text(encoding="utf-8")[:2000]
        return ""

    def _read_deps(self) -> str:
        for name in ("pyproject.toml", "package.json", "Cargo.toml"):
            p = self._root / name
            if p.exists():
                return p.read_text(encoding="utf-8")[:2000]
        return ""

    def _build_prompt(self, context: dict) -> str:
        return f"""Generate a project memory file (Markdown) for:
{context['project_name']}

## Directory Tree
{context['tree']}

## README
{context['readme']}

## Dependencies
{context['deps']}

Write in this format:
# Project Overview
... (one paragraph)

# Architecture
... (key directories and their roles)

# Key Conventions
- ...

# Dependencies
- ...

Keep it concise. Focus on what an AI agent needs to know to work in this codebase."""
