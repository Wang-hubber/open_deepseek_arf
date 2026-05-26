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
    │       路径穿越（..）/ 绝对路径（/）/ 工作区逃逸
    │
    ├─ [3] ToolPermissionChecker.check()（软阻断，框架级）
    │       模式匹配 → deny / ask / allow
    │       deny → 阻断；ask → 放行（审批通道尚未实现）
    │
    ├─ [4] tool_executor.execute()
    │       工具在 Agent 进程内执行
    │
    └─ [5] RegexOutputGuard.check()（输出过滤，框架级）
            API key / 手机号替换为 [REDACTED]
```

### 2.2 防护栏 — 框架级强制

三个防护栏通过 `DefaultGuardRunner`（`arf/guardrails/runner.py`）组合，在引擎中统一调用：

| 防护栏 | 位置 | 类型 | 行为 |
|--------|------|------|------|
| `NoneInputGuard` | 输入 | — | 始终放行，预留 LLM 分类器扩展点 |
| `PathCheckToolGuard` | 工具参数 | 硬阻断 | 阻断 `..`、绝对路径、工作区逃逸 |
| `RegexOutputGuard` | 输出 | 过滤 | API key / 手机号替换为 `[REDACTED]` |

### 2.3 PathCheckToolGuard — 路径沙箱

`arf/guardrails/path_check.py`。在每次工具调用前执行（`graph.py`），检查所有字符串类型参数值：

```python
class PathCheckToolGuard:
    async def check(self, tool_name: str, params: dict) -> GuardResult:
        for v in params.values():
            if not isinstance(v, str):
                continue
            if ".." in Path(v).parts:       # 目录遍历
                return GuardResult(allowed=False, reason=f"Path traversal blocked")
            if v.startswith("/"):            # 绝对路径
                return GuardResult(allowed=False, reason=f"Absolute path blocked")
            if not self._sandbox.validate_path(v):  # 解析后逃逸检测
                return GuardResult(allowed=False, reason=f"Path escapes workspace")
        return GuardResult(allowed=True)
```

`PathSandbox.validate_path()`（`arf/sandbox/path_sandbox.py`）将路径对工作区根解析后做 containment 判断。工作区根在 `base.py` 设为 `tools_dir.parent`（即应用目录）。

**当前限制**：
- 只检查顶层参数值，不递归检查嵌套结构中的字符串
- 无符号链接检测（`Path.resolve()` 可能穿越 symlink）
- 无 per-invocation 资源配额

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

检查顺序：deny 优先（模式匹配 > 配置列表），其次 ask，最后 allow。引擎在 `graph.py` 中处理：`"deny"` 直接阻断并 emit 错误事件；`"ask"` 当前直接放行——审批通道尚未实现。

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

  permissions:
    deny: [python_exec, file_deleter]
    ask: [file_writer]
    allow: [file_reader, web_search, web_fetch]
```

**事实校验**：`SandboxConfig`（`allow_escape`、`writable_dirs`）已在配置模型中定义，但未接入任何 guard 或引擎逻辑。

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

**审批通道**：`check_tool_permission()` 返回 `"ask"` 时，引擎 emit `approval_required` 事件暂停执行，等待用户在前端确认。

**递归参数检查**：扩展路径检查到嵌套字典/列表中的所有字符串值，而非仅顶层。

**符号链接检测**：`PathSandbox` 中加入 symlink 解析后路径对比，防止 symlink 劫持逃逸。
