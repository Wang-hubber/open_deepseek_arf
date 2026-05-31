"""ToolProvider — scan tools/{name}/ for tool.yaml + function.py."""
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



class ToolProvider:
    """Scans tools/ directory. Each tool is a subdirectory with tool.yaml + function.py.

    Splits tools into kernel (activation: kernel, readonly framework tools)
    and dynamic (user-created tools, invalidated on filesystem change).
    """

    def __init__(self, tools_dir: str | Path) -> None:
        self._dir = Path(tools_dir)
        self._cache = ResourceCache()
        self._tools: dict[str, ToolConfig] = {}  # backward-compat combined view
        self._functions: dict[str, callable] = {}  # backward-compat combined view
        self._kernel_functions: dict[str, callable] = {}
        self._kernel_rollbacks: dict[str, callable] = {}
        self._rollbacks: dict[str, callable] = {}
        self._backend = FunctionBackend()
        self._loaded = False

    # -- query API --

    def list_kernel(self) -> list[ToolConfig]:
        if not self._loaded:
            self._load()
        return list(self._cache.kernel.values())

    def list_dynamic(self) -> list[ToolConfig]:
        if not self._loaded:
            self._load()
        return list(self._cache.dynamic.values())

    async def list_tools(self) -> list[ToolConfig]:
        """Backward-compat alias for existing callers."""
        if not self._loaded:
            self._load()
        return list(self._tools.values())

    async def resolve(self, name: str) -> ToolConfig | None:
        if not self._loaded:
            self._load()
        return self._tools.get(name)

    async def execute(self, name: str, params: dict) -> ToolResult:
        cfg = await self.resolve(name)
        if cfg is None:
            return ToolResult(tool_name=name, success=False, error=f"Tool '{name}' not found")
        fn = self._functions.get(name) or self._kernel_functions.get(name)
        rb_fn = self._rollbacks.get(name) or self._kernel_rollbacks.get(name)
        if fn:
            return await self._backend.execute_with_fn(cfg, fn, params, rollback_fn=rb_fn)
        return await self._backend.execute(cfg, params)

    # -- cache management --

    def invalidate_dynamic(self) -> None:
        """Clear dynamic cache and dynamic function bindings. Kernel untouched."""
        self._cache.invalidate_dynamic()
        # Rebuild backward-compat combined view (kernel only now)
        self._tools = dict(self._cache.kernel)
        # Keep only kernel functions
        kernel_only_names = set(self._kernel_functions.keys())
        for name in list(self._functions.keys()):
            if name not in kernel_only_names:
                del self._functions[name]
        for name in list(self._rollbacks.keys()):
            if name not in kernel_only_names:
                del self._rollbacks[name]
        self._loaded = False

    # -- internal --

    def _validate_yaml_against_fn(self, cfg: ToolConfig, fn: callable, func_path: Path) -> None:
        """Warn when tool.yaml parameters don't match function.py signature."""
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            return
        yaml_params = set((cfg.parameters or {}).get("properties", {}).keys())
        fn_params = {name for name in sig.parameters if not name.startswith("_")}
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
        self._cache.invalidate_dynamic()
        self._functions.clear()
        if not self._dir.exists():
            self._tools = dict(self._cache.kernel)
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
            activation = raw.get("activation", "discoverable")

            func_path = tool_dir / "function.py"
            fn = None
            rb_fn = None
            if func_path.exists():
                spec = importlib.util.spec_from_file_location(
                    f"arf_tool_{name}", str(func_path),
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "execute"):
                        fn = mod.execute
                    if hasattr(mod, "rollback"):
                        rb_fn = mod.rollback

            if activation == "kernel":
                if not self._cache.has_kernel(name):
                    self._cache.kernel[name] = cfg
                    if fn:
                        self._validate_yaml_against_fn(cfg, fn, func_path)
                        self._kernel_functions[name] = fn
                    if rb_fn:
                        self._kernel_rollbacks[name] = rb_fn
            else:
                self._cache.dynamic[name] = cfg
                if fn:
                    self._functions[name] = fn
                if rb_fn:
                    self._rollbacks[name] = rb_fn

        # Rebuild backward-compat combined view
        self._tools = {**self._cache.kernel, **self._cache.dynamic}
        # _functions already has dynamic functions populated above
        self._functions.update(self._kernel_functions)
        self._rollbacks.update(self._kernel_rollbacks)
