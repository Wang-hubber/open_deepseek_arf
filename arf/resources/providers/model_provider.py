"""ModelProvider — scan models/*.yaml for model configs."""
import logging
from pathlib import Path
import yaml
from arf.core.config_base import ModelConfig

logger = logging.getLogger(__name__)


class ModelProvider:
    """Scans models/ directory for *.yaml files. Each file = one model config.

    Splits models into kernel (activation: kernel, readonly framework models)
    and dynamic (user-configured models, invalidated on filesystem change).
    """

    def __init__(self, models_dir: str | Path):
        self._dir = Path(models_dir)
        self._kernel: dict[str, ModelConfig] = {}
        self._dynamic: dict[str, ModelConfig] = {}
        self._loaded = False

    def list_kernel(self) -> list[ModelConfig]:
        if not self._loaded:
            self._load()
        return list(self._kernel.values())

    def list_dynamic(self) -> list[ModelConfig]:
        if not self._loaded:
            self._load()
        return list(self._dynamic.values())

    def list(self) -> list[ModelConfig]:
        return self.list_kernel() + self.list_dynamic()

    def invalidate_dynamic(self) -> None:
        self._dynamic.clear()
        self._loaded = False

    def _load(self) -> None:
        self._dynamic.clear()
        if not self._dir.exists():
            self._loaded = True
            return
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not raw or "name" not in raw:
                    continue
                cfg = ModelConfig(**raw)
                activation = cfg.activation
                if activation == "kernel":
                    if cfg.name not in self._kernel:
                        self._kernel[cfg.name] = cfg
                else:
                    self._dynamic[cfg.name] = cfg
            except Exception as e:
                logger.warning("Skipping %s: %s", yaml_path, e)
        self._loaded = True
