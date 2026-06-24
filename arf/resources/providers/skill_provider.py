"""SkillProvider — scan skills/*.yaml for skill definitions."""
import logging
from pathlib import Path
import yaml
from arf.core.config_base import SkillConfig
from arf.resources.cache import ResourceCache

logger = logging.getLogger(__name__)


class SkillProvider:
    """Scans skills/ directory for *.yaml files. Each file = one skill.

    All skills are loaded uniformly — no kernel/dynamic split.
    FileWatcher triggers full reload on filesystem change.
    """

    def __init__(self, skills_dir: str | Path):
        self._dir = Path(skills_dir)
        self._cache = ResourceCache()
        self._loaded = False

    def list(self) -> list[SkillConfig]:
        """Return all loaded skills."""
        if not self._loaded:
            self._load()
        return self._cache.get_all()

    def invalidate_dynamic(self) -> None:
        """Clear cache and reread on next list()."""
        self._cache.invalidate()
        self._loaded = False

    def _load(self) -> None:
        self._cache.invalidate()
        if not self._dir.exists():
            self._loaded = True
            return
        # Format 1: subdirectories containing skill.yaml (project skills)
        for skill_dir in sorted(self._dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            yaml_path = skill_dir / "skill.yaml"
            if not yaml_path.exists():
                continue
            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not raw or "name" not in raw:
                    continue
                cfg = SkillConfig(**raw)
                self._cache.put(cfg.name, cfg)
            except Exception as e:
                logger.warning("Skipping %s: %s", yaml_path, e)
        # Format 2: flat *.yaml files (plugin skills, inline)
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not raw or "name" not in raw:
                    continue
                if self._cache.has(raw["name"]):
                    continue  # subdirectory format takes precedence
                cfg = SkillConfig(**raw)
                self._cache.put(cfg.name, cfg)
            except Exception as e:
                logger.warning("Skipping %s: %s", yaml_path, e)
        self._loaded = True
