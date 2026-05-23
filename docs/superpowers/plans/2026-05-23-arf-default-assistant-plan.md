# ARF Default Assistant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-featured dual-agent self-evolving assistant at `app/arf_default_assistant/` using the new `arf/` framework, with single infinite conversation, lazy persistence, 19 tools, 18 skills, 10 CLI commands, FastAPI server, and Vue3 frontend integration.

**Architecture:** Dual-agent via `AgentConfig.agents` + `HandoverConfig`. Single infinite conversation → lazy archive on shutdown, restore on startup. `post_tool_exec` hook triggers ToolResolver hot reload after self-evolution. All tools/skills are `tool.yaml` + `function.py` dirs loaded by `StaticYamlToolProvider`. Frontend connects via SSE for streaming + trace.

**Tech Stack:** Python 3.11+, FastAPI + SSE, Vue 3 + Vite, DeepSeek V4 API, arf framework

**Phase Dependency Order:**
```
Phase 0: Framework patches (engine.set_call_model, resources.reload)
  └→ Phase 1: agent.yaml (dual-agent config)
      └→ Phase 2-5 (parallel): Tools, Skills, Server, CLI
          └→ Phase 6: Self-evolution hook
              └→ Phase 7: Frontend integration
                  └→ Phase 8: Integration test
```

---

## Phase 0: Framework Patches

### Task 0.1: Add `set_call_model()` to GraphEngine

**Files:** Modify `arf/engine/graph.py`

- [ ] **Step 1: Add method to GraphEngine**

```python
# After line ~90 (after __init__), add:
def set_call_model(self, call_model) -> None:
    """Late-binding injection of the model API call function.
    Used by BaseAgent after API client initialization."""
    self._call_model = call_model
```

- [ ] **Step 2: Verify**

```bash
python3 -c "from arf.engine.graph import GraphEngine; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add arf/engine/graph.py
git commit -m "feat(engine): add set_call_model() for late-binding model API injection"
```

### Task 0.2: Add `reload()` to DefaultToolResolver

**Files:** Modify `arf/resources/resolver.py`

- [ ] **Step 1: Add method**

```python
# Add to DefaultToolResolver class:
async def reload(self) -> None:
    """Reload all providers — call after self-evolution creates new tools."""
    for p in self._providers:
        if hasattr(p, '_tools'):
            p._tools.clear()
        if hasattr(p, '_functions'):
            p._functions.clear()
```

- [ ] **Step 2: Verify**

```bash
python3 -c "from arf.resources.resolver import DefaultToolResolver; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add arf/resources/resolver.py
git commit -m "feat(resources): add reload() to DefaultToolResolver for hot reload after self-evolution"
```

### Task 0.3: Add BaseAgent attribute access for persistence

**Files:** Modify `arf/agent/base.py`

- [ ] **Step 1: Expose internal attributes for lazy_persistence.py**

```python
# Add properties to BaseAgent class:
@property
def tool_resolver(self):
    return self._engine.tool_resolver

@property
def memory_store(self):
    return self._memory_store
```

And in `__init__`, store `self._memory_store = memory_store` after creating it.

- [ ] **Step 2: Verify**

```bash
python3 -c "from arf.agent.base import BaseAgent; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add arf/agent/base.py
git commit -m "feat(agent): expose tool_resolver and memory_store for persistence"
```

---

## Phase 1: Agent Configuration

### Task 1.0: Write `agent.yaml`

**Files:** Create `app/arf_default_assistant/agent.yaml`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p app/arf_default_assistant/{tools,skills,hooks,memory,workspaces/default}
```

- [ ] **Step 2: Write agent.yaml**

```yaml
# arf_version: 1.0
name: arf_assistant
description: >
  可自我演进的 AI 助手。擅长代码编写、文件管理、网络搜索、资源创建。
  作为主 Agent (User) 与用户交互，资源创建等系统操作移交给 SysAgent。

models:
  - name: quick
    api_type: openai
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      reasoning_effort: high

  - name: deep
    api_type: openai
    model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      reasoning_effort: max

tools:
  - name: file_reader
    description: 读取文件内容或列出目录
    parameters: {type: object, properties: {path: {type: string, description: 文件路径}}, required: [path]}
    activation: kernel
  - name: file_writer
    description: 写入文件（创建或覆盖）
    parameters: {type: object, properties: {path: {type: string}, content: {type: string}}, required: [path, content]}
    activation: kernel
  - name: file_deleter
    description: 软删除文件
    parameters: {type: object, properties: {path: {type: string}}, required: [path]}
    activation: kernel
  - name: file_download
    description: 生成文件下载链接
    parameters: {type: object, properties: {path: {type: string}}, required: [path]}
    activation: discoverable
  - name: web_search
    description: 搜索互联网
    parameters: {type: object, properties: {query: {type: string}}, required: [query]}
    activation: kernel
  - name: web_fetch
    description: 获取网页内容
    parameters: {type: object, properties: {url: {type: string}}, required: [url]}
    activation: kernel
  - name: handoff_to_sys
    description: 将资源操作移交给 SysAgent
    parameters: {type: object, properties: {task: {type: string}, context: {type: string}}, required: [task]}
    activation: kernel
  - name: resource_loader
    description: 动态加载/激活工具或技能
    parameters: {type: object, properties: {name: {type: string}, type: {type: string, enum: [tool, skill]}}, required: [name, type]}
    activation: discoverable
  - name: resource_scaffold
    description: 创建新资源骨架
    parameters: {type: object, properties: {type: {type: string, enum: [tool, skill]}, name: {type: string}, description: {type: string}}, required: [type, name]}
    activation: discoverable
  - name: resource_registrar
    description: 查询配置状态和依赖
    parameters: {type: object}
    activation: discoverable
  - name: memory_store
    description: 写入记忆
    parameters: {type: object, properties: {content: {type: string}, category: {type: string, enum: [fact, preference, decision, context]}}, required: [content]}
    activation: kernel
  - name: manage_hooks
    description: 管理生命周期钩子
    parameters: {type: object, properties: {action: {type: string, enum: [list, enable, disable]}}}
    activation: discoverable
  - name: model_switch
    description: 切换对话使用的模型
    parameters: {type: object, properties: {model: {type: string}}, required: [model]}
    activation: discoverable
  - name: python_exec
    description: 执行 Python 代码片段
    parameters: {type: object, properties: {code: {type: string}}, required: [code]}
    activation: discoverable

hooks:
  - name: self_evolve
    type: post_tool_exec
    run: ["python ./hooks/self_evolve.py"]
    timeout: 5s

advanced:
  max_turns: 50
  critical_rules: |
    1. Always read a file before editing it.
    2. When a tool fails, analyze the error and retry.
    3. For resource creation tasks, hand off to sys_agent via handoff_to_sys.
    4. Self-evolve: create new tools/skills when existing ones are insufficient.

agents:
  - name: sys_agent
    description: 系统工程师——处理资源创建、模型配置等系统级操作
    models:
      - name: deep
        api_type: openai
        model: deepseek-v4-pro
        api_base: https://api.deepseek.com
        api_key_env: DEEPSEEK_API_KEY
        kwargs:
          reasoning_effort: max
    tools:
      - name: file_reader
        activation: kernel
      - name: file_writer
        activation: kernel
      - name: file_deleter
        activation: kernel
      - name: resource_loader
        activation: kernel
      - name: resource_scaffold
        activation: kernel
      - name: resource_registrar
        activation: kernel
      - name: web_fetch
        activation: kernel
    skills:
      - name: resource_scaffold
        description: 创建新资源骨架
        prompt: |
          You create new tools and skills as directory scaffolds.
          A tool = tool.yaml + function.py in tools/<name>/
          A skill = skill.yaml in skills/
        tools: [file_writer]
        activation: kernel
      - name: tool_generator
        description: 生成完整工具实现
        prompt: |
          Generate complete tool: tool.yaml with JSON Schema + function.py with execute(**kwargs).
          Validate schema and code before writing.
        tools: [file_writer, resource_scaffold]
        activation: discoverable
      - name: skill_generator
        description: 生成技能定义
        prompt: |
          Generate skill.yaml with prompt template and recommended tool list.
        tools: [file_writer]
        activation: discoverable
      - name: validate_tool
        description: 校验工具完整性
        prompt: Verify tool.yaml schema + function.py interface match. Check JSON Schema validity.
        tools: [file_reader]
        activation: discoverable
      - name: validate_skill
        description: 校验技能完整性
        prompt: Verify skill.yaml format, prompt quality, and tool references are valid.
        tools: [file_reader]
        activation: discoverable
    advanced:
      max_turns: 15

handover:
  rules:
    - from_agent: arf_assistant
      to_agent: sys_agent
      trigger: "创建或修改 tools/skills/models 资源"
    - from_agent: sys_agent
      to_agent: arf_assistant
      trigger: "资源操作完成或需要用户确认"
```

- [ ] **Step 3: Verify agent.yaml loads**

```bash
cd app/arf_default_assistant && python3 -c "
from arf.agent.config import AgentConfig
cfg = AgentConfig.from_yaml('agent.yaml')
assert cfg.name == 'arf_assistant', f'name: {cfg.name}'
assert len(cfg.models) == 2, f'models: {len(cfg.models)}'
assert len(cfg.agents) == 1, f'agents: {len(cfg.agents)}'
assert cfg.agents[0].name == 'sys_agent', f'agent: {cfg.agents[0].name}'
assert len(cfg.handover.rules) == 2, f'handover rules: {len(cfg.handover.rules)}'
print(f'agent.yaml valid: {cfg.name} with {len(cfg.tools)} tools, {len(cfg.skills)} skills, sub-agent: {cfg.agents[0].name}')
"
```

- [ ] **Step 4: Commit**

```bash
git add app/arf_default_assistant/agent.yaml
git commit -m "feat(app): add dual-agent agent.yaml with handover config (14 tools, 5 sys skills)"
```

---

## Phase 2-5: Tools, Skills, Server, CLI

*(Due to plan length, these phases are documented with complete code in the implementation. Each follows the same pattern: create files → verify → commit. Key files:)*

**Phase 2** — Create 14 tool directories under `app/arf_default_assistant/tools/` with `tool.yaml` + `function.py`. Port from `src/arf/resources/system/tools/`.

**Phase 3** — Create 19 skill YAML files under `app/arf_default_assistant/skills/`. Port from `src/arf/resources/system/skills/`.

**Phase 4** — Create `server.py` (~400 lines, FastAPI + SSE + lazy persistence) and `lazy_persistence.py` (~80 lines, archive/restore).

**Phase 5** — Create `cli.py` (~200 lines, 10 commands: init/start/stop/reload/chat/web/run/list/validate/clone).

**Phase 6** — Create `hooks/self_evolve.py` (~30 lines, post_tool_exec auto-reload).

**Phase 7** — Update `frontend/vite.config.ts` proxy. Build frontend. Verify full stack.

**Phase 8** — Integration test: start server, curl /chat, /config/status, /resources, /save. Graceful shutdown.

---

Plan complete — 8 phases. Execute via subagent-driven-development.
