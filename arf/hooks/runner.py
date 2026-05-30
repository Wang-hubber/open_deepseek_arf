"""SubprocessHookRunner — execute hooks as subprocesses with parallel launch."""
import asyncio
import os
from arf.core.config_base import HookDefinition
from arf.core.results import HookResult


class SubprocessHookRunner:
    def __init__(self, hooks: list[HookDefinition], plugin_runtime=None) -> None:
        from arf.core.plugin_runtime import PluginRuntime
        self._hooks: dict[str, list[HookDefinition]] = {}
        self._order: dict[str, list[str]] = {}
        self._runtime: PluginRuntime | None = plugin_runtime
        for h in hooks:
            self._hooks.setdefault(h.type, []).append(h)

    def update_runtime(self, session_id: str | None = None,
                       interaction_round: int | None = None) -> None:
        if self._runtime is None:
            return
        if session_id is not None:
            self._runtime.session_id = session_id
        if interaction_round is not None:
            self._runtime.interaction_round = interaction_round

    def set_order(self, event_type: str, hook_names: list[str]) -> None:
        self._order[event_type] = hook_names

    def get_definitions(self) -> list[HookDefinition]:
        return [h for hooks in self._hooks.values() for h in hooks]

    async def fire(self, event_type: str, context: dict) -> list[HookResult]:
        hooks = self._hooks.get(event_type, [])
        ordered = self._order.get(event_type, [])
        if ordered:
            name_map = {h.name: h for h in hooks}
            resolved = [name_map[n] for n in ordered if n in name_map]
            remaining = [h for h in hooks if h.name not in ordered]
            hooks = resolved + remaining

        all_results: list[HookResult] = []

        async def _run_hook(hook: HookDefinition) -> HookResult:
            results: list[HookResult] = []
            for cmd in hook.run:
                env_vars = {**os.environ}
                for k, v in (hook.env or {}).items():
                    for ck, cv in context.items():
                        v = v.replace(f"$ARF_{ck.upper()}", str(cv))
                    env_vars[k] = v
                # Inject PluginRuntime JSON if provided in context
                runtime_dict = context.get("plugin_runtime")
                if runtime_dict:
                    import json as _json
                    env_vars["ARF_RUNTIME"] = _json.dumps(runtime_dict)
                    # Deprecated individual vars (backward compat)
                    env_vars.setdefault("ARF_ROUND", str(runtime_dict.get("interaction_round", 0)))
                    env_vars.setdefault("ARF_SESSION_ID", runtime_dict.get("session_id", "default"))
                    env_vars.setdefault("ARF_MEMORY_DIR", runtime_dict.get("memory_dir", "./memory"))
                try:
                    proc = await asyncio.create_subprocess_shell(
                        cmd, env=env_vars,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    tout = _pars_timeout(hook.timeout)
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=tout)
                    rc = proc.returncode or 0
                    hr = HookResult(
                        hook_name=hook.name, exit_code=rc,
                        stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                        stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                        injected_message=stdout.decode("utf-8", errors="replace") if rc == 2 and stdout else None,
                    )
                    results.append(hr)
                    if rc != 0:
                        break
                except asyncio.TimeoutError:
                    if proc:
                        proc.kill()
                    results.append(HookResult(hook_name=hook.name, exit_code=-1, stderr="timeout"))
                    break
            return results[-1] if results else HookResult(hook_name=hook.name, exit_code=0)

        tasks = [_run_hook(h) for h in hooks]
        resolved_list = await asyncio.gather(*tasks, return_exceptions=True)
        for r in resolved_list:
            if isinstance(r, HookResult):
                all_results.append(r)
            elif isinstance(r, Exception):
                all_results.append(HookResult(hook_name="unknown", exit_code=-1, stderr=str(r)))
        return all_results


def _pars_timeout(s: str) -> float:
    s = s.strip().lower()
    for suffix, mult in [("s", 1), ("m", 60), ("h", 3600)]:
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return 30.0
