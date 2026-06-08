# Prompt 组装 — 系统提示词装配

## 1. OS 方案演进

### 1.1 类比：程序加载器（execve / ELF Loader）

操作系统在 `execve()` 时完成三件事：
1. **读取 ELF 头** — 校验格式，提取段描述符
2. **映射内存** — text（只读可执行）+ data（读写）+ bss（零初始化）
3. **传递环境** — argc/argv/envp/auxv 注入进程地址空间

Prompt 组装就是 Agent 的 execve：
1. **读取配置** — `system_prompt.prefix`（role + critical_rules）+ `system_prompt.suffix`
2. **分层拼接** — prefix（稳定，命中 API 缓存）+ suffix（动态模板）
3. **占位符替换** — `$INVENTORY`（工具列表）、`$MEMORY`（长期记忆）、`$WORKSPACE`（工作区路径，规划中）、`$TURN_BUDGET`（剩余轮次，规划中）

### 1.2 稳定内容为何在前

ELF 将 `.text` 放在前、`.data`/`.bss` 放在后。同样的逻辑：LLM API 的 prompt cache 按前缀匹配。`role` 和 `critical_rules` 极少变动，始终命中缓存。`suffix` 包含 `$INVENTORY`，工具更新时变化——它在后半部分，不影响前缀缓存。

| 组件 | ELF 类比 | 缓存行为 |
|------|---------|---------|
| `prefix.role` | `.text`（只读，稳定） | 始终命中缓存 |
| `prefix.critical_rules` | `.rodata`（只读数据） | 始终命中缓存 |
| `suffix` | `.data` / `.bss`（可变） | 随会话变化，不缓存 |

### 1.3 演进阶段

| 阶段 | 问题 | 方案 |
|------|------|------|
| v0.1 | 临时字符串拼接 | 裸 `template` + `critical_rules` 字段，无分层 |
| v0.2 | 无控制反转 | `SystemPromptProvider` Protocol 支持 DI 覆盖 |
| v1.0（当前） | prefix/suffix 分层 + `string.Template` 占位符 | `role` → `critical_rules` 顺序保证，缓存优化 |
| v1.1（规划） | 多 Agent prompt 组合，基于角色的模板分发 | 见第 3 节 |

---

## 2. 当前实现

### 2.1 配置模型

```yaml
# agent.yaml
system_prompt:
  prefix:
    role: |
      You are arf_assistant, a helpful assistant.
    critical_rules: |
      ### R1: Verify with tools, never guess
      ### R2: Tool calls are action, not text
  suffix: |
    $INVENTORY
    $MEMORY
    Current workspace: $WORKSPACE
    Remaining turns: $TURN_BUDGET
```

```python
# arf/agent/config.py
class PrefixConfig(BaseModel):
    role: str = ""
    critical_rules: str = ""

class SystemPromptConfig(BaseModel):
    prefix: PrefixConfig = Field(default_factory=PrefixConfig)
    suffix: str = ""
```

**字段语义：**

| 字段 | 缓存策略 | 内容 |
|------|---------|------|
| `prefix.role` | 极稳定 | 角色定义（你是谁，能力边界） |
| `prefix.critical_rules` | 极稳定 | 硬规则（R1/R2/...），不可违反 |
| `suffix` | 可变 | `$INVENTORY` / `$MEMORY` 占位符模板 |

### 2.2 组装流程

```
agent.yaml                    DefaultSystemPromptProvider       BaseAgent
─────────                     ─────────────────────────         ─────────
system_prompt.prefix ───────→ build() ──→ SystemPrompt ──────→ $INVENTORY → MCP 工具列表
       .role                        │         .prefix             $MEMORY    → 长期记忆（memory.md）
       .critical_rules              │         .suffix
       .suffix                      │
                                     │
                                     └── prefix: role + "\n\n" + critical_rules（顺序保证）
                                         suffix: 原样透传（占位符由 BaseAgent 填充）
```

### 2.3 DefaultSystemPromptProvider

```python
# arf/agent/default_prompt_provider.py
class DefaultSystemPromptProvider:
    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    def build(self) -> SystemPrompt:
        sp = self._config.system_prompt
        pc = sp.prefix
        prefix_parts: list[str] = []
        if pc.role:
            prefix_parts.append(pc.role.strip())
        if pc.critical_rules:
            prefix_parts.append(pc.critical_rules.strip())
        prefix = "\n\n".join(prefix_parts)

        suffix = sp.suffix

        return SystemPrompt(prefix=prefix, suffix=suffix)
```

**顺序保证**：`role` 在前，`critical_rules` 在后。拼接顺序不依赖 YAML 文件中字段的书写顺序。

### 2.4 SystemPrompt 值对象

```python
# arf/agent/prompt.py
@dataclass
class SystemPrompt:
    """组装后的系统提示词，prefix/suffix 分离。

    prefix — role + critical_rules（稳定，命中 API 缓存）
    suffix — inventory + 每轮占位符
    """
    prefix: str
    suffix: str

    @property
    def full_text(self) -> str:
        return self.prefix + self.suffix
```

### 2.5 占位符机制

使用 Python `string.Template` 语法（`$VAR`）。两阶段替换——均在 `BaseAgent.__init__` 期间完成：

| 占位符 | 替换时机 | 替换者 | 缓存影响 |
|--------|---------|--------|---------|
| `$INVENTORY` | 会话启动，MCP 连接后 | `BaseAgent.__init__` 通过 `_build_inventory_from_mcp()` | 工具更新触发 `resources/updated` 通知 |
| `$MEMORY` | 会话启动，长期记忆加载后 | `BaseAgent.__init__` 通过 `_load_resident_memory()` | 不应频繁变动（轮次边界写入） |
| `$WORKSPACE` | 尚未实现 | — | 规划由 ControlPlane 每轮替换 |
| `$TURN_BUDGET` | 尚未实现 | — | 规划由 ControlPlane 每轮替换 |

**为什么用 `string.Template`：**
- `$VAR` 比 `{{VAR}}` 更短，减少 token 消耗
- `$$` 转义为字面量 `$`
- 比 `str.replace()` 更安全——`Template.safe_substitute()` 对未定义变量不抛异常

当前实现中，启动期占位符使用简单的 `str.replace()`。`suffix` 中可能包含 `$WORKSPACE` 和 `$TURN_BUDGET` 标记，在每轮替换实现前它们保留为字面文本。

**`BaseAgent.__init__` 中的实际替换：**

1. `$INVENTORY` — 由 `_build_inventory_from_mcp()` 填充（`base.py:468-484`）。查询 `McpClientManager` 获取所有可用工具定义，格式化为 `## Available Tools` 下的 Markdown 列表。MCP 未就绪时返回空字符串。

2. `$MEMORY` — 由 `_load_resident_memory()` 填充（`base.py:39-61`）。从工作区 memory 目录读取 `memory.md`。内容上限 `max_size_kb`（默认 300 KB），按行截断保留完整行。文件不存在时返回空字符串。

### 2.6 Protocol 接口

```python
# arf/core/protocols/prompt.py
class SystemPromptProvider(Protocol):
    def build(self) -> SystemPrompt:
        """返回组装后的 SystemPrompt，prefix/suffix 已填充。"""
        ...
```

应用代码通过 `override_protocols["system_prompt_provider"]` 注入自定义实现。默认使用 `DefaultSystemPromptProvider`，读取 `AgentConfig` 的 `system_prompt` 段。

### 2.7 代码路径

```
arf/agent/config.py:56-75              SystemPromptConfig + PrefixConfig（Pydantic 模型）
arf/agent/prompt.py:1-17               SystemPrompt 值对象
arf/agent/default_prompt_provider.py   DefaultSystemPromptProvider
arf/agent/base.py:299-321              BaseAgent 组装入口，占位符替换
arf/agent/base.py:468-484              _build_inventory_from_mcp() — $INVENTORY 来自 MCP 工具
arf/agent/base.py:39-61                _load_resident_memory() — $MEMORY 来自 memory.md
arf/core/protocols/prompt.py:9-13      SystemPromptProvider Protocol
```

---

## 3. 演进方向

### 3.1 多 Agent Prompt 组合

当前每个 Agent 独立配置 `system_prompt`。演进方向：共享 `base_prompt` + per-agent `delta`：

```yaml
base_prompt:
  prefix:
    role: "You are an ARF agent."
    critical_rules: |
      ### R1: Verify with tools
      ### R2: Tool calls are action

agents:
  - name: main_agent
    prompt_delta:
      prefix:
        role: "You handle user conversations."
  - name: sys_agent
    prompt_delta:
      prefix:
        role: "You handle system operations."
```

合并策略：`base + delta`——delta 字段按名覆盖 base 字段；未指定的字段从 base 继承。

### 3.2 基于角色的模板分发

根据 Agent 的 `role` 字段选择 prompt 模板，减少重复配置：

```yaml
prompt_templates:
  router:
    prefix:
      role: "You are a router agent."
      critical_rules: "### R4: Route to appropriate sub-agent..."
  builder:
    prefix:
      role: "You are a builder agent."
      critical_rules: "### Design first, then build."

agents:
  - name: main_agent
    template: router
  - name: sys_agent
    template: builder
```

### 3.3 上下文感知 Prompt（每轮占位符）

根据会话状态动态调整 prompt——当 `context_summary` 非空时注入 `[Earlier]: ...` 摘要；当 `tool_failures > 3` 时注入错误恢复提示。`$WORKSPACE` 和 `$TURN_BUDGET` 的每轮替换也属于此范畴——`ControlPlane` 在每次 `invoke()` 或 `astream()` 迭代前更新。

### 3.4 Prompt 版本化与 A/B 测试

- 每个 prompt 版本标记 hash；在 trace 中记录 `prompt_hash`
- Eval 回放时匹配 prompt 版本，排除 prompt 变更引入的回归噪声
- 支持 A/B 测试：同会话内随机选择 prompt 变体，在 trace 中标记
