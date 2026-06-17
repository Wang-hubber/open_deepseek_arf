# arf/skills/skill_index.py
"""SkillIndex — scan, index, and retrieve lazy-loaded skills.

Each skill is a directory containing:
  skill.yaml — {name, description, tools_sequence?}
  skill.md  — domain knowledge body (Markdown, returned by use_skill)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("arf.skills")


@dataclass
class SkillEntry:
    name: str
    description: str
    source_dir: str  # parent directory containing skill.yaml + skill.md
    tools_sequence: list[str] = field(default_factory=list)


class SkillIndex:
    """Scan skill directories and serve skill content on demand.

    Directories scanned (in order):
      1. <project_root>/skills/
      2. <project_root>/arf/plugins/*/skills/

    Later scans override earlier entries with the same name.
    """

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root = Path(project_root)
        self._index: dict[str, SkillEntry] = {}
        self._scanned = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> None:
        """(Re)build the skill index from all source directories."""
        self._index.clear()

        # Layer 1: project-level skills/
        self._scan_dir(self._root / "skills")

        # Layer 2: plugin skills (overrides layer 1 on name conflict)
        plugins_dir = self._root / "arf" / "plugins"
        if plugins_dir.exists():
            for plugin_dir in sorted(plugins_dir.iterdir()):
                if plugin_dir.is_dir():
                    self._scan_dir(
                        plugin_dir / "skills",
                        plugin_name=plugin_dir.name,
                    )

        self._scanned = True
        logger.info("Skill index built: %d skills", len(self._index))

    def list_index(self) -> list[SkillEntry]:
        """Return all indexed skills (name + description only)."""
        if not self._scanned:
            self.scan()
        return list(self._index.values())

    def resolve(self, name: str) -> SkillEntry | None:
        """Look up a skill by name."""
        if not self._scanned:
            self.scan()
        return self._index.get(name)

    def load_body(self, name: str) -> str | None:
        """Read the full skill.md body for *name*."""
        entry = self.resolve(name)
        if entry is None:
            return None
        md_path = Path(entry.source_dir) / "skill.md"
        if not md_path.exists():
            logger.warning("Skill '%s' has no skill.md at %s", name, md_path)
            return None
        return md_path.read_text(encoding="utf-8")

    def format_index_markdown(self) -> str:
        """Format the index as a Markdown list for system-reminder."""
        if not self._scanned:
            self.scan()
        if not self._index:
            return ""

        lines = ["## Available Skills"]
        for entry in sorted(self._index.values(), key=lambda e: e.name):
            lines.append(f"- **{entry.name}**: {entry.description}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_dir(self, skills_dir: Path, plugin_name: str = "") -> None:
        """Scan a single skills directory for skill subdirectories."""
        if not skills_dir.exists() or not skills_dir.is_dir():
            return

        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            yaml_path = skill_dir / "skill.yaml"
            if not yaml_path.exists():
                logger.debug(
                    "Skipping %s: no skill.yaml", skill_dir)
                continue

            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not raw or "name" not in raw:
                    continue
                entry = SkillEntry(
                    name=raw["name"],
                    description=raw.get("description", ""),
                    source_dir=str(skill_dir),
                    tools_sequence=raw.get("tools_sequence", []),
                )
                self._index[entry.name] = entry
                logger.debug(
                    "Indexed skill '%s' from %s", entry.name, skill_dir)
            except Exception as exc:
                logger.warning(
                    "Failed to parse %s: %s", yaml_path, exc)
