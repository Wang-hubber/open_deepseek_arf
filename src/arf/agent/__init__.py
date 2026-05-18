"""ARF Agent -- conversational orchestration layer."""

import copy
import json
import logging
import os
from pathlib import Path

from ..engine import GraphParams, GraphEngine
from ..resources.manager import ResourceRegistry

logger = logging.getLogger("arf.agent")
from ..resources.model_adapter import ModelAdapter


MAX_SCHEMA_TOKENS_PER_TOOL = 400


def _trim_schema(schema: dict, max_tokens: int = MAX_SCHEMA_TOKENS_PER_TOOL) -> dict:
    """Recursively strip descriptions from nested properties if schema exceeds token budget."""
    text = json.dumps(schema, ensure_ascii=False)
    estimated = len(text) // 3  # rough token estimate

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

    # Strip descriptions from nested properties, keep top-level
    props = trimmed.get("properties", {})
    for prop_schema in props.values():
        if isinstance(prop_schema, dict):
            for sub_key, sub_val in prop_schema.items():
                if sub_key == "properties" and isinstance(sub_val, dict):
                    _strip_descriptions(sub_val)
                elif sub_key == "items":
                    _strip_descriptions(sub_val)

    return trimmed


class ARFAgent:
    """Core agent that understands user intent, orchestrates resources,
    and generates missing components."""

    # Sections are (priority, name, method) -- lower priority = earlier in prompt.
    # Override or extend in subclasses to customize the prompt assembly.
    PROMPT_PIPELINE: list[tuple[int, str, str]] = [
        (10, "workspace",        "_workspace_section"),
        (15, "long_term_memory", "_long_term_memory_section"),
        (20, "memory",           "_memory_section"),
        (25, "critical_rules",   "_critical_rules_section"),
        (30, "identity",         "_identity_section"),
        (50, "inventory",        "_inventory_section"),
        (60, "language",         "_language_instruction"),
    ]

    # Tools always active -- cannot be deactivated.
    # file_* -> resource discovery, resource_loader -> activation,
    # memory_store / model_manager -> session lifecycle (extraction on disconnect)
    KERNEL_TOOLS = frozenset({
        "file_reader", "file_writer", "file_deleter",
        "resource_loader", "memory_store", "model_manager",
        "resource_registrar", "model_switch", "manage_hooks",
    })

    def __init__(self, model: ModelAdapter, registry: ResourceRegistry, workspace_dir: str | None = None, language: str = "zh", hook_runner=None):
        self.model = model
        self.registry = registry
        self.workspace_dir = workspace_dir  # user workspace root
        self.language = language
        self.hook_runner = hook_runner  # HookRunner instance for PreToolUse/PostToolUse
        # Active tools -- kernel set + optional preload from arf_agent.yaml.
        # Mutated by resource_loader at runtime; read by _build_openai_tools() each loop iteration.
        self._active_tools: set[str] = set(self.KERNEL_TOOLS)
        for tool_name in self._read_preload():
            if tool_name in self.registry._items.get("tools", {}):
                self._active_tools.add(tool_name)

    def _read_preload(self) -> list[str]:
        """Read resources.preload from arf_agent.yaml. Tools listed here are
        auto-activated at session start (in addition to kernel tools)."""
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
        """Read agent.default_model from arf_agent.yaml."""
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
        """Read agent.max_turns from arf_agent.yaml. User controls their own budget."""
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

    # ---- prompt pipeline --------------------------------------------

    def build_system_prompt(self) -> str:
        """Assemble system prompt by running each section in the pipeline.
        Sections are defined in PROMPT_PIPELINE -- reorder or extend to customize.
        """
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

    # -- critical rules -------------------------------------------------

    def _critical_rules_section(self) -> str:
        """Hard behavioral rules — placed after memory so the model has context first."""
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

    # -- workspace config ----------------------------------------------

    def _workspace_section(self) -> str:
        """Load workspace identity from arf_agent.yaml."""
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

    # -- memory --------------------------------------------------------

    def _memory_section(self) -> str:
        """Load persistent memory from memory/session.md."""
        if not self.workspace_dir:
            return ""
        mem_path = Path(self.workspace_dir) / "memory" / "session.md"
        if not mem_path.exists():
            return ""
        try:
            content = mem_path.read_text(encoding="utf-8").strip()
            # Skip the heading line, keep only user content
            lines = [l for l in content.split("\n") if not l.startswith("# ")]
            body = "\n".join(lines).strip()
            if not body or body == "Session Memory":
                return ""
            # Truncate to ~2000 chars to avoid bloating the prompt
            if len(body) > 2000:
                body = body[:2000] + "\n... (truncated)"
            return "## Memory\n\n" + body
        except Exception:
            return ""

    # -- long-term memory ---------------------------------------------

    def _long_term_memory_section(self) -> str:
        """Load long-term memory from memory/long_term.md."""
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

    # -- identity ------------------------------------------------------

    def _identity_section(self) -> str:
        """Core identity and skill usage instructions."""
        return (
            "You are ARF Agent, a workspace manager. "
            "You help users accomplish tasks through natural language conversation. "
            "You can read/write files, execute tools, and leverage skills.\n\n"
            "## Path System\n\n"
            "Two path spaces govern all file access:\n\n"
            "| Prefix | Target | Access | Example |\n"
            "|--------|--------|--------|---------|\n"
            "| `@sys/` | Framework built-in resources | **read-only** | `@sys/tools/file_reader/function.py` |\n"
            "| _(no prefix)_ | User workspace | read + write | `tools/weather/function.py` |\n\n"
            "Relative paths (no `@sys/`) resolve against the user's workspace root.\n"
            "- User tools: `tools/<name>/tool.yaml` + `tools/<name>/function.py`\n"
            "- User skills: `skills/<name>/skill.yaml`\n"
            "- User models: `models/<name>/config.yaml`\n"
            "- Uploads: `uploads/<filename>`\n"
            "- Memory: `memory/session.md`, `memory/long_term.md`\n\n"
            "System resources are marked [sys], user resources [usr]. "
            "Use `arf clone <type> <name>` to copy a system resource into your workspace.\n\n"
            "## Progressive Discovery\n\n"
            "ARF uses progressive disclosure to keep the prompt lean:\n\n"
            "1. **Kernel tools** are always active: file_reader, file_writer, file_deleter, "
            "resource_loader, memory_store, model_manager.\n"
            "2. **Skills** are listed under Available Resources with name + description. "
            "When a skill matches the user's intent, read it via `file_reader`:\n"
            "   - System skill: `@sys/skills/<name>/skill.yaml`\n"
            "   - User skill: `skills/<name>/skill.yaml`\n"
            "3. **Activate tools** with `resource_loader` action `activate` after reading "
            "a skill that requires them. They become available immediately.\n"
            "4. User tools are never pre-loaded -- discover and activate them through skills.\n\n"
            "## Resource Creation -- Gated Workflow\n\n"
            "**This is a strict sequence. Do NOT skip or reorder steps.**\n\n"
            "### Gate 1 -- Design\n"
            "Read `@sys/skills/resource_scaffold/skill.yaml` for the format spec.\n"
            "Present the design to the user: tool/skill name, parameters, workflow.\n"
            "**STOP here.** Wait for the user to say \"go ahead\", \"yes\", \"确认\", etc.\n"
            "Do NOT call file_writer yet -- not even for a \"preview\".\n\n"
            "### Gate 2 -- Write\n"
            "Only after explicit user approval, call file_writer to create each file.\n"
            "The result includes a content preview so the user can review what was written.\n"
            "After writing, the registry auto-reloads -- the new resource is immediately "
            "discoverable by resource_loader.\n\n"
            "### Gate 3 -- Validate\n"
            "Read `@sys/skills/validate_tool/skill.yaml` and follow its checklist.\n"
            "Verify: tool.yaml parses, function.py imports, execute() is callable.\n\n"
            "### Gate 4 -- Activate\n"
            "Call `resource_loader` action `activate` with the new tool name.\n"
            "If activation succeeds, the tool is ready. Inform the user.\n\n"
            "**Hard rules:**\n"
            "- Gate 1 is the checkpoint. Never skip it.\n"
            "- file_writer is ONLY allowed after the user explicitly approves the design.\n"
            "- If the user says \"looks good\" or \"go ahead\", confirm once more before writing.\n"
            "- Use Python stdlib only unless the user specifies dependencies.\n"
            "- All execute() functions must return a dict.\n\n"
            "## Error Handling\n\n"
            "Tool returned `error` -> read `@sys/skills/error_handler/skill.yaml` "
            "(classify -> fix -> retry -> escalate).\n"
            "If resource_loader returns `unknown_tools` after Gate 3, the registry may "
            "need a reload -- try resource_loader again first, then fall back to re-reading "
            "the tool files.\n\n"
            "## Memory Management\n\n"
            "- \"remember this\" -> `memory_store` action `write`\n"
            "- `compression_needed: true` -> read `@sys/skills/memory_management/skill.yaml`\n\n"
            "## Model Management\n\n"
            "Use `model_manager` tool to manage model configs (list / create / test / switch). "
            "NEVER write `models/*/config.yaml` directly.\n\n"
            "## Model Switching\n\n"
            "The session starts with `quick_thinking` (fast, reasoning enabled). "
            "When tasks become more complex, use `model_switch` to switch to "
            "`deep_thinking`. Read `@sys/skills/model_switch/skill.yaml` for "
            "detailed guidance on when and how to switch.\n\n"
            "If automatic model routing is enabled (ARF_CLASSIFIER_ENABLED=1), "
            "the system analyzes each new message and automatically selects the "
            "best model tier before generating a response:\n"
            "- **medium** (greetings, code, debugging, tool orchestration) -> quick_thinking\n"
            "- **complex** (system design, multi-file refactoring) -> deep_thinking\n"
            "You can still manually override the model at any time with `model_switch`.\n\n"
            "`quick_no_thinking` is reserved for background tasks (compression, "
            "memory extraction, summaries). Do NOT switch to it manually.\n\n"
            "## Guidelines\n\n"
            "- Reference resource names and paths when answering.\n"
            "- When a tool can handle a request, call it directly.\n"
            "- Keep responses concise and actionable.\n"
            "- Always include the tool name in backticks when discussing resources."
        )

    def _inventory_section(self) -> str:
        """Dynamic resource inventory -- active tools, discoverable tools, skills."""
        lines = ["## Available Resources", ""]
        lines.extend(self._format_models())
        lines.extend(self._format_tools())
        lines.extend(self._format_skills())
        return "\n".join(lines)

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

        # Discoverable tools -- name only, hint to read skills
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

    # -- language instruction -------------------------------------------

    def _language_instruction(self) -> str:
        if self.language == "en":
            return "## Language Requirement\n\nAlways respond in **English**."
        return "## 语言要求\n\n请始终使用**简体中文**与用户交流。"

    # ---- query assembly -----------------------------------------------

    def _make_run_hook(self):
        """Build a run_hook callback for the GraphEngine.

        Returns a callable matching the engine's hook contract:
          run_hook(event: str, payload: dict) -> dict | None
        """
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

    def _build_query_params(self, message: str, history: list[dict]) -> GraphParams:
        """Assemble GraphParams from agent state for the GraphEngine."""
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
            max_turns=self._read_max_turns(),
        )

    # ---- tool calling ------------------------------------------------

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

        # Inject workspace dir for tools that need it
        if tool_name in ("memory_store", "model_manager", "model_switch") and project_dir:
            args.setdefault("_workspace_dir", project_dir)

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
        orig_path = args.get("path", "")  # save before resolution for reload check
        if tool_name in ("file_reader", "file_writer", "file_deleter") and project_dir:
            raw_path = args.get("path", "")
            # @sys/ prefix -> resolve against system resources directory (read-only)
            if raw_path.startswith("@sys/"):
                import arf.resources.system
                sys_dir = Path(arf.resources.system.__file__).parent
                resolved = (sys_dir / raw_path[5:]).resolve()
                if not str(resolved).startswith(str(sys_dir.resolve())):
                    return json.dumps({"error": "Path traversal blocked: @sys/ path escapes system resources"})
                if tool_name in ("file_writer", "file_deleter"):
                    return json.dumps({"error": "System resources are read-only. Use @sys/ only with file_reader."})
            else:
                # Strip leading / so Pathlib doesn't discard project_dir
                if raw_path.startswith("/"):
                    raw_path = raw_path.lstrip("/")
                resolved = (Path(project_dir).resolve() / raw_path).resolve()
                if not str(resolved).startswith(str(Path(project_dir).resolve()) + os.sep) \
                   and str(resolved) != str(Path(project_dir).resolve()):
                    return json.dumps({"error": "Path traversal blocked: cannot access files outside workspace"})
            args["path"] = str(resolved)

        try:
            result = func(**args)
            # Hot-reload registry when file tools modify resource directories
            args["_orig_path"] = orig_path
            self._reload_registry_if_needed(tool_name, args, result, project_dir)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error("Tool '%s' execution failed: %s", tool_name, e, exc_info=True)
            return json.dumps({"error": str(e)})

    def _reload_registry_if_needed(self, tool_name: str, args: dict, result: dict, project_dir: str | None) -> None:
        """Trigger registry hot-reload when a file tool modifies resource directories."""
        if not project_dir or not isinstance(result, dict) or not result.get("ok"):
            return

        # Use the original (pre-resolution) path to match resource prefixes
        check_path = args.get("_orig_path") or args.get("path", "")
        if not check_path or check_path.startswith("@sys/"):
            return

        is_modify = tool_name in ("file_writer", "file_deleter")
        if not is_modify:
            return

        resource_prefixes = ("tools/", "skills/", "models/")
        if any(check_path.startswith(p) for p in resource_prefixes):
            self.registry.reload_user(project_dir)

    # ---- engine selection --------------------------------------------

    def _available_model_types(self) -> set[str]:
        """Return the set of configured model types in the registry."""
        types = set()
        for name, m in self.registry._items.get("models", {}).items():
            mt = m.get("model_type", "")
            if mt:
                types.add(mt)
        return types

    def _build_model_adapter(self, model_type: str):
        """Create a ModelAdapter for a given model type from the registry."""
        for name, m in self.registry._items.get("models", {}).items():
            if m.get("model_type") == model_type:
                cfg = m.get("config", {})
                if cfg:
                    ctx = m.get("context_window", 1048576)
                    return ModelAdapter(cfg, context_window=ctx)
        return None

    def _build_classifier_call(self):
        """Create a classifier callable using a fast model."""
        # Prefer quick_thinking for classification
        adapter = self._build_model_adapter("quick_thinking")
        if adapter is None:
            adapter = self._build_model_adapter("deep_thinking")
        if adapter is None:
            adapter = self._build_model_adapter("quick_no_thinking")
        if adapter is None:
            return None
        return lambda msgs: adapter.chat_complete(
            msgs, tools=None
        ).content or ""

    def _build_graph_engine(self, project_dir: str | None = None):
        """Build a GraphEngine with all DI callables injected."""
        from ..engine import GraphEngine

        available_types = self._available_model_types()
        classifier_call = self._build_classifier_call()
        classifier_enabled = os.environ.get("ARF_CLASSIFIER_ENABLED", "").lower() in ("1", "true", "yes")

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

        # Wire the tools refresher so post-tool state refresh works
        engine.set_tools_refresher(lambda: self._build_openai_tools())

        return engine

    def chat_with_tools(self, message: str, history: list[dict], project_dir: str | None = None):
        """Send a message, execute tool calls via the GraphEngine."""
        params = self._build_query_params(message, history)
        engine = self._build_graph_engine(project_dir)
        result = engine.run(params)

        response = result.response if not result.truncated else (
            "已达到本轮对话最大轮次限制（{}轮），请开始新对话以继续。".format(params.max_turns)
            if self.language == "zh" else
            "Maximum turns ({} turns) reached. Start a new session to continue.".format(params.max_turns)
        )
        return response, result.history, result.tool_events, result.usage, result.transition_log

    # ---- streaming tool calling -------------------------------------

    def chat_stream_with_tools(self, message: str, history: list[dict], project_dir: str | None = None):
        """Streaming generator that yields text chunks and tool call events in real time.

        Yields:
            {"type": "chunk", "content": "...", "reasoning": "..."}
            {"type": "tool_call", "tool": "...", "arguments": "...", "id": "..."}
            {"type": "tool_result", "tool": "...", "id": "...", "result": "..."}
            {"type": "done", "response": "...", "history": [...]}
        """
        params = self._build_query_params(message, history)
        engine = self._build_graph_engine(project_dir)
        yield from engine.run_stream(params)

    # ---- chat -------------------------------------------------------

    def chat(self, message: str, history: list[dict], project_dir: str | None = None) -> tuple[str, list[dict]]:
        """Send a message, execute any tool calls, and return (response, display_history)."""
        response, full_messages, _, _, _ = self.chat_with_tools(message, history, project_dir)
        display_history = [m for m in full_messages if m["role"] in ("user", "assistant") and "tool_calls" not in m]
        return response, display_history


# New dual-agent architecture (2026-05)
from .base import BaseAgent, generate_default_configs  # noqa: E402, F401
from .user_agent import UserAgent  # noqa: E402
from .sys_agent import SysAgent  # noqa: E402
