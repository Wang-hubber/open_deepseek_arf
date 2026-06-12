"""SubprocessHookRunner — executes side plugins as fire-and-forget subprocesses."""
import asyncio
import json
import logging
import os
from arf.core.plugin_context import PluginContext
from arf.core.protocols.plugin import PluginProtocol

logger = logging.getLogger("arf.hooks.subprocess")


class SubprocessHookRunner:
    """Fires side hooks as subprocesses concurrently. Does not block the engine.

    Side hooks are fire-and-forget: launched in a task, failure is logged.
    Used for trace, metrics, and other observability plugins.
    Also supports external hook scripts registered via HookDefinition.
    """

    def __init__(self, plugins: list[PluginProtocol] | None = None,
                 hook_defs: list | None = None,
                 plugin_runtime=None) -> None:
        from arf.core.plugin_runtime import PluginRuntime
        self._plugins: dict[str, list[PluginProtocol]] = {}
        self._runtime: PluginRuntime | None = plugin_runtime
        self._hook_defs: dict[str, list] = {}
        if plugins:
            for p in plugins:
                self.register(p)
        if hook_defs:
            for h in hook_defs:
                self._hook_defs.setdefault(h.type, []).append(h)

    def register(self, plugin: PluginProtocol) -> None:
        for hook_name, mode in plugin.hooks.items():
            if mode == "side":
                self._plugins.setdefault(hook_name, []).append(plugin)
        logger.debug("Registered side plugin '%s' on hooks: %s",
                     plugin.name, [h for h, m in plugin.hooks.items() if m == "side"])

    def update_runtime(self, **kwargs) -> None:
        if self._runtime:
            for k, v in kwargs.items():
                setattr(self._runtime, k, v)

    async def fire(self, event_type: str, ctx: PluginContext) -> None:
        """Fire all side plugins concurrently and wait for completion.

        In-process plugins run via asyncio.gather — all must finish before
        the caller proceeds, so side effects (e.g. trace writes) are visible.
        Subprocess hooks remain fire-and-forget.
        """
        plugins = self._plugins.get(event_type, [])
        hook_defs = self._hook_defs.get(event_type, [])

        tasks: list[asyncio.Task] = []

        # Subprocess-based external hooks (fire-and-forget)
        for hd in hook_defs:
            asyncio.ensure_future(self._run_subprocess_hook(hd, ctx))

        # In-process side plugins — await completion so side effects
        # (trace writes, metrics) are visible when the caller proceeds.
        for plugin in plugins:
            tasks.append(
                asyncio.ensure_future(self._safe_fire_in_process(plugin, event_type, ctx))
            )

        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_fire_in_process(self, plugin, event_type, ctx):
        try:
            await plugin.on_hook(event_type, ctx)
        except Exception:
            logger.exception("Side plugin '%s' failed on hook '%s'", plugin.name, event_type)

    async def _run_subprocess_hook(self, hook_def, ctx):
        try:
            env_vars = {**os.environ}
            runtime_dict = self._runtime.to_dict() if self._runtime else {}
            merge_dict = dict(runtime_dict)
            merge_dict.update(ctx.hook_data)
            if runtime_dict:
                env_vars["ARF_RUNTIME"] = json.dumps(runtime_dict)
            for k, v in (getattr(hook_def, 'env', {}) or {}).items():
                for mk, mv in merge_dict.items():
                    v = v.replace(f"$ARF_{mk.upper()}", str(mv))
                env_vars[k] = v
            for cmd in getattr(hook_def, 'run', []):
                proc = await asyncio.create_subprocess_shell(
                    cmd, env=env_vars,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                ctx.hook_data.setdefault("_subprocess_results", []).append({
                    "hook": getattr(hook_def, 'name', 'unknown'),
                    "command": cmd,
                    "exit_code": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace")[:2000] if stdout else "",
                    "stderr": stderr.decode("utf-8", errors="replace")[:2000] if stderr else "",
                })
        except Exception:
            logger.exception("Subprocess hook '%s' failed", getattr(hook_def, 'name', 'unknown'))
