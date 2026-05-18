"""BaseAgent -- ABC for ARF agents driven entirely by YAML config.

Subclasses only declare a config filename::

    class UserAgent(BaseAgent):
        _config_filename = "arf_user_agent.yaml"

    class SysAgent(BaseAgent):
        _config_filename = "arf_sys_agent.yaml"

Config resolution (from_config):
    1. Framework default  → src/arf/agent/<_config_filename>
    2. Workspace override → <workspace_dir>/<_config_filename>   (optional)
    Workspace keys deeply-merge over framework keys.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from abc import ABC
from pathlib import Path
from typing import Any

from ..engine import GraphParams, GraphEngine
from ..resources.model_adapter import ModelAdapter

logger = logging.getLogger("arf.agent")

MAX_SCHEMA_TOKENS_PER_TOOL = 400

# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.  Lists are replaced, not merged."""
    merged = copy.deepcopy(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


# framework config directory (where this file lives)
_FRAMEWORK_DIR = Path(__file__).resolve().parent


def resolve_config(config_filename: str, workspace_dir: str | None) -> dict:
    """Build merged config: framework default ← workspace override.

    Returns empty dict if neither file exists.
    """
    framework = _load_yaml(_FRAMEWORK_DIR / config_filename)

    if workspace_dir:
        workspace = _load_yaml(Path(workspace_dir) / config_filename)
    else:
        workspace = {}

    return _deep_merge(framework, workspace)


# ---------------------------------------------------------------------------
# schema trimming helper
# ---------------------------------------------------------------------------

def _trim_schema(schema: dict, max_tokens: int = MAX_SCHEMA_TOKENS_PER_TOOL) -> dict:
    text = json.dumps(schema, ensure_ascii=False)
    estimated = len(text) // 3
    if estimated <= max_tokens:
        return schema
    trimmed = copy.deepcopy(schema)

    def _strip(obj):
        if isinstance(obj, dict):
            obj.pop("description", None)
            for v in obj.values():
                _strip(v)
        elif isinstance(obj, list):
            for item in obj:
                _strip(item)

    props = trimmed.get("properties", {})
    for ps in props.values():
        if isinstance(ps, dict):
            for sk, sv in ps.items():
                if sk == "properties" and isinstance(sv, dict):
                    _strip(sv)
                elif sk == "items":
                    _strip(sv)
    return trimmed


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """ABC for ARF agents.

    Subclasses MUST set ``_config_filename``.  Instantiate via ``from_config()``.

    Config is resolved by merging workspace YAML over the framework default
    shipped in ``src/arf/agent/``.
    """

    _config_filename: str

    # ---- factory --------------------------------------------------------

    @classmethod
    def from_config(cls, registry, workspace_dir: str, hook_runner=None):
        """Build an agent from its config file (framework default + workspace override)."""
        raw = resolve_config(cls._config_filename, workspace_dir)
        agent_cfg = raw.get("agent", {})
        tools_cfg = raw.get("tools", {})
        identity = raw.get("identity", "")

        model_type = agent_cfg.get("model", "quick_thinking")
        adapter = cls._resolve_model_adapter(registry, model_type)
        if adapter is None:
            available = cls._list_model_types(registry)
            raise ValueError(
                f"Model type {model_type!r} requested by {cls._config_filename} "
                f"is not configured. Available types: {available}"
            )

        return cls(
            model=adapter,
            registry=registry,
            workspace_dir=workspace_dir,
            agent_name=agent_cfg.get("name", "ARF Agent"),
            agent_description=agent_cfg.get("description", ""),
            agent_mode=agent_cfg.get("mode", "sys"),
            default_model=model_type,
            max_turns=agent_cfg.get("max_turns", 8),
            language=agent_cfg.get("language", "zh"),
            classifier_enabled=agent_cfg.get("classifier_enabled", False),
            kernel_tools=frozenset(tools_cfg.get("kernel", [])),
            preload_tools=tools_cfg.get("preload", []),
            identity_prompt=identity,
            hook_runner=hook_runner,
        )

    # ---- constructor ----------------------------------------------------

    def __init__(
        self,
        *,
        model: ModelAdapter,
        registry,
        workspace_dir: str,
        agent_name: str,
        agent_description: str,
        agent_mode: str,
        default_model: str,
        max_turns: int,
        language: str,
        classifier_enabled: bool,
        kernel_tools: frozenset[str],
        preload_tools: list[str],
        identity_prompt: str,
        hook_runner=None,
    ):
        self.model = model
        self.registry = registry
        self.workspace_dir = workspace_dir
        self.agent_name = agent_name
        self.agent_description = agent_description
        self.agent_mode = agent_mode
        self.default_model = default_model
        self.max_turns = max_turns
        self.language = language
        self.classifier_enabled = classifier_enabled
        self.kernel_tools = kernel_tools
        self.identity_prompt = identity_prompt
        self.hook_runner = hook_runner

        # Active tools = kernel U preload (validated against registry)
        self._active_tools: set[str] = set(self.kernel_tools)
        all_tools = registry._items.get("tools", {})
        for tn in preload_tools:
            if tn in all_tools:
                self._active_tools.add(tn)

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _resolve_model_adapter(registry, model_type: str) -> ModelAdapter | None:
        for m in registry._items.get("models", {}).values():
            if m.get("model_type") == model_type:
                cfg = m.get("config", {})
                if cfg:
                    ctx = m.get("context_window", 1048576)
                    return ModelAdapter(cfg, context_window=ctx)
        return None

    @staticmethod
    def _list_model_types(registry) -> set[str]:
        return {m.get("model_type", "") for m in registry._items.get("models", {}).values()}

    @staticmethod
    def available_models(registry) -> list[str]:
        return sorted(BaseAgent._list_model_types(registry))

    # ---- prompt pipeline ------------------------------------------------

    def build_system_prompt(self) -> str:
        sections: list[str] = []
        for _prio, _name, method_name in sorted(self._prompt_pipeline()):
            method = getattr(self, method_name, None)
            if method is None:
                continue
            try:
                result = method()
                if result:
                    sections.append(result)
            except Exception:
                logger.warning("Prompt section '%s' failed", _name, exc_info=True)
        return "\n\n".join(sections)

    def _prompt_pipeline(self) -> list[tuple[int, str, str]]:
        return [
            (10, "workspace",        "_workspace_section"),
            (12, "user_resources",   "_user_resources_section"),
            (15, "long_term_memory", "_long_term_memory_section"),
            (20, "memory",           "_memory_section"),
            (25, "critical_rules",   "_critical_rules_section"),
            (30, "identity",         "_identity_section"),
            (50, "inventory",        "_inventory_section"),
            (60, "language",         "_language_instruction"),
        ]

    def _workspace_section(self) -> str:
        lines = [f"## Workspace: {self.agent_name}"]
        if self.agent_description:
            lines.append(self.agent_description)
        return "\n".join(lines)

    def _user_resources_section(self) -> str:
        """List user-created resources prominently so the agent is always
        aware of what already exists and can activate them on demand.
        """
        items = self.registry._items
        user_tools = [
            (n, t) for n, t in items.get("tools", {}).items()
            if t.get("source") == "user"
        ]
        user_skills = [
            (n, s) for n, s in items.get("skills", {}).items()
            if s.get("source") == "user"
        ]
        user_models = [
            (n, m) for n, m in items.get("models", {}).items()
            if m.get("source") == "user" and m.get("configured")
        ]

        if not user_tools and not user_skills and not user_models:
            return ""

        lines = ["## Your Resources"]
        lines.append("The following resources already exist in your workspace. "
                     "Before creating a new one, check if one of these fits. "
                     "Use `resource_loader` action `activate` to load a tool. "
                     "Use `file_reader` on `skills/<name>/skill.yaml` to load a skill.")

        if user_models:
            lines.append("")
            lines.append("### Your Models")
            for name, m in sorted(user_models):
                cfg = m.get("config", {})
                lines.append(f"- **{name}** ({m.get('model_type', '')}): "
                           f"{cfg.get('model_name', '')} @ {cfg.get('base_url', '')}")

        if user_tools:
            lines.append("")
            lines.append("### Your Tools")
            for name, t in sorted(user_tools):
                desc = t.get("description", "")
                active = " [active]" if name in self._active_tools else ""
                lines.append(f"- **{name}**{active}: {desc}" if desc else f"- **{name}**{active}")

        if user_skills:
            lines.append("")
            lines.append("### Your Skills")
            for name, s in sorted(user_skills):
                desc = s.get("description", "")
                lines.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")

        lines.append("")
        return "\n".join(lines)

    def _memory_section(self) -> str:
        if not self.workspace_dir:
            return ""
        mem_path = Path(self.workspace_dir) / "memory" / "session.md"
        if not mem_path.exists():
            return ""
        try:
            content = mem_path.read_text(encoding="utf-8").strip()
            lines = [l for l in content.split("\n") if not l.startswith("# ")]
            body = "\n".join(lines).strip()
            if not body or body == "Session Memory":
                return ""
            if len(body) > 2000:
                body = body[:2000] + "\n... (truncated)"
            return "## Memory\n\n" + body
        except Exception:
            return ""

    def _long_term_memory_section(self) -> str:
        if not self.workspace_dir:
            return ""
        mem_file = Path(self.workspace_dir) / "memory" / "long_term.md"
        if not mem_file.exists():
            return ""
        try:
            content = mem_file.read_text(encoding="utf-8").strip()
            if not content:
                return ""
            if len(content) > 4000:
                content = content[:4000] + "\n\n_(... truncated)_"
            return "## Long-Term Memory\n\n" + content
        except Exception:
            return ""

    _FALLBACK_CRITICAL_RULES = (
        "### R0: Self-check before EVERY response\n"
        "Before writing ANY response text, ask yourself: \"Did I call a tool to "
        "verify what I'm about to say?\" If the answer is no AND your response "
        "states any fact about current state (model, tools, files, config), "
        "STOP. Call the tool first. Then respond with the verified information.\n\n"
        "### R1: Verify, then answer\n"
        "Never state the current model, active tools, file contents, or any "
        "runtime state from memory. Call the relevant tool FIRST, then answer "
        "from the tool result. Guessing is always wrong.\n\n"
        "### R2: Tool calls ≠ words\n"
        "To switch models, you MUST call `model_switch` or `model_manager`. "
        "Saying \"switched to X\" or \"now using X\" without calling the tool "
        "is a violation. The same applies to any state-changing action.\n\n"
        "### R3: Verify after action\n"
        "After calling a tool that changes state, verify the result. If the "
        "tool says it succeeded, report success. If it failed or shows "
        "unexpected state, tell the user the actual result."
    )

    def _critical_rules_section(self) -> str:
        raw = resolve_config(self._config_filename, self.workspace_dir)
        rules = raw.get("critical_rules", "")
        if rules:
            return "## CRITICAL — Hard Rules\n\n" + rules.strip()
        return "## CRITICAL — Hard Rules\n\n" + self._FALLBACK_CRITICAL_RULES

    def _identity_section(self) -> str:
        return self.identity_prompt

    def _language_instruction(self) -> str:
        if self.language == "en":
            return "## Language Requirement\n\nAlways respond in **English**."
        return "## 语言要求\n\n请始终使用**简体中文**与用户交流。"

    # ---- inventory ------------------------------------------------------

    def _inventory_section(self) -> str:
        return "\n".join([
            *self._format_models(),
            *self._format_tools(),
            *self._format_skills(),
        ])

    def _format_models(self) -> list[str]:
        items = self.registry._items.get("models", {})
        lines = ["### Models"]
        if not items:
            lines.append("_(No models registered yet.)_")
        else:
            for name, m in items.items():
                mt = m.get("model_type", "")
                cfg = m.get("config", {})
                active = cfg.get("model_name", "")
                src = "[sys]" if m.get("source") == "system" else "[usr]"
                extra = f" -> {active}" if active else ""
                lines.append(f"- {src} **{name}** ({mt}){extra}")
        lines.append("")
        return lines

    def _format_tools(self) -> list[str]:
        items = self.registry._items.get("tools", {})
        lines = ["### Tools (Active)"]
        active_shown = False
        for name, t in items.items():
            if name not in self._active_tools:
                continue
            active_shown = True
            desc = t.get("description", "")
            src = "[sys]" if t.get("source") == "system" else "[usr]"
            lines.append(f"- {src} **{name}**: {desc}" if desc else f"- {src} **{name}**")
        if not active_shown:
            lines.append("_(No active tools)_")

        discoverable = [n for n in items if n not in self._active_tools]
        if discoverable:
            lines.append("")
            lines.append("### Tools (Discoverable -- activate via resource_loader)")
            for name in sorted(discoverable):
                t = items[name]
                src = "[sys]" if t.get("source") == "system" else "[usr]"
                lines.append(f"- {src} **{name}**")
            lines.append("")
            lines.append("_Discover tools by reading skills. Skills list their required "
                        "tools. After reading a skill, use `resource_loader` action "
                        "`activate` to load the tools you need._")
        lines.append("")
        return lines

    def _format_skills(self) -> list[str]:
        items = self.registry._items.get("skills", {})
        lines = ["### Skills"]
        if not items:
            lines.append("_(No skills available yet. You can help the user create one.)_")
        else:
            for name, s in items.items():
                desc = s.get("description", "")
                src = "[sys]" if s.get("source") == "system" else "[usr]"
                lines.append(f"- {src} **{name}**: {desc}" if desc else f"- {src} **{name}**")
        lines.append("")
        return lines

    # ---- tool building --------------------------------------------------

    def _build_openai_tools(self) -> list[dict]:
        tools = []
        for name, info in self.registry._items["tools"].items():
            if name not in self._active_tools:
                continue
            schema = _trim_schema(info.get("json_schema", {}))
            if not schema:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info.get("description", ""),
                    "parameters": schema,
                },
            })
        return tools

    # ---- tool execution -------------------------------------------------

    def _execute_tool(self, tool_name: str, arguments: str, project_dir: str | None = None) -> str:
        tool_info = self.registry.get_tool(tool_name)
        if not tool_info:
            return json.dumps({"error": f"Tool '{tool_name}' not found"})

        func = tool_info.get("function")
        if not func:
            return json.dumps({"error": f"Tool '{tool_name}' has no executable function"})

        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid arguments JSON: {arguments}"})

        if tool_name in ("memory_store", "model_manager", "model_switch", "file_download") and project_dir:
            args.setdefault("_workspace_dir", project_dir)

        if tool_name in ("file_writer", "file_deleter"):
            args.setdefault("_agent_mode", self.agent_mode)

        if tool_name == "resource_loader":
            args.setdefault("_active_tools", self._active_tools)
            args.setdefault("_kernel_tools", self.kernel_tools)
            args.setdefault("_all_tool_names", set(self.registry._items["tools"].keys()))
            args.setdefault("_registry", self.registry)

        if tool_name == "resource_registrar":
            args.setdefault("_registry", self.registry)

        orig_path = args.get("path", "")
        if tool_name in ("file_reader", "file_writer", "file_deleter", "file_download") and project_dir:
            raw_path = args.get("path", "")
            if raw_path.startswith("@sys/"):
                import arf.resources.system
                sys_dir = Path(arf.resources.system.__file__).parent
                resolved = (sys_dir / raw_path[5:]).resolve()
                if not str(resolved).startswith(str(sys_dir.resolve())):
                    return json.dumps({"error": "Path traversal blocked: @sys/ path escapes system resources"})
                if tool_name in ("file_writer", "file_deleter"):
                    return json.dumps({"error": "System resources are read-only. Use @sys/ only with file_reader."})
            else:
                if raw_path.startswith("/"):
                    raw_path = raw_path.lstrip("/")
                resolved = (Path(project_dir).resolve() / raw_path).resolve()
                if not str(resolved).startswith(str(Path(project_dir).resolve()) + os.sep) \
                   and str(resolved) != str(Path(project_dir).resolve()):
                    return json.dumps({"error": "Path traversal blocked: cannot access files outside workspace"})
            args["path"] = str(resolved)

        try:
            result = func(**args)
            args["_orig_path"] = orig_path
            self._reload_registry_if_needed(tool_name, args, result, project_dir)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error("Tool '%s' execution failed: %s", tool_name, e, exc_info=True)
            return json.dumps({"error": str(e)})

    def _reload_registry_if_needed(self, tool_name: str, args: dict, result: dict,
                                    project_dir: str | None) -> None:
        if not project_dir or not isinstance(result, dict) or not result.get("ok"):
            return
        check_path = args.get("_orig_path") or args.get("path", "")
        if not check_path or check_path.startswith("@sys/"):
            return
        if tool_name not in ("file_writer", "file_deleter"):
            return
        if any(check_path.startswith(p) for p in ("tools/", "skills/", "models/")):
            changes = self.registry.reload_user(project_dir)
            # Auto-activate newly created user tools so they're usable immediately
            for change in changes:
                if change.startswith("+tools/"):
                    new_name = change[len("+tools/"):]
                    self._active_tools.add(new_name)
                    logger.info("Auto-activated new user tool: %s", new_name)

    # ---- hooks ----------------------------------------------------------

    def _make_run_hook(self):
        runner = self.hook_runner
        if runner is None:
            return None

        def _run(event: str, payload: dict) -> dict | None:
            try:
                result = runner.run(event, payload)
                if result.exit_code == 1:
                    return {"blocked": True, "reason": result.message or "Blocked by hook"}
                if result.exit_code == 2:
                    return {"inject": result.message or "Hook message"}
                if result.exit_code == 0 and result.data and "inject" in result.data:
                    return {"inject": result.data["inject"]}
                return None
            except Exception:
                return None

        return _run

    # ---- engine wiring --------------------------------------------------

    def _available_model_types(self) -> set[str]:
        return {m.get("model_type", "") for m in self.registry._items.get("models", {}).values()}

    def _build_model_adapter(self, model_type: str):
        for m in self.registry._items.get("models", {}).values():
            if m.get("model_type") == model_type:
                cfg = m.get("config", {})
                if cfg:
                    ctx = m.get("context_window", 1048576)
                    return ModelAdapter(cfg, context_window=ctx)
        return None

    def _build_classifier_call(self):
        adapter = (self._build_model_adapter("quick_thinking")
                   or self._build_model_adapter("deep_thinking")
                   or self._build_model_adapter("quick_no_thinking"))
        if adapter is None:
            return None
        return lambda msgs: adapter.chat_complete(msgs, tools=None).content or ""

    def _build_graph_engine(self, project_dir: str | None = None):
        from ..engine import GraphEngine

        available_types = self._available_model_types()
        classifier_call = self._build_classifier_call()

        engine = GraphEngine(
            call_model=lambda msgs, tls: self.model.chat_complete(
                msgs, tools=self._build_openai_tools()),
            execute_tool=lambda name, args: self._execute_tool(name, args, project_dir),
            stream_model=lambda msgs, tls: self.model.chat_stream_full(
                msgs, tools=self._build_openai_tools()),
            run_hook=self._make_run_hook(),
            model_adapter_factory=lambda mt: self._build_model_adapter(mt),
            classifier_call=classifier_call,
            classifier_enabled=self.classifier_enabled,
            available_model_types=available_types,
            user_model_preference=self.default_model,
        )
        engine.set_tools_refresher(lambda: self._build_openai_tools())
        engine._trace_collector = getattr(self, '_trace_collector', None)
        engine._system_tool_names = frozenset(
            name for name, info in self.registry._items["tools"].items()
            if info.get("source") == "system"
        )
        return engine

    def _build_query_params(self, message: str, history: list[dict],
                            max_turns: int | None = None) -> GraphParams:
        system_prompt = self.build_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]
        for h in history:
            messages.append(h)
        messages.append({"role": "user", "content": message})

        tools = self._build_openai_tools()
        return GraphParams(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools if tools else None,
            max_turns=max_turns if max_turns is not None else self.max_turns,
        )

    # ---- public API -----------------------------------------------------

    def chat_with_tools(self, message: str, history: list[dict],
                        project_dir: str | None = None,
                        max_turns: int | None = None):
        params = self._build_query_params(message, history, max_turns)
        engine = self._build_graph_engine(project_dir)
        result = engine.run(params)
        response = result.response if not result.truncated else (
            "已达到本轮对话最大轮次限制，请开始新对话以继续。"
            if self.language == "zh" else
            "Maximum turns reached. Start a new session to continue."
        )
        return response, result.history, result.tool_events, result.usage, result.transition_log

    def chat_stream_with_tools(self, message: str, history: list[dict],
                               project_dir: str | None = None,
                               max_turns: int | None = None):
        params = self._build_query_params(message, history, max_turns)
        engine = self._build_graph_engine(project_dir)
        yield from engine.run_stream(params)


# ---------------------------------------------------------------------------
# default configs → workspace
# ---------------------------------------------------------------------------

def generate_default_configs(workspace_dir: str) -> tuple[Path, Path]:
    """Copy framework default agent configs into *workspace_dir* if missing.

    Called by SessionManager so users can customise their agent configs.
    Returns (user_path, sys_path).
    """
    ws = Path(workspace_dir)
    user_path = ws / "arf_user_agent.yaml"
    sys_path = ws / "arf_sys_agent.yaml"

    if not user_path.exists():
        src = _FRAMEWORK_DIR / "arf_user_agent.yaml"
        if src.exists():
            user_path.write_text(src.read_text(encoding="utf-8"))
            logger.info("Copied default user agent config → %s", user_path)

    if not sys_path.exists():
        src = _FRAMEWORK_DIR / "arf_sys_agent.yaml"
        if src.exists():
            sys_path.write_text(src.read_text(encoding="utf-8"))
            logger.info("Copied default sys agent config → %s", sys_path)

    return user_path, sys_path
