"""BaseAgent -- shared ARF agent logic for prompt pipeline and GraphEngine wiring."""

import copy
import json
import logging
import os
from pathlib import Path

from ..engine import GraphParams, GraphEngine
from ..resources.model_adapter import ModelAdapter

logger = logging.getLogger("arf.agent")

MAX_SCHEMA_TOKENS_PER_TOOL = 400


def _trim_schema(schema: dict, max_tokens: int = MAX_SCHEMA_TOKENS_PER_TOOL) -> dict:
    text = json.dumps(schema, ensure_ascii=False)
    estimated = len(text) // 3
    if estimated <= max_tokens:
        return schema
    trimmed = copy.deepcopy(schema)

    def _strip_descriptions(obj):
        if isinstance(obj, dict):
            obj.pop("description", None)
            for v in obj.values():
                _strip_descriptions(v)
        elif isinstance(obj, list):
            for item in obj:
                _strip_descriptions(item)

    props = trimmed.get("properties", {})
    for prop_schema in props.values():
        if isinstance(prop_schema, dict):
            for sub_key, sub_val in prop_schema.items():
                if sub_key == "properties" and isinstance(sub_val, dict):
                    _strip_descriptions(sub_val)
                elif sub_key == "items":
                    _strip_descriptions(sub_val)
    return trimmed


class BaseAgent:
    """Shared agent foundation -- prompt pipeline, tool building, GraphEngine wiring.

    Subclasses define their own PROMPT_PIPELINE, TOOLS (active tool set),
    KERNEL_TOOLS, and identity sections.
    """

    PROMPT_PIPELINE: list[tuple[int, str, str]] = [
        (10, "workspace",        "_workspace_section"),
        (15, "long_term_memory", "_long_term_memory_section"),
        (20, "memory",           "_memory_section"),
        (25, "critical_rules",   "_critical_rules_section"),
        (30, "identity",         "_identity_section"),
        (50, "inventory",        "_inventory_section"),
        (60, "language",         "_language_instruction"),
    ]

    KERNEL_TOOLS: frozenset[str] = frozenset()
    AGENT_MODE: str = "sys"  # "user" | "sys"

    def __init__(self, model: ModelAdapter, registry, workspace_dir: str | None = None,
                 language: str = "zh", hook_runner=None):
        self.model = model
        self.registry = registry
        self.workspace_dir = workspace_dir
        self.language = language
        self.hook_runner = hook_runner
        self._active_tools: set[str] = set(self.KERNEL_TOOLS)
        for tool_name in self._read_preload():
            if tool_name in self.registry._items.get("tools", {}):
                self._active_tools.add(tool_name)

    @property
    def agent_mode(self) -> str:
        return self.AGENT_MODE

    # ---- prompt pipeline --------------------------------------------

    def build_system_prompt(self) -> str:
        sections = []
        for _prio, _name, method_name in sorted(self.PROMPT_PIPELINE):
            method = getattr(self, method_name, None)
            if method is None:
                continue
            try:
                result = method()
                if result:
                    sections.append(result)
            except Exception:
                logger.warning("Prompt section '%s' failed", _name, exc_info=True)
                continue
        return "\n\n".join(sections)

    def _critical_rules_section(self) -> str:
        return (
            "## CRITICAL — Hard Rules\n\n"
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

    def _workspace_section(self) -> str:
        if not self.workspace_dir:
            return ""
        try:
            import yaml
            cfg_path = Path(self.workspace_dir) / "arf_agent.yaml"
            if not cfg_path.exists():
                return ""
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            agent = cfg.get("agent", {})
            name = agent.get("name", "Workspace")
            desc = agent.get("description", "")
            lines = [f"## Workspace: {name}"]
            if desc:
                lines.append(desc)
            return "\n".join(lines)
        except Exception:
            return ""

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

    def _language_instruction(self) -> str:
        if self.language == "en":
            return "## Language Requirement\n\nAlways respond in **English**."
        return "## 语言要求\n\n请始终使用**简体中文**与用户交流。"

    # ---- inventory formatting ---------------------------------------

    def _format_models(self) -> list[str]:
        items = self.registry._items["models"]
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
        items = self.registry._items["tools"]
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
        items = self.registry._items["skills"]
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

    # ---- yaml helpers ------------------------------------------------

    def _read_preload(self) -> list[str]:
        if not self.workspace_dir:
            return []
        try:
            import yaml
            cfg_path = Path(self.workspace_dir) / "arf_agent.yaml"
            if not cfg_path.exists():
                return []
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            resources = cfg.get("resources", {})
            preload = resources.get("preload", [])
            return preload if isinstance(preload, list) else []
        except Exception:
            return []

    def _read_default_model(self) -> str | None:
        if not self.workspace_dir:
            return None
        try:
            import yaml
            cfg_path = Path(self.workspace_dir) / "arf_agent.yaml"
            if not cfg_path.exists():
                return None
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return cfg.get("agent", {}).get("default_model")
        except Exception:
            return None

    def _read_max_turns(self) -> int:
        if not self.workspace_dir:
            return 10
        try:
            import yaml
            cfg_path = Path(self.workspace_dir) / "arf_agent.yaml"
            if not cfg_path.exists():
                return 10
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            mt = cfg.get("agent", {}).get("max_turns", 10)
            return max(1, int(mt)) if isinstance(mt, (int, float)) else 10
        except Exception:
            return 10

    # ---- tool building ------------------------------------------------

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

    # ---- tool execution -----------------------------------------------

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

        # Inject workspace dir
        if tool_name in ("memory_store", "model_manager", "model_switch", "file_download") and project_dir:
            args.setdefault("_workspace_dir", project_dir)

        # Inject agent mode for path-restricted tools
        if tool_name in ("file_writer", "file_deleter"):
            args.setdefault("_agent_mode", self.AGENT_MODE)

        # Inject state for resource_loader
        if tool_name == "resource_loader":
            args.setdefault("_active_tools", self._active_tools)
            args.setdefault("_kernel_tools", self.KERNEL_TOOLS)
            args.setdefault("_all_tool_names",
                           set(self.registry._items["tools"].keys()))
            args.setdefault("_registry", self.registry)

        # Inject registry for resource_registrar
        if tool_name == "resource_registrar":
            args.setdefault("_registry", self.registry)

        # Path resolution for file tools
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
        is_modify = tool_name in ("file_writer", "file_deleter")
        if not is_modify:
            return
        resource_prefixes = ("tools/", "skills/", "models/")
        if any(check_path.startswith(p) for p in resource_prefixes):
            self.registry.reload_user(project_dir)

    # ---- engine wiring ------------------------------------------------

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

    def _available_model_types(self) -> set[str]:
        types = set()
        for name, m in self.registry._items.get("models", {}).items():
            mt = m.get("model_type", "")
            if mt:
                types.add(mt)
        return types

    def _build_model_adapter(self, model_type: str):
        for name, m in self.registry._items.get("models", {}).items():
            if m.get("model_type") == model_type:
                cfg = m.get("config", {})
                if cfg:
                    ctx = m.get("context_window", 1048576)
                    return ModelAdapter(cfg, context_window=ctx)
        return None

    def _build_classifier_call(self):
        adapter = self._build_model_adapter("quick_thinking")
        if adapter is None:
            adapter = self._build_model_adapter("deep_thinking")
        if adapter is None:
            adapter = self._build_model_adapter("quick_no_thinking")
        if adapter is None:
            return None
        return lambda msgs: adapter.chat_complete(msgs, tools=None).content or ""

    def _build_graph_engine(self, project_dir: str | None = None,
                            classifier_enabled: bool = False):
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
            classifier_enabled=classifier_enabled,
            available_model_types=available_types,
            user_model_preference=self._read_default_model(),
        )
        engine.set_tools_refresher(lambda: self._build_openai_tools())
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
            max_turns=max_turns if max_turns is not None else self._read_max_turns(),
        )

    # ---- public API --------------------------------------------------

    def chat_with_tools(self, message: str, history: list[dict],
                        project_dir: str | None = None,
                        max_turns: int | None = None):
        params = self._build_query_params(message, history, max_turns)
        engine = self._build_graph_engine(
            project_dir, classifier_enabled=self._classifier_enabled())
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
        engine = self._build_graph_engine(
            project_dir, classifier_enabled=self._classifier_enabled())
        yield from engine.run_stream(params)

    def _classifier_enabled(self) -> bool:
        return False
