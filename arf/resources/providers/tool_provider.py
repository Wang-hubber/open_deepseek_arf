"""ToolProvider — scan tools/{name}/ for tool.yaml + function.py."""
from __future__ import annotations
import importlib.util
import inspect
import logging
from pathlib import Path
import yaml
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult
from arf.resources.backends.function import FunctionBackend
from arf.resources.cache import ResourceCache

logger = logging.getLogger("arf.tools")

# Reserved param names — framework injects these, tools must not define them.
# Names starting with '_' are framework-internal (e.g. _workspace, _engine).
# session_id is the one non-underscore param the engine injects.
_RESERVED_PARAM_NAMES: frozenset[str] = frozenset({
    "session_id",
    "agent_name",
    "round",
    "turn",
    "rounds",
    "turns",
    "session_name",
})


class ToolProvider:
    """Scans tools/ directory. Each tool is a subdirectory with tool.yaml + function.py.

    All tools are loaded uniformly — no kernel/dynamic split.
    FileWatcher triggers full reload on filesystem change.
    """

    def __init__(self, tools_dir: str | Path) -> None:
        self._dir = Path(tools_dir)
        self._cache = ResourceCache()
        self._functions: dict[str, callable] = {}
        self._rollbacks: dict[str, callable] = {}
        self._backend = FunctionBackend()
        self._loaded = False

    # -- query API --

    def list(self) -> list[ToolConfig]:
        """Return all loaded tools."""
        if not self._loaded:
            self._load()
        return self._cache.get_all()

    async def list_tools(self) -> list[ToolConfig]:
        """Async alias for list()."""
        return self.list()

    async def resolve(self, name: str) -> ToolConfig | None:
        if not self._loaded:
            self._load()
        return self._cache.get(name)

    async def execute(self, name: str, params: dict) -> ToolResult:
        cfg = await self.resolve(name)
        if cfg is None:
            return ToolResult(tool_name=name, success=False, error=f"Tool '{name}' not found")
        fn = self._functions.get(name)
        rb_fn = self._rollbacks.get(name)
        if fn:
            return await self._backend.execute_with_fn(cfg, fn, params, rollback_fn=rb_fn)
        return await self._backend.execute(cfg, params)

    # -- cache management --

    def invalidate_dynamic(self) -> None:
        """Clear cache and reread on next list()."""
        self._cache.invalidate()
        self._functions.clear()
        self._rollbacks.clear()
        self._loaded = False

    # -- internal --

    def _validate_yaml_against_fn(self, cfg: ToolConfig, fn: callable, func_path: Path) -> None:
        """Warn when tool.yaml parameters don't match function.py signature.

        Engine-injected params (session_id, _engine, _state_store, etc.) are
        excluded from the comparison — the engine provides them, not the model.
        """
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            return
        yaml_params = set((cfg.parameters or {}).get("properties", {}).keys())
        fn_params = {
            name for name, p in sig.parameters.items()
            if not name.startswith("_")
            and name != "session_id"
            and name != "kwargs"
            and p.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        }
        extra_in_yaml = yaml_params - fn_params
        if extra_in_yaml:
            logger.warning(
                "tool.yaml '%s' declares params not in function signature: %s (%s)",
                cfg.name, extra_in_yaml, func_path,
            )
        extra_in_fn = fn_params - yaml_params
        if extra_in_fn:
            logger.warning(
                "function.py '%s' expects params not in tool.yaml: %s (%s)",
                cfg.name, extra_in_fn, func_path,
            )

    def _load(self) -> None:
        self._loaded = True
        self._cache.invalidate()
        self._functions.clear()
        self._rollbacks.clear()
        if not self._dir.exists():
            return
        for tool_dir in sorted(self._dir.iterdir()):
            if not tool_dir.is_dir():
                continue
            yaml_path = tool_dir / "tool.yaml"
            if not yaml_path.exists():
                continue
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            cfg = ToolConfig(**raw)
            name = cfg.name

            # Reject reserved param names — framework injection would clobber them
            tool_params = set((cfg.parameters or {}).get("properties", {}).keys())
            conflicts = {p for p in tool_params if p.startswith("_")} | (tool_params & _RESERVED_PARAM_NAMES)
            if conflicts:
                logger.error(
                    "Tool '%s' defines reserved param names (framework-injected): %s. "
                    "Rename these params to avoid conflicts.",
                    name, sorted(conflicts),
                )
                continue  # skip this tool — don't register it

            func_path = tool_dir / "function.py"
            if func_path.exists():
                spec = importlib.util.spec_from_file_location(
                    f"arf_tool_{name}", str(func_path),
                )
                if spec and spec.loader:
                    try:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "execute"):
                            self._functions[name] = mod.execute
                            self._validate_yaml_against_fn(cfg, mod.execute, func_path)
                        if hasattr(mod, "rollback"):
                            self._rollbacks[name] = mod.rollback
                    except Exception as e:
                        logger.warning("Failed to load tool '%s': %s", name, e)

            self._cache.put(name, cfg)
