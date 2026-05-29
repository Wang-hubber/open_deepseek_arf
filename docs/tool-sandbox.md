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
GraphEngine
    │
    ├─ [1] Pipeline 顺序检查（硬阻断）
    │       SkillPipeline.can_execute() → 依赖未满足则阻断
    │
    ├─ [2] PathCheckToolGuard.check()（硬阻断，框架级）
    │       可配置检查项（默认仅工作区逃逸）：路径穿越 / 绝对路径 / symlink / 工作区逃逸
    │       自动跳过文件内容字符串（含换行或 >500 字符）
    │
    ├─ [3] ToolPermissionChecker.check()（软阻断，框架级）
    │       模式匹配 → deny / ask / allow
    │       deny → 阻断；ask → 审批通道（60s 超时自动拒绝）
    │
    ├─ [4] 审批通道（GraphEngine + SSE + 前端）
    │       approval_required 事件 → 前端确认 → 恢复/拒绝执行
    │
    ├─ [5] tool_executor.execute()
    │       工具在 Agent 进程内执行
    │
    └─ [6] RegexOutputGuard.check()（输出过滤，框架级）
            API key → `[REDACTED_API_KEY]`，手机号 → `[REDACTED_PHONE]`
```

### 2.2 防护栏 — 框架级强制

三个防护栏通过 `DefaultGuardRunner`（`arf/guardrails/runner.py`）组合，在引擎中统一调用：

| 防护栏 | 位置 | 类型 | 行为 |
|--------|------|------|------|
| `NoneInputGuard` | 输入 | — | 始终放行，预留 LLM 分类器扩展点 |
| `PathCheckToolGuard` | 工具参数 | 硬阻断 | 递归扫描参数中的路径字符串（跳过内容字符串）；默认仅检查工作区逃逸，其他检查项通过 `SandboxConfig.checks` 按需启用 |
| `ToolPermissionChecker` | 工具参数 | 软阻断 | deny → 阻断；ask → 审批通道；allow → 放行 |
| `RegexOutputGuard` | 输出 | 过滤 | API key → `[REDACTED_API_KEY]`，手机号 → `[REDACTED_PHONE]` |

### 2.3 PathCheckToolGuard — 路径沙箱

`arf/guardrails/path_check.py`。在每次工具调用前执行（`graph.py`），递归检查所有参数中的路径字符串。各检查项通过 `SandboxConfig.checks` 独立开关，默认仅启用 `workspace_containment`：

```yaml
# agent.yaml
advanced:
  sandbox:
    checks:
      path_traversal: false          # 目录穿越（..）
      absolute_path: false           # 绝对路径（/）
      workspace_containment: true    # 工作区逃逸（默认唯一开启）
      symlink: false                 # 符号链接检测
```

检查时自动跳过文件内容字符串（含换行符或长度 >500 字符的字符串视为内容而非路径），避免 `/* CSS 注释 */` 等被误判为路径。检查顺序：内容跳过 → 目录穿越 → 绝对路径 → 深度/数量配额 → 符号链接 → 工作区逃逸。

`PathSandbox.has_symlink()`（`arf/sandbox/path_sandbox.py`）从根目录向下逐段检查原始路径的每个组件是否为符号链接——在 `resolve()` 之前检测，防止 symlink 劫持逃逸。

`PathSandbox` 还提供以下实用方法：

| 方法 | 说明 |
|------|------|
| `validate_command(command)` | 检查命令字符串是否包含危险 shell 模式（`;`、`&&`、`|`、`$(`、`` ` `` 等） |
| `resolve_path(path_str)` | 将路径字符串相对于工作区根目录解析为绝对 `Path` 对象 |
| `allowed_dirs()` | 返回可写目录列表（构造时传入的 `writable_dirs`） |

`ResourceQuota` 支持三个可选限制：

| 配额 | 类型 | 说明 |
|------|------|------|
| `max_path_count` | `int \| None` | 单次调用最多检查的路径字符串数量 |
| `max_path_depth` | `int \| None` | 单个路径的最大目录深度（`Path.parts` 长度） |
| `deny_symlinks` | `bool` | 是否拦截 symlink 穿越（默认 `True`） |

检测顺序（首次失败即返回）：
1. 路径穿越（`..`）
2. 绝对路径（以 `/` 开头）
3. 路径深度超配额
4. 路径数量超配额
5. 符号链接穿越（可配置）
6. 解析后工作区逃逸（PathSandbox containment）

### 2.4 双源隔离 — 应用层约定

> **以下隔离为应用层约定，非框架强制。**

框架资源的"只读"和用户工作区的"读写"分离通过以下方式实现：

| 区域 | 权限 | 实现方式 |
|------|------|----------|
| 框架资源（`arf/`） | 约定只读 | 不在工具可写路径内；`PathCheckToolGuard` 阻断绝对路径间接保护 |
| 用户工作区 | 读写 | 所有内置文件工具硬编码 `WORKSPACE = Path("workspaces/default")` |
| 系统资源标记 | UI 提示 | 前端对系统工具/技能显示"(只读)"标签 |

框架提供的是 `PathCheckToolGuard`——保证工具调用不会逃逸工作区边界。至于"框架文件不可写"这一约束，依赖工具实现遵守工作区约定，框架未在代码层面强制。

### 2.5 权限分级 — deny → ask → allow

`ToolPermissionChecker`（`arf/guardrails/permissions.py`）：

```python
def check(self, tool_name: str, params: dict) -> str:
    # 1. 内建危险模式匹配（rm -rf /, sudo, curl|sh 等）→ "deny"
    # 2. 配置 deny 列表匹配 → "deny"
    # 3. 配置 ask 列表匹配 → "ask"
    # 4. 配置 allow 列表匹配 → "allow"
    # 5. 以上都不匹配 → "ask"（安全默认）
```

`ToolPermissionChecker` 内置两组默认规则：

- `_DEFAULT_ALLOW_TOOLS`：默认允许工具列表，包含 `["file_reader", "web_search", "web_fetch", "memory_store", "resource_loader", "resource_registrar", "resource_scaffold"]`
- `_BUILTIN_DENY_PATTERNS`：内置危险模式列表，包含 `["rm -rf /", "sudo ", "chmod 777 /", "> /dev/sda", "curl.*|.*sh", "wget.*|.*sh"]`

当 `agent.yaml` 中 `permissions.allow` 未配置时，`_DEFAULT_ALLOW_TOOLS` 作为默认值；`_BUILTIN_DENY_PATTERNS` 始终与配置的 `deny_patterns` 合并生效。

检查顺序：deny 优先（模式匹配 > 配置列表），其次 ask，最后 allow。引擎在 `graph.py` 中处理：

- `"deny"` → 直接阻断，emit 错误事件
- `"allow"` → 放行执行
- `"ask"` → 若 `human_loop` 审批通道已开启（`approval_enabled=True`），emit `approval_required` SSE 事件并暂停执行，等待用户在前端确认（60s 超时则自动拒绝）；若审批通道未开启（`approval_enabled=False`，即 YOLO 模式），跳过权限控制直接放行

审批通道实现（`GraphEngine._step_classify_tool_calls`）：
1. 引擎生成 `decision_id`，发射 `approval_required` 事件，`asyncio.Event.wait(60s)` 挂起
2. **SSE 流式路径（astream）**：事件通过 `yield` 直接推送 SSE → 前端展示审批 UI
3. **非流式路径（invoke）**：事件通过 `EventBus` 发射，App 层需自行订阅 EventBus
   并推送给前端（如 WebSocket 或轮询）；收到审批后调用 `engine.approve()` 解除阻塞
4. App 层调用 `engine.approve(decision_id, approved)` → Event.set()
5. 引擎恢复执行：批准 → `valid_calls`，拒绝 → `denied_calls`

### 2.6 Hook 退出码契约

Hook 作为独立子进程运行（`SubprocessHookRunner`），通过退出码与引擎协作：

| 退出码 | 行为 |
|--------|------|
| 0 | 继续执行 |
| 1 | 阻断当前操作 |
| 2 | 注入消息到对话流 |

Hook 不是安全机制（子进程行为不可信），而是框架与外部脚本的协作接口。安全边界由 `PathCheckToolGuard` 和权限配置强制保证。

### 2.7 配置

```yaml
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
    permissions:
      deny: []
      ask: [file_writer, file_deleter, python_exec]
      allow: [file_reader, web_search, web_fetch, …]
      deny_patterns: ["rm -rf", "sudo", "chmod 777"]

  human_loop:
    approval_points: tool_name_allowlist   # always_auto | tool_name_allowlist
    allowlist: [file_writer, file_deleter, python_exec]
    channel: websocket
    timeout: 60s
```

**事实校验**：`PermissionsConfig` 通过 `base.py` → `ToolPermissionChecker` 完整接入；`HumanLoopConfig` 通过 `base.py` → `GraphEngine.approval_enabled` 完整接入。`SandboxConfig`（`allow_escape`、`writable_dirs`）通过 `AdvancedConfig.sandbox` → `PathCheckToolGuard` 完整接入。

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：per-invocation 独立沙箱

当前工具在 Agent 进程内执行（`backend: function`）。一个工具的崩溃或死循环直接影响 Agent 进程。参考 seccomp 和容器隔离的思路：

1. **子进程执行**：每次调用 fork+exec 独立进程，限制 CPU/内存
2. **系统调用过滤**：限制工具进程可用的 syscall
3. **文件系统隔离**：每次调用挂载临时 overlay，调用结束后丢弃

### 3.2 MCP 协议集成

ARF 的工具解析器架构（`ToolResolver` + `ToolProvider`）支持多提供者扩展。增加 `MCPToolProvider` 后，沙箱检查在引擎层统一执行，与工具来源无关。

### 3.3 探索性方向

**审批通道增强**：支持更丰富的审批策略（如按参数值审批、审批链），审批历史记录与回溯，审批超时策略定制。
