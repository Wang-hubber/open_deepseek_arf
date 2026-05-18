# Dual Agent Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split single ARFAgent into UserAgent + SysAgent with a Dispatcher that handles automatic handoff, each with tailored prompts and tool sets.

**Architecture:** Extracts shared logic from ARFAgent into BaseAgent, creates UserAgent (user persona, restricted tools, classifier-enabled) and SysAgent (sys persona, full tools, fixed deep_thinking), adds a Dispatcher that runs User Graph first then optionally Sys Graph on handoff, adds two new system tools (handoff_to_sys, file_download), and extends file_writer/file_deleter with agent-mode path restrictions.

**Tech Stack:** Python 3.10+, LangGraph, existing ARF framework

---

## File Structure

```
New files:
  src/arf/agent/base.py                                    # BaseAgent — shared logic
  src/arf/agent/user_agent.py                             # UserAgent
  src/arf/agent/sys_agent.py                              # SysAgent
  src/arf/engine/dispatcher.py                            # Two-phase orchestrator
  src/arf/resources/system/tools/handoff_to_sys/tool.yaml # Handoff tool spec
  src/arf/resources/system/tools/handoff_to_sys/function.py
  src/arf/resources/system/tools/file_download/tool.yaml  # Download tool spec
  src/arf/resources/system/tools/file_download/function.py
  tests/test_dual_agent.py                                # Tests

Modified files:
  src/arf/agent/__init__.py                               # Exports UserAgent, SysAgent
  src/arf/engine/state.py                                 # +agent_mode field
  src/arf/resources/system/tools/file_writer/function.py  # +_agent_mode restriction
  src/arf/resources/system/tools/file_deleter/function.py # +_agent_mode restriction
  src/arf/server/session_manager.py                       # Integrate Dispatcher
  src/arf/server/routes.py                                # +download endpoint
```

---

### Task 1: Add handoff_to_sys system tool

**Files:**
- Create: `src/arf/resources/system/tools/handoff_to_sys/tool.yaml`
- Create: `src/arf/resources/system/tools/handoff_to_sys/function.py`

- [ ] **Step 1: Write tool.yaml**

```yaml
name: handoff_to_sys
description: |
  当用户需要创建/修改/删除资源（工具、skill、模型），或需要写入 tools/skills/models 路径，
  或当前工具集无法满足请求时调用。调用后任务将转交给系统工程师 Agent。
parameters:
  type: object
  properties:
    intent:
      type: string
      description: "翻译后的用户意图，用中文描述"
    required_actions:
      type: array
      items:
        type: string
      description: "需要的具体动作列表，如 ['创建 tool', '写入 function.py', '激活 tool']"
    reason:
      type: string
      description: "无法处理的原因，如 '缺少 resource_loader 工具'"
  required:
    - intent
    - required_actions
```

- [ ] **Step 2: Write function.py**

```python
def execute(intent: str, required_actions: list, reason: str = "") -> dict:
    return {
        "ok": True,
        "handoff": True,
        "intent": intent,
        "required_actions": required_actions,
        "reason": reason,
    }
```

- [ ] **Step 3: Commit**

```bash
git add src/arf/resources/system/tools/handoff_to_sys/
git commit -m "feat: add handoff_to_sys system tool"
```

---

### Task 2: Add file_download system tool

**Files:**
- Create: `src/arf/resources/system/tools/file_download/tool.yaml`
- Create: `src/arf/resources/system/tools/file_download/function.py`

- [ ] **Step 1: Write tool.yaml**

```yaml
name: file_download
description: "将工作区文件生成可下载链接，用户点击即可下载查看"
parameters:
  type: object
  properties:
    path:
      type: string
      description: "文件路径（相对于工作区）"
    label:
      type: string
      description: "可选，链接显示名称，默认为文件名"
  required:
    - path
```

- [ ] **Step 2: Write function.py**

```python
from pathlib import Path


def execute(path: str, label: str = "", _workspace_dir: str = "") -> dict:
    ws = Path(_workspace_dir) if _workspace_dir else Path.cwd()
    p = (ws / path).resolve()

    if not str(p).startswith(str(ws.resolve())):
        return {"error": f"Path escapes workspace: {path}"}

    if not p.exists():
        return {"error": f"File not found: {path}"}

    if p.is_dir():
        return {"error": f"Cannot download directory: {path}"}

    display = label or p.name
    return {
        "ok": True,
        "path": str(p.relative_to(ws)),
        "filename": p.name,
        "label": display,
        "size": p.stat().st_size,
        "download_url": f"/api/download?file={p.relative_to(ws)}",
    }
```

- [ ] **Step 3: Commit**

```bash
git add src/arf/resources/system/tools/file_download/
git commit -m "feat: add file_download system tool for generating download links"
```

---

### Task 3: Add download API endpoint

**Files:**
- Modify: `src/arf/server/routes.py`

- [ ] **Step 1: Add download route after the upload route (before line 567)**

```python
@router.get("/download")
def download_file(file: str, mgr: SessionManager = Depends(get_mgr)):
    ws = mgr.workspace_dir
    target = (ws / file).resolve()
    if not str(target).startswith(str(ws.resolve())):
        raise HTTPException(status_code=400, detail="Path escapes workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/arf/server/routes.py
git commit -m "feat: add /api/download endpoint for file_download tool"
```

---

### Task 4: Extend AgentState with agent_mode

**Files:**
- Modify: `src/arf/engine/state.py`

- [ ] **Step 1: Add agent_mode to AgentState TypedDict (after current_model, line 58)**

```python
    # Model routing
    current_model: str  # "quick_thinking" | "deep_thinking"
    agent_mode: Optional[str]  # "user" | "sys"
    classification: Optional[str]  # "medium" | "complex"
```

- [ ] **Step 2: Add agent_mode default in default_state() (after current_model, line 96)**

```python
        "current_model": current_model,
        "agent_mode": "user",
        "classification": None,
```

- [ ] **Step 3: Commit**

```bash
git add src/arf/engine/state.py
git commit -m "feat: add agent_mode field to AgentState"
```

---

### Task 5: Add agent_mode path restrictions to file_writer and file_deleter

**Files:**
- Modify: `src/arf/resources/system/tools/file_writer/function.py`
- Modify: `src/arf/resources/system/tools/file_deleter/function.py`

- [ ] **Step 1: Modify file_writer/function.py — add restriction check at top of execute()**

```python
from pathlib import Path

USER_RESTRICTED_PREFIXES = ("tools/", "skills/", "models/")


def execute(path: str, content: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if path.lstrip("/").startswith(prefix):
                return {
                    "error": (
                        f"User Agent 无法写入 {path}。"
                        f"tools/, skills/, models/ 路径下的文件操作需要 Sys Agent。"
                        f"请调用 handoff_to_sys 转交任务。"
                    )
                }

    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        preview = content[:600]
        if len(content) > 600:
            preview += f"\n... ({len(content) - 600} more chars)"

        return {
            "ok": True,
            "path": str(p),
            "filename": p.name,
            "bytes": len(content),
            "preview": preview,
        }
    except Exception as e:
        return {"error": str(e)}
```

- [ ] **Step 2: Modify file_deleter/function.py — add restriction check at top of execute()**

```python
from pathlib import Path

USER_RESTRICTED_PREFIXES = ("tools/", "skills/", "models/")


def execute(path: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if path.lstrip("/").startswith(prefix):
                return {
                    "error": (
                        f"User Agent 无法删除 {path}。"
                        f"tools/, skills/, models/ 路径下的文件操作需要 Sys Agent。"
                        f"请调用 handoff_to_sys 转交任务。"
                    )
                }

    p = Path(path)
    try:
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if p.is_dir():
            return {"error": "Cannot delete directories: {path}"}
        deleted_path = p.with_name(p.name + "_deleted")
        p.rename(deleted_path)
        return {"ok": True, "path": str(p), "deleted_as": str(deleted_path)}
    except Exception as e:
        return {"error": str(e)}
```

- [ ] **Step 3: Commit**

```bash
git add src/arf/resources/system/tools/file_writer/function.py src/arf/resources/system/tools/file_deleter/function.py
git commit -m "feat: add _agent_mode path restrictions to file_writer and file_deleter"
```

---

### Task 6: Create BaseAgent — extract shared logic from ARFAgent

**Files:**
- Create: `src/arf/agent/base.py`

Extract shared code from `ARFAgent` into `BaseAgent`. UserAgent and SysAgent will extend this.

- [ ] **Step 1: Write src/arf/agent/base.py**

```python
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
    """Shared agent foundation — prompt pipeline, tool building, GraphEngine wiring.

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
```

- [ ] **Step 2: Commit**

```bash
git add src/arf/agent/base.py
git commit -m "refactor: extract BaseAgent from ARFAgent with shared prompt pipeline and engine wiring"
```

---

### Task 7: Create UserAgent

**Files:**
- Create: `src/arf/agent/user_agent.py`

- [ ] **Step 1: Write src/arf/agent/user_agent.py**

```python
"""UserAgent — personal assistant persona with restricted tools and classifier routing."""

import os
from .base import BaseAgent


class UserAgent(BaseAgent):
    """User-facing agent: read files, browse web, process tasks, handoff to sys."""

    AGENT_MODE = "user"

    # Kernel tools for user agent: read-only + task tools + handoff
    KERNEL_TOOLS = frozenset({
        "file_reader", "file_writer", "file_deleter", "file_download",
        "memory_store", "handoff_to_sys",
        "web_fetch", "web_search",
        "image_understanding", "ocr",
        "speech_understanding", "speech_output", "video_understanding",
    })

    PROMPT_PIPELINE: list[tuple[int, str, str]] = [
        (10, "workspace",        "_workspace_section"),
        (15, "long_term_memory", "_long_term_memory_section"),
        (20, "memory",           "_memory_section"),
        (25, "critical_rules",   "_critical_rules_section"),
        (30, "identity",         "_identity_section"),
        (50, "inventory",        "_inventory_section"),
        (60, "language",         "_language_instruction"),
    ]

    def _identity_section(self) -> str:
        return (
            "You are ARF Agent, a personal assistant. "
            "You help users accomplish tasks through natural language conversation. "
            "You can read files, browse the web, manage memory, and process documents.\n\n"
            "## Path System\n\n"
            "Two path spaces govern all file access:\n\n"
            "| Prefix | Target | Access | Example |\n"
            "|--------|--------|--------|---------|\n"
            "| `@sys/` | Framework built-in resources | **read-only** | `@sys/tools/file_reader/function.py` |\n"
            "| _(no prefix)_ | User workspace | read + write | `uploads/report.pdf` |\n\n"
            "Relative paths (no `@sys/`) resolve against the user's workspace root.\n"
            "- Uploads: `uploads/<filename>`\n"
            "- Output files: any path not under `tools/`, `skills/`, `models/`\n"
            "- Memory: `memory/session.md`, `memory/long_term.md`\n\n"
            "## File Operations\n\n"
            "You can read, write, and delete files in the user workspace. "
            "Use `file_download` to give the user a downloadable link to a file.\n\n"
            "## Intent Translation\n\n"
            "Users rarely state technical actions directly. Translate their words "
            "into potential actions:\n"
            "- \"Can you...\" / \"I want...\" → may involve creating resources\n"
            "- \"Help me think of...\" / \"Is there a way...\" → may involve discovery or creation\n"
            "- \"Change...\" / \"Add a feature...\" → involves modifying resources\n"
            "- \"Why is this tool...\" / \"How do I use...\" → read-only, you can handle\n"
            "- \"Help me tweak this result...\" → file modification, use file_writer\n\n"
            "For each potential action, check whether you have the required tool. "
            "If ANY action requires a tool you don't have → call `handoff_to_sys`.\n\n"
            "## File Writer / Deleter Path Restrictions\n"
            "You CAN use file_writer and file_deleter for user files "
            "(uploads/, output/, data/, and general workspace files). "
            "But if the target path is under tools/, skills/, or models/, "
            "or involves resource creation/registration/activation, "
            "you MUST call `handoff_to_sys` instead.\n\n"
            "## Handoff\n\n"
            "Call `handoff_to_sys` when:\n"
            "- User asks to create/modify/delete a tool, skill, or model\n"
            "- You need to write to tools/, skills/, or models/ paths\n"
            "- You need resource_loader, resource_registrar, model_manager, "
            "model_switch, or manage_hooks\n"
            "- Any task your current toolset cannot fulfill\n\n"
            "When calling handoff_to_sys, provide the translated intent, "
            "required actions, and why you can't handle it.\n\n"
            "## Memory Management\n"
            "- \"remember this\" → `memory_store` action `write`\n\n"
            "## Guidelines\n"
            "- Keep responses concise and actionable.\n"
            "- When a tool can handle a request, call it directly.\n"
            "- Verify before answering — call tools, don't guess."
        )

    def _classifier_enabled(self) -> bool:
        return os.environ.get("ARF_CLASSIFIER_ENABLED", "").lower() in ("1", "true", "yes")
```

- [ ] **Step 2: Commit**

```bash
git add src/arf/agent/user_agent.py
git commit -m "feat: add UserAgent with restricted tools, intent translation, and handoff guidance"
```

---

### Task 8: Create SysAgent

**Files:**
- Create: `src/arf/agent/sys_agent.py`

- [ ] **Step 1: Write src/arf/agent/sys_agent.py**

```python
"""SysAgent — coding/R&D persona with full tools, fixed deep_thinking, resource orchestration."""

from .base import BaseAgent


class SysAgent(BaseAgent):
    """System engineer agent: resource creation, tool orchestration, model management."""

    AGENT_MODE = "sys"

    # Kernel tools: all framework tools (everything)
    KERNEL_TOOLS = frozenset({
        "file_reader", "file_writer", "file_deleter", "file_download",
        "resource_loader", "memory_store", "model_manager",
        "resource_registrar", "model_switch", "manage_hooks",
        "web_fetch", "web_search",
        "image_understanding", "ocr",
        "speech_understanding", "speech_output", "video_understanding",
    })

    PROMPT_PIPELINE: list[tuple[int, str, str]] = [
        (10, "workspace",        "_workspace_section"),
        (15, "long_term_memory", "_long_term_memory_section"),
        (20, "memory",           "_memory_section"),
        (25, "critical_rules",   "_critical_rules_section"),
        (30, "identity",         "_identity_section"),
        (50, "inventory",        "_inventory_section"),
        (60, "language",         "_language_instruction"),
    ]

    def _identity_section(self) -> str:
        return (
            "You are ARF System Engineer, a workspace builder. "
            "You create and orchestrate tools, skills, and models. "
            "You have full access to all framework tools.\n\n"
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
            "1. **Kernel tools** are always active.\n"
            "2. **Skills** are listed under Available Resources with name + description. "
            "When a skill matches the user's intent, read it via `file_reader`:\n"
            "   - System skill: `@sys/skills/<name>/skill.yaml`\n"
            "   - User skill: `skills/<name>/skill.yaml`\n"
            "3. **Activate tools** with `resource_loader` action `activate` after reading "
            "a skill that requires them.\n"
            "4. User tools are never pre-loaded — discover and activate them through skills.\n\n"
            "## Resource Creation — Gated Workflow\n\n"
            "**This is a strict sequence. Do NOT skip or reorder steps.**\n\n"
            "### Gate 1 — Design\n"
            "Read `@sys/skills/resource_scaffold/skill.yaml` for the format spec.\n"
            "Present the design to the user: tool/skill name, parameters, workflow.\n"
            "**STOP here.** Wait for the user to say \"go ahead\", \"yes\", \"确认\", etc.\n"
            "Do NOT call file_writer yet.\n\n"
            "### Gate 2 — Write\n"
            "Only after explicit user approval, call file_writer to create each file.\n"
            "The result includes a content preview for the user to review.\n"
            "After writing, the registry auto-reloads.\n\n"
            "### Gate 3 — Validate\n"
            "Read `@sys/skills/validate_tool/skill.yaml` and follow its checklist.\n"
            "Verify: tool.yaml parses, function.py imports, execute() is callable.\n\n"
            "### Gate 4 — Activate\n"
            "Call `resource_loader` action `activate` with the new tool name.\n"
            "If activation succeeds, the tool is ready. Inform the user.\n\n"
            "**Hard rules:**\n"
            "- Gate 1 is the checkpoint. Never skip it.\n"
            "- file_writer is ONLY allowed after the user explicitly approves the design.\n"
            "- Use Python stdlib only unless the user specifies dependencies.\n"
            "- All execute() functions must return a dict.\n\n"
            "## Error Handling\n\n"
            "Tool returned `error` → read `@sys/skills/error_handler/skill.yaml`.\n\n"
            "## Memory Management\n"
            "- \"remember this\" → `memory_store` action `write`\n"
            "- `compression_needed: true` → read `@sys/skills/memory_management/skill.yaml`\n\n"
            "## Model Management\n"
            "Use `model_manager` tool for model configs (list / create / test / switch). "
            "NEVER write `models/*/config.yaml` directly.\n\n"
            "## Model Switching\n"
            "This agent runs on `deep_thinking` (maximum reasoning). "
            "Use `model_switch` if the task requires a different model tier.\n\n"
            "## Guidelines\n"
            "- Reference resource names and paths when answering.\n"
            "- When a tool can handle a request, call it directly.\n"
            "- Keep responses concise and actionable.\n"
            "- Always include the tool name in backticks when discussing resources."
        )

    def _classifier_enabled(self) -> bool:
        # Sys Agent always uses deep_thinking, no classifier
        return False
```

- [ ] **Step 2: Commit**

```bash
git add src/arf/agent/sys_agent.py
git commit -m "feat: add SysAgent with full tools, resource orchestration, fixed deep_thinking"
```

---

### Task 9: Update agent __init__.py exports

**Files:**
- Modify: `src/arf/agent/__init__.py`

- [ ] **Step 1: Rewrite src/arf/agent/__init__.py**

```python
"""ARF Agents — conversational orchestration layer.

UserAgent: personal assistant, restricted tools, classifier routing.
SysAgent:  system engineer, full tools, fixed deep_thinking.
ARFAgent:  legacy single agent (kept for backward compatibility).
"""

from .base import BaseAgent
from .user_agent import UserAgent
from .sys_agent import SysAgent

# Re-export ARFAgent from base for backward compat — delegates to SysAgent behavior
# Existing code that creates ARFAgent directly will keep working.
# New code should use UserAgent / SysAgent / Dispatcher.
```

And keep the existing `ARFAgent` class in the same file (or a separate `legacy.py`). Actually the simplest approach is to keep ARFAgent in __init__.py as a backwards-compat wrapper. Let me reconsider — the spec says "split ARFAgent" but we shouldn't break existing callers. Better to keep ARFAgent as-is for backward compat, and add the new classes alongside.

Let me adjust: keep existing `__init__.py` but add the new imports.

Actually, looking more carefully, ARFAgent is imported by SessionManager and routes.py. We'll update SessionManager to use Dispatcher. But for safety, let's keep ARFAgent intact and just add the new exports.

```python
"""ARF Agents — conversational orchestration layer.

ARFAgent:  legacy single agent (backward compatible).
UserAgent: personal assistant, restricted tools, classifier routing.
SysAgent:  system engineer, full tools, fixed deep_thinking.
"""

import copy
import json
import logging
import os
from pathlib import Path

from ..engine import GraphParams, GraphEngine
from ..resources.manager import ResourceRegistry

logger = logging.getLogger("arf.agent")
from ..resources.model_adapter import ModelAdapter

# Keep existing ARFAgent class as-is (it's the full class from the current __init__.py)
# ... (ARFAgent stays, not shown for brevity)

# New exports
from .base import BaseAgent
from .user_agent import UserAgent
from .sys_agent import SysAgent
```

Wait, this is getting complicated. The existing `__init__.py` has 648 lines. Let me just keep it as-is and add the new imports at the bottom. The `_trim_schema` function is defined both in `base.py` and in the old `__init__.py`. We need to handle that.

OK, simplest approach for the plan:
1. Keep the existing `__init__.py` unchanged (backward compat)
2. Add the new imports at the bottom
3. In a follow-up, clean up duplication between ARFAgent and BaseAgent

```python
# At the bottom of existing __init__.py, add:
from .base import BaseAgent
from .user_agent import UserAgent
from .sys_agent import SysAgent
```

- [ ] **Step 1: Add imports to existing src/arf/agent/__init__.py**

Add these lines at the end of the file (after the ARFAgent class):

```python
# New dual-agent architecture (2026-05)
from .base import BaseAgent  # noqa: E402, F401 — imported for public API
from .user_agent import UserAgent  # noqa: E402
from .sys_agent import SysAgent  # noqa: E402
```

- [ ] **Step 2: Commit**

```bash
git add src/arf/agent/__init__.py
git commit -m "feat: export BaseAgent, UserAgent, SysAgent alongside legacy ARFAgent"
```

---

### Task 10: Create Dispatcher

**Files:**
- Create: `src/arf/engine/dispatcher.py`

- [ ] **Step 1: Write src/arf/engine/dispatcher.py**

```python
"""Dispatcher — two-phase agent orchestrator.

Runs UserAgent graph first. If UserAgent calls handoff_to_sys,
runs SysAgent graph with full context and returns its result.
"""

import json
import logging
from typing import Any

from .graph import GraphParams, GraphResult

logger = logging.getLogger("arf.engine.dispatcher")

DEFAULT_USER_MAX_TURNS = 6
DEFAULT_SYS_MAX_TURNS = 10


class Dispatcher:
    """Two-phase agent orchestrator.

    Phase 1: UserAgent graph (user persona, restricted tools, classifier-enabled)
    Phase 2: SysAgent graph (sys persona, all tools, fixed deep_thinking)
             — only invoked if UserAgent calls handoff_to_sys.
    """

    def __init__(self, user_agent, sys_agent):
        self.user_agent = user_agent
        self.sys_agent = sys_agent

    def run(self, message: str, history: list[dict],
            project_dir: str | None = None) -> GraphResult:
        """Non-streaming dispatch. Runs Phase 1, optionally Phase 2."""

        total_max = DEFAULT_SYS_MAX_TURNS

        # Phase 1: User Agent
        user_result = self._run_phase(
            self.user_agent, message, history, project_dir,
            max_turns=min(DEFAULT_USER_MAX_TURNS, total_max),
        )

        if not self._detect_handoff(user_result.tool_events):
            return user_result

        # Phase 2: Sys Agent
        handoff = self._extract_handoff(user_result.tool_events)
        sys_history = self._build_sys_history(
            history, message, user_result, handoff
        )
        sys_message = self._build_handoff_message(message, handoff)
        remaining_turns = max(1, total_max - user_result.turns)

        sys_result = self._run_phase(
            self.sys_agent, sys_message, sys_history, project_dir,
            max_turns=remaining_turns,
        )

        # Merge: use Sys response but include full history
        return GraphResult(
            response=sys_result.response,
            history=sys_result.history,
            tool_events=user_result.tool_events + sys_result.tool_events,
            transition_log=user_result.transition_log + sys_result.transition_log,
            turns=user_result.turns + sys_result.turns,
            truncated=sys_result.truncated,
            usage=_merge_usage(user_result.usage, sys_result.usage),
        )

    def run_stream(self, message: str, history: list[dict],
                   project_dir: str | None = None) -> Any:
        """Streaming dispatch. Emits events from Phase 1, then Phase 2 on handoff."""

        total_max = DEFAULT_SYS_MAX_TURNS
        user_max = min(DEFAULT_USER_MAX_TURNS, total_max)

        # Phase 1: Stream User Agent
        handoff_info = None
        user_events = []
        user_turns = 0
        user_usage = None
        user_traces = []

        for event in self.user_agent.chat_stream_with_tools(
            message, history, project_dir, max_turns=user_max,
        ):
            etype = event.get("type", "")
            user_events.append(event)

            if etype == "done":
                user_turns = event.get("turns", user_max)
                user_usage = event.get("usage", {})
                user_traces = event.get("traces", [])
                # Check for handoff across all accumulated tool events
                # (tool_results are interleaved, we check on done)
                if self._detect_handoff_from_events(user_events):
                    handoff_info = self._extract_handoff_from_events(user_events)
                if not handoff_info:
                    yield event
                    return
                # Don't yield done yet — we continue with Phase 2
                continue

            if etype == "error":
                yield event
                # Don't handoff on error
                yield {"type": "done", "response": event.get("detail", "Error"),
                       "history": history, "error": True}
                return

            yield event

        if not handoff_info:
            return

        # Phase 2: Emit handoff event, then stream Sys Agent
        yield {
            "type": "handoff",
            "from": "user_agent",
            "to": "sys_agent",
            "intent": handoff_info.get("intent", ""),
        }

        sys_history = self._build_sys_history_from_stream(
            history, message, user_events, handoff_info
        )
        sys_message = self._build_handoff_message(message, handoff_info)
        remaining_turns = max(1, total_max - user_turns)

        for event in self.sys_agent.chat_stream_with_tools(
            sys_message, sys_history, project_dir, max_turns=remaining_turns,
        ):
            yield event

    # ---- helpers -------------------------------------------------------

    def _run_phase(self, agent, message, history, project_dir, max_turns):
        """Run one agent phase, return GraphResult-compatible object."""
        response, full_history, tool_events, usage, traces = agent.chat_with_tools(
            message, history, project_dir, max_turns=max_turns,
        )
        # Count turns from the trace log
        turns = len(traces) if traces else 1
        return GraphResult(
            response=response,
            history=full_history,
            tool_events=tool_events,
            transition_log=traces,
            turns=turns,
            truncated=False,
            usage=usage,
        )

    @staticmethod
    def _detect_handoff(tool_events: list[dict]) -> bool:
        for te in tool_events:
            if te.get("type") == "tool_result" and te.get("tool") == "handoff_to_sys":
                try:
                    result = json.loads(te.get("result", "{}"))
                    if result.get("handoff"):
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
        return False

    @staticmethod
    def _detect_handoff_from_events(events: list[dict]) -> bool:
        for e in events:
            if e.get("type") == "tool_result" and e.get("tool") == "handoff_to_sys":
                try:
                    result = json.loads(e.get("result", "{}"))
                    if result.get("handoff"):
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
        return False

    @staticmethod
    def _extract_handoff(tool_events: list[dict]) -> dict:
        for te in tool_events:
            if te.get("type") == "tool_result" and te.get("tool") == "handoff_to_sys":
                try:
                    return json.loads(te.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
        return {}

    @staticmethod
    def _extract_handoff_from_events(events: list[dict]) -> dict:
        for e in events:
            if e.get("type") == "tool_result" and e.get("tool") == "handoff_to_sys":
                try:
                    return json.loads(e.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
        return {}

    @staticmethod
    def _build_sys_history(original_history: list[dict], original_msg: str,
                           user_result: GraphResult, handoff: dict) -> list[dict]:
        """Build history for Sys Agent: original history + user message.

        The handoff message (sent as the new user message) carries
        intent, required_actions, reason, and original user message --
        sufficient context for Sys Agent to start without User Agent's
        intermediate tool calls.
        """
        history = list(original_history)
        history.append({"role": "user", "content": original_msg})
        return history

    @staticmethod
    def _build_sys_history_from_stream(original_history: list[dict],
                                        original_msg: str,
                                        user_events: list[dict],
                                        handoff: dict) -> list[dict]:
        """Build history from stream events -- same simple approach."""
        history = list(original_history)
        history.append({"role": "user", "content": original_msg})
        return history

    @staticmethod
    def _build_handoff_message(original_msg: str, handoff: dict) -> str:
        intent = handoff.get("intent", "")
        actions = handoff.get("required_actions", [])
        reason = handoff.get("reason", "")
        return (
            f"[Handoff from User Agent]\n"
            f"意图: {intent}\n"
            f"需要动作: {', '.join(actions) if actions else '无'}\n"
            f"原因: {reason}\n"
            f"原始用户消息: {original_msg}"
        )


def _merge_usage(a: dict | None, b: dict | None) -> dict:
    result = {}
    for u in (a, b):
        if u:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                result[k] = result.get(k, 0) + u.get(k, 0)
    return result
```

- [ ] **Step 2: Commit**

```bash
git add src/arf/engine/dispatcher.py
git commit -m "feat: add Dispatcher for two-phase User→Sys agent orchestration"
```

---

### Task 11: Integrate Dispatcher into SessionManager

**Files:**
- Modify: `src/arf/server/session_manager.py`

- [ ] **Step 1: Update get_agent() in SessionManager to return a Dispatcher**

Replace the `get_agent` method (lines 69-99) with a version that creates UserAgent, SysAgent, and wraps them in a Dispatcher:

```python
    def get_agent(self):
        """Return a Dispatcher wrapping UserAgent + SysAgent, auto-invalidating
        if the underlying model config file changed."""
        agent_yaml = self.read_agent_yaml()
        preferred_name = (agent_yaml.get("agent") or {}).get("model")
        resolved = self.resolve_model_config(preferred_name)

        if not resolved:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail="No model configured. Please configure at least one model.",
            )

        model_name, model_config = resolved
        config_path = self.workspace_dir / "models" / model_name / "config.yaml"
        current_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0

        if self._agent is not None and current_mtime != self._agent_mtime:
            self.reset_resource_state()
        if self._agent is None:
            from ..agent import BaseAgent, UserAgent, SysAgent
            from ..engine import Dispatcher
            from ..resources.model_adapter import ModelAdapter

            items = self.get_registry()._items.get("models", {})
            item = items.get(model_name, {})
            ctx = item.get("context_window", 1048576)

            # User Agent model
            user_model = ModelAdapter(model_config, context_window=ctx)
            language = self._load_language()
            user_agent = UserAgent(user_model, self.get_registry(),
                                   str(self.workspace_dir), language,
                                   hook_runner=self.get_hook_runner())

            # Sys Agent model — resolve deep_thinking specifically
            sys_resolved = self.resolve_model_config("deep_thinking")
            if sys_resolved:
                sys_name, sys_config = sys_resolved
                sys_item = items.get(sys_name, {})
                sys_ctx = sys_item.get("context_window", 1048576)
                sys_model = ModelAdapter(sys_config, context_window=sys_ctx)
            else:
                # Fallback to same model
                sys_model = user_model

            sys_agent = SysAgent(sys_model, self.get_registry(),
                                 str(self.workspace_dir), language,
                                 hook_runner=self.get_hook_runner())

            self._agent = Dispatcher(user_agent, sys_agent)
            self._agent_mtime = current_mtime
        return self._agent
```

- [ ] **Step 2: The Dispatcher's `run` and `run_stream` are called via the existing API**

The routes.py at line 449 already calls `agent.chat_with_tools(...)` and at line 773 calls `agent.chat_stream_with_tools(...)`. The Dispatcher exposes these same methods. But the `chat_with_tools` return type is different — the Dispatcher returns a `GraphResult` but the route expects `(response, full_messages, _, usage, traces)`.

Add adapter methods to Dispatcher to match the ARFAgent interface:

In `dispatcher.py`, add:

```python
    def chat_with_tools(self, message: str, history: list[dict],
                        project_dir: str | None = None):
        """ARFAgent-compatible interface for routes.py."""
        result = self.run(message, history, project_dir)
        return (
            result.response,
            result.history,
            result.tool_events,
            result.usage,
            result.transition_log,
        )

    def chat_stream_with_tools(self, message: str, history: list[dict],
                               project_dir: str | None = None):
        """ARFAgent-compatible streaming interface for routes.py."""
        yield from self.run_stream(message, history, project_dir)

    @property
    def model(self):
        """Expose model for usage tracking in routes.py (_stream_chat line 787)."""
        return self.user_agent.model
```

- [ ] **Step 3: Commit**

```bash
git add src/arf/engine/dispatcher.py src/arf/server/session_manager.py
git commit -m "feat: integrate Dispatcher into SessionManager with ARFAgent-compatible API"
```

---

### Task 12: Tests

**Files:**
- Create: `tests/test_dual_agent.py`

- [ ] **Step 1: Write tests/test_dual_agent.py**

```python
"""Tests for dual-agent architecture: Dispatcher, handoff, tool partitioning."""

import json
import pytest
from pathlib import Path


class TestHandoffTool:
    """handoff_to_sys tool should return handoff marker."""

    def test_handoff_returns_marker(self):
        from arf.resources.system.tools.handoff_to_sys.function import execute
        result = execute(
            intent="创建天气查询工具",
            required_actions=["创建 tool", "写入 function.py"],
            reason="缺少 resource_loader",
        )
        assert result["ok"] is True
        assert result["handoff"] is True
        assert result["intent"] == "创建天气查询工具"
        assert len(result["required_actions"]) == 2

    def test_handoff_reason_optional(self):
        from arf.resources.system.tools.handoff_to_sys.function import execute
        result = execute(
            intent="测试",
            required_actions=["test"],
        )
        assert result["ok"] is True
        assert result["reason"] == ""


class TestFileDownloadTool:
    """file_download should generate download info for workspace files."""

    def test_download_existing_file(self, tmp_path):
        from arf.resources.system.tools.file_download.function import execute
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = execute(path="test.txt", _workspace_dir=str(tmp_path))
        assert result["ok"] is True
        assert result["filename"] == "test.txt"
        assert "/api/download" in result["download_url"]

    def test_download_missing_file(self, tmp_path):
        from arf.resources.system.tools.file_download.function import execute
        result = execute(path="nonexistent.txt", _workspace_dir=str(tmp_path))
        assert "error" in result

    def test_download_with_label(self, tmp_path):
        from arf.resources.system.tools.file_download.function import execute
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        result = execute(path="data.csv", label="数据文件", _workspace_dir=str(tmp_path))
        assert result["ok"] is True
        assert result["label"] == "数据文件"


class TestFileWriterRestrictions:
    """file_writer should reject User Agent writes to resource paths."""

    def test_user_mode_rejects_tools_path(self, tmp_path):
        from arf.resources.system.tools.file_writer.function import execute
        result = execute(
            path="tools/weather/function.py",
            content="# test",
            _agent_mode="user",
        )
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_rejects_skills_path(self, tmp_path):
        from arf.resources.system.tools.file_writer.function import execute
        result = execute(
            path="skills/test/skill.yaml",
            content="name: test",
            _agent_mode="user",
        )
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_rejects_models_path(self, tmp_path):
        from arf.resources.system.tools.file_writer.function import execute
        result = execute(
            path="models/test/config.yaml",
            content="name: test",
            _agent_mode="user",
        )
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_allows_regular_path(self, tmp_path):
        from arf.resources.system.tools.file_writer.function import execute
        p = tmp_path / "output" / "report.md"
        result = execute(
            path=str(p),
            content="# Report",
            _agent_mode="user",
        )
        assert result["ok"] is True
        assert p.exists()

    def test_sys_mode_allows_tools_path(self, tmp_path):
        from arf.resources.system.tools.file_writer.function import execute
        p = tmp_path / "tools" / "test" / "function.py"
        result = execute(
            path=str(p),
            content="# test",
            _agent_mode="sys",
        )
        assert result["ok"] is True
        assert p.exists()


class TestFileDeleterRestrictions:
    """file_deleter should reject User Agent deletes on resource paths."""

    def test_user_mode_rejects_tools_delete(self, tmp_path):
        from arf.resources.system.tools.file_deleter.function import execute
        p = tmp_path / "tools" / "test.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("test")
        result = execute(path=str(p), _agent_mode="user")
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_sys_mode_allows_tools_delete(self, tmp_path):
        from arf.resources.system.tools.file_deleter.function import execute
        p = tmp_path / "tools" / "test.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("test")
        result = execute(path=str(p), _agent_mode="sys")
        assert result["ok"] is True


class TestDispatcherHandoffDetection:
    """Dispatcher should detect handoff_to_sys in tool events."""

    def test_detect_handoff_positive(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_call", "tool": "file_reader", "arguments": "{}", "id": "1"},
            {"type": "tool_result", "tool": "file_reader", "id": "1",
             "result": '{"ok": true}'},
            {"type": "tool_call", "tool": "handoff_to_sys",
             "arguments": '{"intent":"test","required_actions":["create"]}', "id": "2"},
            {"type": "tool_result", "tool": "handoff_to_sys", "id": "2",
             "result": '{"ok":true,"handoff":true,"intent":"test","required_actions":["create"]}'},
        ]
        assert Dispatcher._detect_handoff(events) is True

    def test_detect_handoff_negative(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_call", "tool": "file_reader", "arguments": "{}", "id": "1"},
            {"type": "tool_result", "tool": "file_reader", "id": "1",
             "result": '{"ok": true}'},
        ]
        assert Dispatcher._detect_handoff(events) is False

    def test_extract_handoff_params(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_result", "tool": "handoff_to_sys", "id": "1",
             "result": '{"ok":true,"handoff":true,"intent":"创建工具","required_actions":["write"],"reason":"test"}'},
        ]
        info = Dispatcher._extract_handoff(events)
        assert info["intent"] == "创建工具"
        assert info["required_actions"] == ["write"]


class TestAgentToolPartitioning:
    """UserAgent and SysAgent should have correct tool sets."""

    def test_user_agent_has_no_resource_loader(self):
        from arf.agent.user_agent import UserAgent
        assert "resource_loader" not in UserAgent.KERNEL_TOOLS
        assert "resource_registrar" not in UserAgent.KERNEL_TOOLS
        assert "model_manager" not in UserAgent.KERNEL_TOOLS
        assert "model_switch" not in UserAgent.KERNEL_TOOLS
        assert "manage_hooks" not in UserAgent.KERNEL_TOOLS

    def test_user_agent_has_handoff(self):
        from arf.agent.user_agent import UserAgent
        assert "handoff_to_sys" in UserAgent.KERNEL_TOOLS

    def test_user_agent_has_file_tools(self):
        from arf.agent.user_agent import UserAgent
        assert "file_reader" in UserAgent.KERNEL_TOOLS
        assert "file_writer" in UserAgent.KERNEL_TOOLS
        assert "file_deleter" in UserAgent.KERNEL_TOOLS
        assert "file_download" in UserAgent.KERNEL_TOOLS

    def test_sys_agent_has_all_kernel_tools(self):
        from arf.agent.sys_agent import SysAgent
        sys_tools = {"resource_loader", "resource_registrar", "model_manager",
                     "model_switch", "manage_hooks"}
        assert sys_tools.issubset(SysAgent.KERNEL_TOOLS)

    def test_sys_agent_no_handoff(self):
        from arf.agent.sys_agent import SysAgent
        assert "handoff_to_sys" not in SysAgent.KERNEL_TOOLS


class TestAgentMode:
    """Agent modes should be correct."""

    def test_user_agent_mode(self):
        from arf.agent.user_agent import UserAgent
        assert UserAgent.AGENT_MODE == "user"

    def test_sys_agent_mode(self):
        from arf.agent.sys_agent import SysAgent
        assert SysAgent.AGENT_MODE == "sys"
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_dual_agent.py -v
```

Expected: all tests pass (note: FileWriter/Deleter tests write to tmp_path so they should pass without a full server)

- [ ] **Step 3: Commit**

```bash
git add tests/test_dual_agent.py
git commit -m "test: add dual-agent architecture tests"
```

---

### Task 13: Integration verification

- [ ] **Step 1: Verify imports work**

```bash
cd /home/wangxie/open_deepseek_arf && python -c "
from arf.agent import BaseAgent, UserAgent, SysAgent, ARFAgent
from arf.engine.dispatcher import Dispatcher
from arf.engine.state import AgentState, default_state
print('All imports OK')
print('AgentState fields:', [k for k in AgentState.__annotations__])
"
```

Expected: "All imports OK" + agent_mode in AgentState fields

- [ ] **Step 2: Verify tools are discoverable by registry**

```bash
cd /home/wangxie/open_deepseek_arf && python -c "
from arf.resources.manager import ResourceRegistry
from pathlib import Path
import arf.resources.system
sys_dir = Path(arf.resources.system.__file__).parent
r = ResourceRegistry()
r.load(str(sys_dir))
assert 'handoff_to_sys' in r._items['tools'], 'handoff_to_sys not found'
assert 'file_download' in r._items['tools'], 'file_download not found'
print('Both tools registered OK')
print('handoff_to_sys schema:', r._items['tools']['handoff_to_sys'].get('json_schema', {}))
print('file_download schema:', r._items['tools']['file_download'].get('json_schema', {}))
"
```

Expected: Both tools registered with valid schemas

- [ ] **Step 3: Verify UserAgent and SysAgent prompt construction**

```bash
cd /home/wangxie/open_deepseek_arf && python -c "
from arf.resources.manager import ResourceRegistry
from arf.resources.model_adapter import ModelAdapter
from arf.agent.user_agent import UserAgent
from arf.agent.sys_agent import SysAgent

# minimal model config
cfg = {'base_url': 'http://localhost', 'api_key': 'test', 'model_name': 'test'}
model = ModelAdapter(cfg)
r = ResourceRegistry()

user = UserAgent(model, r)
sys = SysAgent(model, r)

user_prompt = user.build_system_prompt()
sys_prompt = sys.build_system_prompt()

assert 'personal assistant' in user_prompt.lower() or '助手' in user_prompt
assert 'handoff_to_sys' in user_prompt
assert 'Intent Translation' in user_prompt or '意图' in user_prompt

assert 'System Engineer' in sys_prompt or '系统工程师' in sys_prompt
assert 'Gate 1' in sys_prompt
assert 'resource_loader' in sys_prompt

print('User prompt length:', len(user_prompt))
print('Sys prompt length:', len(sys_prompt))
print('All prompt assertions passed')
"
```

Expected: All assertions pass, prompt lengths reported

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: integration verification for dual-agent architecture"
```

---

## Post-Implementation

After all tasks complete:
1. Run `pytest tests/ -v` to verify no regressions
2. Start the dev server and test a real conversation with both user-task and resource-creation messages
3. Verify the handoff transition appears correctly in streaming responses
4. Verify file_download generates working download links
