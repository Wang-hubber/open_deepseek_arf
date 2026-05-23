"""StaticYamlToolProvider — load tools from directory of tool.yaml files."""
import yaml
import importlib.util
from pathlib import Path
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult
from arf.resources.backends.function import FunctionBackend


class StaticYamlToolProvider:
    def __init__(self, tools_dir: str | Path) -> None:
        self._dir = Path(tools_dir)
        self._tools: dict[str, ToolConfig] = {}
        self._functions: dict[str, callable] = {}
        self._backend = FunctionBackend()

    async def list_tools(self) -> list[ToolConfig]:
        if not self._tools:
            self._load_all()
        return list(self._tools.values())

    async def resolve(self, name: str) -> ToolConfig | None:
        if not self._tools:
            self._load_all()
        return self._tools.get(name)

    async def execute(self, name: str, params: dict) -> ToolResult:
        cfg = await self.resolve(name)
        if cfg is None:
            return ToolResult(tool_name=name, success=False, error=f"Tool '{name}' not found")
        fn = self._functions.get(name)
        if fn:
            return await self._backend.execute_with_fn(cfg, fn, params)
        return await self._backend.execute(cfg, params)

    def _load_all(self) -> None:
        if not self._dir.exists():
            return
        for tool_dir in self._dir.iterdir():
            if not tool_dir.is_dir():
                continue
            yaml_path = tool_dir / "tool.yaml"
            if not yaml_path.exists():
                continue
            raw = yaml.safe_load(yaml_path.read_text())
            cfg = ToolConfig(**raw)
            self._tools[cfg.name] = cfg

            func_path = tool_dir / "function.py"
            if func_path.exists():
                spec = importlib.util.spec_from_file_location(
                    f"arf_tool_{cfg.name}", str(func_path),
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "execute"):
                        self._functions[cfg.name] = mod.execute
