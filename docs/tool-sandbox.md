# Tool Sandbox & Security Boundaries

ARF 将工具调用视为系统调用，通过路径沙箱、参数校验和权限分级构建安全边界。

---

## 1. OS 方案演进

> 本章描述 OS 如何实现安全边界与访问控制，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 系统调用 — 用户态到内核态的门控

**问题**：用户程序如何执行特权操作（读写文件、网络通信、分配内存）而不破坏系统安全？

**解决方案**：系统调用。x86 架构通过 `int 0x80`（旧）或 `sysenter/syscall`（新）指令从用户态（Ring 3）陷入内核态（Ring 0）。内核在入口处检查调用号是否合法、参数指针是否在用户地址空间内，然后通过系统调用表（sys_call_table）分派到具体处理函数。

**演进**：现代 CPU 提供 SMAP/SMEP（Supervisor Mode Access/Execution Prevention）硬件强制内核不能随意访问用户空间内存，消除了一整类内核漏洞。

### 1.2 保护环 — 分级特权

x86 保护环模型（Ring 0–3）。Ring 0（内核态）可执行特权指令，Ring 3（用户态）无法直接执行。环之间的穿越必须通过系统调用门或中断门。现代 OS 实际只用 Ring 0 和 Ring 3，Ring 1-2 被虚拟化技术替代。

### 1.3 访问控制 — DAC 到 MAC

- **自主访问控制（DAC）**：文件所有者设定 rwx 权限，经典 Unix 模式。问题是 root 可绕过一切
- **强制访问控制（MAC）**：SELinux 按 type 标签控制，AppArmor 按路径控制。用户不可绕过
- **能力（Capabilities）**：将 root 超级权限拆分为细粒度能力（CAP_NET_RAW、CAP_SYS_ADMIN 等）

### 1.4 进程隔离与沙箱

- **chroot**（1979）：切换根目录，root 可逃逸
- **seccomp-bpf**（2012）：进程自愿限制自己可用的系统调用。Chromium 用它锁定渲染进程
- **命名空间**（2013+）：PID/Mount/Network 等独立视图。Docker 的安全基础

### 1.5 对 ARF 的启发

系统调用的门控模型启示了工具调用的层层检查：参数校验 → 权限判断 → 执行。保护环启示了框架资源（内核）与用户工作区（用户空间）的分离。seccomp 的 syscall 过滤启示了 per-tool 参数校验。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
LLM 生成 tool_call
    │
    ▼
GraphEngine（权限判断 — auth）
    │
    ├─ [1] Pipeline 顺序检查（硬阻断）
    │       SkillPipeline.can_execute() → 依赖未满足则阻断
    │
    ├─ [2] SessionModeManager.resolve()（框架级）
    │       将全局 session_mode + 当前 AgentPolicy 解析为有效模式
    │       auto → 全部放行；plan → 只读放行 + 写工具阻断；ask → 检查权限列表
    │
    ├─ [3] PermissionRegistry.evaluate()（框架级，仅 ASK 模式）
    │       deny_patterns → deny list → ask list → allow list → 默认 ask
    │       deny → 阻断；ask → 审批通道（60s 超时自动拒绝）；allow → 放行
    │
    ├─ [4] 审批通道（GraphEngine + SSE + 前端）
    │       approval_required 事件 → 前端确认 → 恢复/拒绝执行
    │
    ├─ [5] tool_executor.execute()
    │       ├─ 解析 DirectoryBoundary（per-tool 白名单边界）
    │       │    优先使用 tool.yaml 中声明的 allowed_dir
    │       │    未配置则使用全局 workspace_root 作为默认边界
    │       ├─ PathCheckToolGuard.check(tool_name, params, boundary)
    │       │   （硬阻断，安全 — are params safe?）
    │       │   两层白名单：全局 workspace_root + per-tool allowed_dir 提权
    │       │   检查项：路径穿越 / 绝对路径 / symlink / 边界逃逸
    │       │   自动跳过文件内容字符串（含换行或 >500 字符）
    │       ├─ ContentGuard.check_dangerous()（硬阻断 — is the intent safe?）
    │       │   CP1：工具执行前检测危险行为模式
    │       │   built-in：pipe_to_shell / eval_exec / rm_rf_root
    │       └─ 工具在 Agent 进程内执行
    │
    ├─ [6] ContentGuard.redact_sensitive()（输出过滤，引擎级）
    │       CP2：_step_execute_tools — 工具输出脱敏
    │       CP3：_step_call_model — 模型响应脱敏
    │       built-in：openai_key / phone_cn
    │
    └─ [7] RegexOutputGuard.check()（已废弃，由 ContentGuard 替代）

**关键架构变化**：权限（能否使用此工具）与安全（参数是否安全）现已分离。路径沙箱从引擎流水线下移到执行器，作为工具执行前的最后一层安全检查。认证由引擎中的 SessionModeManager + PermissionRegistry 处理。
```

### 2.2 防护栏 — 框架级强制

五个防护栏通过 `DefaultGuardRunner`（`arf/guardrails/runner.py`）组合，在引擎中统一调用。PermissionRegistry 通过 `check_tool_permission()` 接口接入，PathCheckToolGuard 通过 `check_tool_params()` 接口接入，ContentGuard 通过 `check_dangerous()` 和 `redact_sensitive()` 两个接口分别集成到执行器和引擎中：

| 防护栏 | 位置 | 类型 | 行为 |
|--------|------|------|------|
| `NoneInputGuard` | 输入 | — | 始终放行，预留 LLM 分类器扩展点 |
| `PermissionRegistry` | 工具权限 | 软阻断 | 通过 `SessionModeManager` 解析有效模式后，按 deny_patterns → deny → ask → allow → 默认 ask 优先级判断 |
| `PathCheckToolGuard` | 工具参数 | 硬阻断 | 在 execution 中执行（而非引擎流水线），接收 `DirectoryBoundary` 进行白名单校验；递归扫描参数中的路径字符串（跳过内容字符串）；两层边界：全局 `workspace_root`（默认）+ per-tool `allowed_dir`（提权）|
| `ContentGuard`（危险） | 工具参数 | 硬阻断 | CP1：工具执行前检测危险行为模式（pipe_to_shell / eval_exec / rm_rf_root），匹配即阻断 |
| `ContentGuard`（敏感） | 输出 + 工具输出 | 过滤 | CP2：工具输出脱敏 openai_key / phone_cn；CP3：模型响应脱敏 openai_key / phone_cn |
| `RegexOutputGuard` | 输出 | 过滤 | **已废弃**，由 ContentGuard 的 sensitive_patterns 替代 |

### 2.3 路径沙箱 — DirectoryBoundary + PathCheckToolGuard

#### DirectoryBoundary — 白名单路径边界

`arf/sandbox/directory_boundary.py`。`DirectoryBoundary` 是基于白名单的路径校验边界，替代了旧的 `PathSandbox` 路径沙箱。每个边界绑定一个根目录，路径合法性通过白名单验证（路径必须解析到边界根目录之内），而非黑名单模式。

```python
boundary = DirectoryBoundary("/path/to/workspace")
boundary.contains("subdir/file.txt")    # True — 完全在边界内
boundary.contains("../etc/passwd")       # False — .. 穿越直接拒绝
boundary.has_symlink("link/file.txt")    # 检查路径组件中的 symlink
```

核心方法：

| 方法 | 说明 |
|------|------|
| `contains(path_str)` | 白名单验证——路径解析后是否在边界根目录内；`..` 穿越直接拒绝 |
| `has_symlink(path_str)` | 从根目录向下逐段检查路径每个组件是否为符号链接 |
| `resolve(path_str)` | 相对于边界根目录解析路径，返回完全解析的 `Path` 对象 |

`PathSandbox`（`arf/sandbox/path_sandbox.py`）已精简为纯路径解析工具，仅保留 `resolve_path()` 和 `validate_command()`，所有边界逻辑移至 `DirectoryBoundary`。

#### 两层白名单模型

`PathCheckToolGuard`（`arf/guardrails/path_check.py`）不再在内部持有固定的 `workspace_root`。边界由 executor 在每次调用时传入，框架支持两层白名单：

```
executor._check_params(tool_name, params):
    boundary = tool_boundaries.get(tool_name, default_boundary)
    # ① 优先使用 per-tool allowed_dir（提权）
    # ② 未配置则使用全局 workspace_root（默认）
    tool_guard.check(tool_name, params, boundary)
```

- **默认边界（`default_boundary`）**：全局 `workspace_root`，所有工具共享，阻止工作区逃逸
- **Per-tool 提权（`tool_boundaries`）**：工具在 `tool.yaml` 中声明 `allowed_dir` 后，可访问 `workspace_root` 之外的白名单目录。框架在 `BaseAgent` 装配时自动收集所有工具的 `allowed_dir`，构建 `tool_name → DirectoryBoundary` 映射

这种做法取代了旧的 `SandboxConfig.allow_escape`（黑名单逃逸开关）和 `SandboxConfig.writable_dirs`（可写目录列表）——基于白名单的 `DirectoryBoundary` 更接近安全最佳实践，也消除了 "声明的可写目录" 与 "实际可访问目录" 之间的歧义。

例如，一个需要访问系统级模板目录的工具在 `tool.yaml` 中声明：

```yaml
# app/arf_default_assistant/tools/template_manager/tool.yaml
name: template_manager
description: 管理系统模板文件
allowed_dir: /usr/share/templates  # 提权到 workspace_root 之外的目录
```

`BaseAgent` 装配时自动为 `template_manager` 创建指向 `/usr/share/templates` 的 `DirectoryBoundary`，executor 执行时优先使用此边界。

#### Subagent 继承修复

子代理执行器（`arf/plugins/subagent/tools/subagent/function.py`）现在从父引擎继承 `tool_guard` 和 `tool_boundaries`：

```python
tool_guard = getattr(parent_executor, '_tool_guard', None)
tool_boundaries = getattr(parent_executor, '_tool_boundaries', {})
default_boundary = getattr(parent_executor, '_default_boundary', None)
```

此前子代理执行器未继承这些安全组件，存在安全缺口。现在子代理与父代理共享相同的 `PathCheckToolGuard` 和边界配置，安全策略在 agent 层次结构中保持一致。

#### PathCheckToolGuard 检查流程

各检查项通过 `PathCheckFlags` 独立开关，默认全部启用：

```yaml
# agent.yaml
advanced:
  sandbox:
    checks:
      path_traversal: true          # 目录穿越（..）
      absolute_path: true           # 绝对路径（/）
      workspace_containment: true   # 工作区逃逸（白名单）
      symlink: true                 # 符号链接检测
```

检查顺序（首次失败即返回）：
1. **内容跳过** — 含换行符或长度 >500 字符的字符串视为内容而非路径，避免 `/* CSS 注释 */` 等被误判
2. **路径穿越**（`..`）
3. **绝对路径**（以 `/` 开头）
4. **路径深度** — 超过 `ResourceQuota.max_path_depth` 则阻断
5. **路径数量** — 超过 `ResourceQuota.max_path_count` 则阻断
6. **符号链接穿越** — 通过 `boundary.has_symlink()` 检测
7. **白名单边界逃逸** — 通过 `boundary.contains()` 验证

#### ResourceQuota

`ResourceQuota` 支持三个可选限制：

| 配额 | 类型 | 说明 |
|------|------|------|
| `max_path_count` | `int \| None` | 单次调用最多检查的路径字符串数量 |
| `max_path_depth` | `int \| None` | 单个路径的最大目录深度（`Path.parts` 长度） |
| `deny_symlinks` | `bool` | 是否拦截 symlink 穿越（默认 `True`） |

### 2.4 ContentGuard — 统一内容安全检查引擎

ContentGuard（`arf/guardrails/content_guard.py`）是统一的内容安全检查引擎，覆盖工具调用前后的全链路安全：执行前检测危险行为，执行后和输出前脱敏敏感信息。

#### 两类规则

| 规则类型 | 检查时机 | 违规行为 |
|----------|----------|----------|
| `dangerous_patterns` | 执行前（CP1） | 阻断调用，返回错误 |
| `sensitive_patterns` | 执行后 + 输出前（CP2/CP3） | 替换脱敏，写入消息 |

框架内置默认规则：

**dangerous_patterns（内置）：**

| 名称 | 模式 | 说明 |
|------|------|------|
| `pipe_to_shell` | `(curl\|wget).*\|.*(sh\|bash\|python)` | 阻止下载内容管道到 shell |
| `eval_exec` | `\beval\s*\(` | 阻止 eval() 动态执行 |
| `rm_rf_root` | `rm\s+-rf\s+/` | 阻止递归删除根目录 |

**sensitive_patterns（内置）：**

| 名称 | 模式 | 替换 |
|------|------|------|
| `openai_key` | `sk-[-a-zA-Z0-9]{20,}` | `[REDACTED_API_KEY]` |
| `phone_cn` | `\b1[3-9]\d{9}\b` | `[REDACTED_PHONE]` |

#### 三个检查点

| 检查点 | 位置 | 时机 | 规则类型 | 结果 |
|--------|------|------|----------|------|
| CP1 | `ConcurrentToolExecutor._check_params()` | 工具执行前 | `dangerous_patterns` | 匹配则阻断，不执行 |
| CP2 | `GraphEngine._step_execute_tools()` | 工具执行后，追加到消息前 | `sensitive_patterns` | 匹配则替换，写入消息 |
| CP3 | `GraphEngine._step_call_model()` | 模型响应后，追加到消息前 | `sensitive_patterns` | 匹配则替换，写入消息 |

#### 合并策略

App 配置与框架内置规则通过名称合并。同名规则覆盖内置规则，异名规则追加到列表：

```python
_BUILTIN_DANGEROUS = [
    {"name": "pipe_to_shell", "pattern": r"(curl|wget).*\|.*(sh|bash|python)", ...},
    {"name": "eval_exec", ...},
    {"name": "rm_rf_root", ...},
]
# App 配置与内置合并：同名覆盖，异名追加
merged = _merge_rules(_BUILTIN_DANGEROUS, app_dangerous)
```

合并后的规则顺序：内置规则在前，App 新增规则在后。同名规则仅保留 App 版本（覆盖内置）。

#### 启用/禁用

ContentGuard 默认启用。可通过 `agent.yaml` 中 `content_guard.enabled: false` 完全禁用（包括所有 dangerous + sensitive 检查）。

#### 与 RegexOutputGuard 的关系

`RegexOutputGuard`（`arf/guardrails/regex_clean.py`）现已废弃，功能由 ContentGuard 的 `sensitive_patterns` 完全替代。ContentGuard 的默认敏感规则（`openai_key`、`phone_cn`）与旧 `RegexOutputGuard` 默认规则一致，确保向后兼容。

#### 与 PathSandbox.validate_command() 的清理

`PathSandbox.validate_command()` 已被移除（dead code），其职责由 ContentGuard 的 `dangerous_patterns` 完全覆盖。`PathSandbox` 现仅保留 `resolve_path()` 作为纯路径解析工具。

### 2.5 双源隔离 — 应用层约定

> **以下隔离为应用层约定，非框架强制。**

框架资源的"只读"和用户工作区的"读写"分离通过以下方式实现：

| 区域 | 权限 | 实现方式 |
|------|------|----------|
| 框架资源（`arf/`） | 约定只读 | 不在工具可写路径内；`PathCheckToolGuard` 阻断绝对路径间接保护 |
| 用户工作区 | 读写 | 所有内置文件工具硬编码 `WORKSPACE = Path("workspaces/default")` |
| 系统资源标记 | UI 提示 | 前端对系统工具/技能显示"(只读)"标签 |

框架提供的是 `PathCheckToolGuard`——保证工具调用不会逃逸工作区边界。至于"框架文件不可写"这一约束，依赖工具实现遵守工作区约定，框架未在代码层面强制。

### 2.6 会话权限模式 — 全新的 session 级权限系统

ARF 引入了统一的会话权限模式系统，替代了旧的 `ToolPermissionChecker` 和 `Promotion/strategies`。新的 `arf/session/` 模块将权限控制从"工具参数级别"提升到"会话级别"，并支持全局模式与代理级策略的组合。

#### 核心类型（`arf/session/types.py`）

`SessionMode` 定义三种全局模式：

| 模式 | 含义 |
|------|------|
| `auto` | 全部放行，忽略所有权限列表和代理策略 |
| `ask` | 按 deny/ask/allow 权限列表审批，代理策略可生效 |
| `plan` | 只读模式，所有有副作用的工具被阻断 |

`AgentPolicy` 定义代理级策略覆盖（仅在全局模式为 `ask` 时生效）：

| 策略 | 含义 |
|------|------|
| `auto` | 该代理全部放行 |
| `ask` | 检查 deny/ask/allow 权限列表 |
| `plan` | 该代理只读，不允许副作用工具 |
| `null` | 跟随全局模式（即 `ask`） |

#### 组合矩阵（`arf/session/mode_manager.py`）

`SessionModeManager` 负责将全局模式与代理策略解析为有效模式：

| 全局模式 | 代理策略 | 有效模式 |
|----------|----------|----------|
| `auto` | 任意 / `null` | `auto` |
| `plan` | 任意 / `null` | `plan` |
| `ask` | `auto` | `auto` |
| `ask` | `ask` | `ask` |
| `ask` | `plan` | `plan` |
| `ask` | `null` | `ask` |

`auto` 和 `plan` 是**硬覆盖**——无论代理策略如何设定，都强制执行全局模式。

#### PermissionRegistry — 统一权限列表评估（`arf/session/permissions.py`）

`PermissionRegistry` 接收 `PermissionLists` 进行工具权限评估，评估优先级如下：

```
deny_patterns（参数内容危险模式匹配）
  → deny 列表
    → ask 列表
      → allow 列表
        → 默认 ask（安全默认）
```

内置默认规则：

- `_DEFAULT_ALLOW_TOOLS`：默认允许工具列表，包含 `["file_reader", "web_search", "web_fetch", "memory_store", "resource_loader", "resource_registrar", "resource_scaffold"]`
- `_BUILTIN_DENY_PATTERNS`：内置危险模式列表，包含 `["rm -rf /", "sudo ", "chmod 777 /", "> /dev/sda", "curl.*|.*sh", "wget.*|.*sh"]`

当 `agent.yaml` 中 `permissions.allow` 未配置时，`_DEFAULT_ALLOW_TOOLS` 作为默认值；`_BUILTIN_DENY_PATTERNS` 始终与配置的 `deny_patterns` 合并生效。

`PermissionLists` 支持热替换——引擎在代理切换（handoff）时调用 `swap_lists()` 切换权限列表。

#### PLAN 模式与副作用检测

`arf/session/mode_manager.py` 提供 `has_side_effect(tool_name)` 函数，用于 PLAN 模式下的只读/写判断：

- **已知只读工具**（通过）：`file_reader`、`glob`、`grep`、`web_search`、`web_fetch`、`memory_store`、`memory_extract`、`resource_loader`、`planner`、`todo`、`handoff`、`model_switch`、`undo`
- **已知写工具**（阻断）：`file_writer`、`file_deleter`、`file_download`、`python_exec`、`bash`、`resource_registrar`、`resource_scaffold`、`md2pdf`
- **未知工具**（安全默认）：假定有副作用，阻断

#### 审批通道实现

引擎在 `graph.py` 中处理 ASK 模式的审批流程：

1. `SessionModeManager` 解析有效模式 → `SessionMode.ASK`
2. `DefaultGuardRunner.check_tool_permission()` → `PermissionRegistry.evaluate()` 返回 `"ask"`
3. 引擎生成 `decision_id`，发射 `approval_required` 事件，`asyncio.Event.wait(60s)` 挂起
4. **SSE 流式路径（astream）**：事件通过 `yield` 直接推送 SSE → 前端展示审批 UI
5. **非流式路径（invoke）**：事件通过 `EventBus` 发射，App 层需自行订阅 EventBus
   并推送给前端（如 WebSocket 或轮询）；收到审批后调用 `engine.approve()` 解除阻塞
6. App 层调用 `engine.approve(decision_id, approved)` → Event.set()
7. 引擎恢复执行：批准 → `valid_calls`，拒绝 → `denied_calls`

### 2.7 Hook 退出码契约

Hook 作为独立子进程运行（`SubprocessHookRunner`），通过退出码与引擎协作：

| 退出码 | 行为 |
|--------|------|
| 0 | 继续执行 |
| 1 | 阻断当前操作 |
| 2 | 注入消息到对话流 |

Hook 不是安全机制（子进程行为不可信），而是框架与外部脚本的协作接口。安全边界由 `PathCheckToolGuard` 和权限配置强制保证。

### 2.8 配置

```yaml
# 顶层：会话级全局权限模式
session_mode: ask            # auto | ask | plan
                             # auto: 全部放行（YOLO 模式）
                             # ask:  权限列表 + 审批通道（默认）
                             # plan: 只读模式，写工具阻断

advanced:
  guardrails:
    input: none
    output: regex_clean
    tool_params: path_check
    # 自定义输出过滤规则（省略时使用框架内置默认值）
    output_patterns:
      - pattern: "sk-[-a-zA-Z0-9]{20,}"
        replacement: "[REDACTED_API_KEY]"
      - pattern: "\\b1[3-9]\\d{9}\\b"
        replacement: "[REDACTED_PHONE]"
      # 用户可扩展：身份证、银行卡、邮箱等
      # - pattern: "\\d{15,19}"
      #   replacement: "[REDACTED_CARD]"
    # ContentGuard — 统一内容安全检查（危险行为检测 + 敏感信息过滤）
    content_guard:
      enabled: true
      # 危险行为模式（执行前检测，匹配即阻断）
      dangerous_patterns:
        - name: pipe_to_shell
          pattern: "(curl|wget).*\\|.*(sh|bash|python)"
          description: "Prevent piping downloaded content to shell"
        # - name: custom_danger
        #   pattern: "some_other_dangerous_pattern"
        #   description: "..."
      # 敏感信息模式（执行后和输出前脱敏）
      sensitive_patterns:
        - name: openai_key
          pattern: "sk-[-a-zA-Z0-9]{20,}"
          replacement: "[REDACTED_API_KEY]"
        - name: phone_cn
          pattern: "\\b1[3-9]\\d{9}\\b"
          replacement: "[REDACTED_PHONE]"
        # - name: custom_sensitive
        #   pattern: "..."
        #   replacement: "..."
    permissions:
      deny: []
      ask: [file_writer, file_deleter, python_exec]
      allow: [file_reader, web_search, web_fetch, …]
      deny_patterns: ["rm -rf", "sudo", "chmod 777"]

  # 代理级策略覆盖（仅当 session_mode=ask 时生效）
  # 每个子代理可在其配置中指定 permissions.policy:
  #   policy: auto       # 该代理全部放行
  #   policy: ask        # 该代理按权限列表审批
  #   policy: plan       # 该代理只读
  #   policy: null       # 跟随全局模式（默认）

  human_loop:
    approval_points: tool_name_allowlist   # always_auto | tool_name_allowlist
    allowlist: [file_writer, file_deleter, python_exec]
    channel: websocket
    timeout: 60s
```

**事实校验**：`session_mode` 通过 `BaseAgent._session_mode_manager` → `SessionModeManager` 完整接入。`PermissionsConfig` 通过 `PermissionLists.from_config()` → `PermissionRegistry` 完整接入。`HumanLoopConfig` 通过 `base.py` → `GraphEngine.approval_enabled` 完整接入。`SandboxConfig.checks` 通过 `AdvancedConfig.sandbox.checks` → `PathCheckToolGuard` 完整接入。`allowed_dir` 在 `tool.yaml` 中声明，由 `BaseAgent` 装配时收集并构建 `tool_boundaries` 映射。`PermissionLists` 热替换在 Agent handoff 时通过 `DefaultGuardRunner.swap_lists()` 完成。`ContentGuard` 通过 `BaseAgent` → `ContentGuard(config)` 装配，通过 `DefaultGuardRunner` → `ConcurrentToolExecutor`（CP1）和 `GraphEngine`（CP2/CP3）接入引擎。配置通过 `GuardrailsConfig.content_guard` → `ContentGuardConfig` 完整接入。

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：per-invocation 独立沙箱

当前工具在 Agent 进程内执行（`backend: function`）。一个工具的崩溃或死循环直接影响 Agent 进程。参考 seccomp 和容器隔离的思路：

1. **子进程执行**：每次调用 fork+exec 独立进程，限制 CPU/内存
2. **系统调用过滤**：限制工具进程可用的 syscall
3. **文件系统隔离**：每次调用挂载临时 overlay，调用结束后丢弃

### 3.2 MCP 协议集成

ARF 的工具解析器架构（`ToolResolver` + `ToolProvider`）支持多提供者扩展。增加 `MCPToolProvider` 后，沙箱检查在引擎层统一执行，与工具来源无关。

### 3.3 已实现：会话权限模式系统

会话权限模式（`arf/session/`）已实现并完整接入引擎、配置系统和 Agent 组装流程。替代了旧的 `ToolPermissionChecker`（已删除）和 `Promotion/strategies`（已删除）。详见 2.6 节。

### 3.4 探索性方向

- **审批通道增强**：支持更丰富的审批策略（如按参数值审批、审批链），审批历史记录与回溯，审批超时策略定制
- **审计日志**：将每次权限判定（deny/ask/allow）写入结构化审计日志，支持会话回放和合规审查
- **运行时模式切换**：支持通过 API 在 auto/ask/plan 之间动态切换（当前仅在配置加载时确定）
