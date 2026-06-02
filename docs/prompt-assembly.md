# Prompt Assembly — System Prompt 组装

## 1. OS 方案演进

### 1.1 类比: 程序加载器 (execve / ELF Loader)

操作系统在 `execve()` 时做三件事:
1. **读 ELF 头** → 确认格式, 提取 segment 描述
2. **映射内存** → text (只读可执行) + data (读写) + bss (零初始化)
3. **传递环境** → argc/argv/envp/auxv 注入进程地址空间

Prompt 组装是 Agent 的 execve:
1. **读配置** → `system_prompt.prefix` + `system_prompt.suffix`
2. **分层拼接** → prefix (稳定, 命中 API cache) → suffix (动态模板)
3. **占位符替换** → `$INVENTORY` (工具清单) / `$MEMORY` (长期记忆) / `$WORKSPACE` (工作区) / `$TURN_BUDGET` (剩余轮次)

### 1.2 为什么稳定区在前

ELF 把 `.text` 放前段, `.data`/`.bss` 放后段。同样的道理: LLM API 的 prompt cache 按 prefix 匹配。`role` 和 `critical_rules` 几乎不变 → 始终命中 cache。`suffix` 中的 `$INVENTORY` 随工具变更而变 → 放在后半段, 不影响 prefix 的 cache 命中。

### 1.3 阶段演进

| 阶段 | 问题 | 方案 |
|------|------|------|
| v0.1 | 散落字符串拼接 | `template` + `critical_rules` 字段, 无分层 |
| v0.2 | 引入 `SystemPromptProvider` Protocol | 支持依赖注入替换 |
| v1.0 (当前) | prefix/suffix 分层 + `string.Template` 占位符 | `role` → `critical_rules` 顺序保证, cache 优化 |
| v1.1 (规划) | 多 Agent prompt 组合, 基于角色的模板分发 | 见 §3 |

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

**字段语义**:

| 字段 | 缓存策略 | 内容 |
|------|---------|------|
| `prefix.role` | 极稳定 | 角色定义 (你是谁, 能力边界) |
| `prefix.critical_rules` | 极稳定 | 硬规则 (R1/R2/...), 绝对不可违反 |
| `suffix` | 可变 | `$INVENTORY` / `$MEMORY` 等占位符模板 |

### 2.2 组装流程

```
agent.yaml               DefaultSystemPromptProvider       BaseAgent
─────────                ─────────────────────────         ─────────
system_prompt.prefix ──→ build() ──→ SystemPrompt ──→ $INVENTORY → MCP 工具清单
       .role                │         .prefix               $MEMORY    → memory.md 内容
       .critical_rules      │         .suffix               $WORKSPACE → 工作区路径
       .suffix              │                               $TURN_BUDGET → 剩余轮次
                            │
                            └── prefix: role + "\n\n" + critical_rules (顺序保证)
                                suffix: 原样透传 (占位符留待 engine 替换)
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
        prefix_parts = []
        if pc.role:
            prefix_parts.append(pc.role.strip())
        if pc.critical_rules:
            prefix_parts.append(pc.critical_rules.strip())
        prefix = "\n\n".join(prefix_parts)

        suffix = sp.suffix

        return SystemPrompt(prefix=prefix, suffix=suffix)
```

**顺序保证**: `role` → `critical_rules`。不依赖用户是否在 YAML 中交换了字段顺序。

### 2.4 SystemPrompt 值对象

```python
# arf/agent/prompt.py
@dataclass
class SystemPrompt:
    prefix: str   # role + critical_rules, 稳定, 命中 API cache
    suffix: str   # $INVENTORY / $MEMORY 等动态模板

    @property
    def full_text(self) -> str:
        return self.prefix + self.suffix
```

### 2.5 占位符机制

使用 Python `string.Template` 语法 (`$VAR`)。两层替换:

| 层级 | 占位符 | 替换时机 | 替换者 | 缓存影响 |
|------|--------|---------|--------|---------|
| 启动期 | `$INVENTORY` | 会话开始, MCP 连接后 | `BaseAgent.__init__` | 工具变更 → `resources/updated` 通知刷新 |
| 启动期 | `$MEMORY` | 会话开始, memory.md 加载后 | `BaseAgent.__init__` | 不应频繁变 (记忆按轮次间隔写入) |
| 每轮 | `$WORKSPACE` | 每轮 `_execute` 开始时 | `GraphEngine` | 每轮变, 放在 prompt 尾部 |
| 每轮 | `$TURN_BUDGET` | 每轮 `_execute` 开始时 | `GraphEngine` | 每轮变, 放在 prompt 尾部 |

**为什么用 `string.Template`**:
- `$VAR` 比 `{{VAR}}` 更短, 减少 token 消耗
- `$$` 转义为字面量 `$`
- 比 `str.replace()` 更安全 — `Template.safe_substitute()` 对未定义变量不抛异常

### 2.6 SubAgent Prompt 组装

SubAgent 独立拥有 `system_prompt`, 通过相同的 `SystemPromptProvider` 组装, 存储在 `self._sub_agent_configs[name]["system_prompt"]` 中。handoff 时 engine 使用目标 Agent 的 system prompt 构建初始消息。

### 2.7 Protocol 接口

```python
# arf/core/protocols/prompt.py
class SystemPromptProvider(Protocol):
    def build(self) -> SystemPrompt:
        """返回组装好的 SystemPrompt, prefix/suffix 已填充"""
        ...
```

App 可通过 `override_protocols["system_prompt_provider"]` 注入自定义实现。

### 2.8 代码路径

```
arf/agent/config.py:65-80        SystemPromptConfig + PrefixConfig (Pydantic models)
arf/agent/prompt.py:6-17         SystemPrompt 值对象
arf/agent/default_prompt_provider.py  DefaultSystemPromptProvider
arf/agent/base.py:335-358        BaseAgent 组装入口, 占位符替换
arf/agent/base.py:293-325        SubAgent prompt 组装
arf/core/protocols/prompt.py:9-13  SystemPromptProvider Protocol
```

---

## 3. 演进方向

### 3.1 多 Agent Prompt 组合

当前每个 Agent 独立配置 `system_prompt`. 演进: 共享 `base_prompt` + per-agent `delta`:

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

合并策略: `base + delta` — delta 字段覆盖 base 同名字段, 未覆盖的继承。

### 3.2 基于角色的模板分发

根据 Agent 的 `role` 字段自动选择 prompt 模板, 减少重复配置:

```yaml
prompt_templates:
  router:
    prefix:
      role: "You are a router agent."
      critical_rules: "### R4: Handoff immediately when..."
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

### 3.3 上下文感知 Prompt

根据会话状态动态调整 prompt — 当 `context_summary` 非空时注入 `[Earlier]: ...` 摘要; 当 `tool_failures > 3` 时注入错误恢复提示。

### 3.4 Prompt 版本管理与 A/B 测试

- 每个 prompt 版本打 hash, trace 中记录 `prompt_hash`
- Eval 回放时匹配 prompt 版本, 排除 prompt 变更导致的回归误报
- 支持 A/B 测试: 同一 session 随机选择 prompt 变体, trace 标记
