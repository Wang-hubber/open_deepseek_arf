"""SkillProvider — scan skills/*.yaml for skill definitions."""
import logging
from pathlib import Path
import yaml
from arf.core.config_base import SkillConfig
from arf.resources.cache import ResourceCache

logger = logging.getLogger(__name__)


class SkillProvider:
    """Scans skills/ directory for *.yaml files. Each file = one skill.

    Splits skills into kernel (activation: kernel, readonly framework skills)
    and dynamic (user-created skills, invalidated on filesystem change).
    """

    def __init__(self, skills_dir: str | Path):
        self._dir = Path(skills_dir)
        self._cache = ResourceCache()
        self._loaded = False

    def list_kernel(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._cache.kernel.values())

    def list_dynamic(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._cache.dynamic.values())

    def list(self) -> list[SkillConfig]:
        return self.list_kernel() + self.list_dynamic()

    def invalidate_dynamic(self) -> None:
        self._cache.invalidate_dynamic()
        self._loaded = False

    def _load(self) -> None:
        self._cache.invalidate_dynamic()
        if not self._dir.exists():
            self._loaded = True
            return
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not raw or "name" not in raw:
                    continue
                cfg = SkillConfig(**raw)
                activation = getattr(cfg, "activation", "discoverable")
                if activation == "kernel":
                    if not self._cache.has_kernel(cfg.name):
                        self._cache.kernel[cfg.name] = cfg
                else:
                    self._cache.dynamic[cfg.name] = cfg
            except Exception as e:
                logger.warning("Skipping %s: %s", yaml_path, e)
        self._loaded = True
