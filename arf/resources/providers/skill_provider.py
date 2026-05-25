"""SkillProvider — scan skills/*.yaml for skill definitions."""
from pathlib import Path
import yaml
from arf.core.config_base import SkillConfig


class SkillProvider:
    """Scans skills/ directory for *.yaml files. Each file = one skill.

    Splits skills into kernel (activation: kernel, readonly framework skills)
    and dynamic (user-created skills, invalidated on filesystem change).
    """

    def __init__(self, skills_dir: str | Path):
        self._dir = Path(skills_dir)
        self._kernel: dict[str, SkillConfig] = {}
        self._dynamic: dict[str, SkillConfig] = {}
        self._loaded = False

    def list_kernel(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._kernel.values())

    def list_dynamic(self) -> list[SkillConfig]:
        if not self._loaded:
            self._load()
        return list(self._dynamic.values())

    def list(self) -> list[SkillConfig]:
        return self.list_kernel() + self.list_dynamic()

    def invalidate_dynamic(self) -> None:
        self._dynamic.clear()
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        self._dynamic.clear()
        if not self._dir.exists():
            return
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not raw or "name" not in raw:
                continue
            cfg = SkillConfig(**raw)
            activation = getattr(cfg, "activation", "discoverable")
            if activation == "kernel":
                if cfg.name not in self._kernel:
                    self._kernel[cfg.name] = cfg
            else:
                self._dynamic[cfg.name] = cfg
