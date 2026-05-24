# Tool Sandbox & Security Boundaries

ARF 将工具调用视为系统调用，通过路径沙箱、参数校验和双源隔离构建安全边界。参照 OS 的保护环模型：框架资源 Ring 0（只读），用户工作区 Ring 3（读写）。

## Architecture

```
LLM 生成 tool_call
    │
    ▼
GraphEngine
    │
    ├─ [1] guard_runner.check_tool_params(name, params)
    │       PathCheckToolGuard: 阻断 .. / 绝对路径 / 工作区逃逸
    │       返回 allowed=True/False
    │
    ├─ [2] tool_executor.execute(valid_calls)
    │       ConcurrentToolExecutor → DefaultToolResolver
    │       → StaticYamlToolProvider → FunctionBackend
    │
    └─ [3] guard_runner.check_output(response)
            RegexOutputGuard: 过滤 API key 等敏感模式
```

## 防护栏

三个防护栏组合在 `DefaultGuardRunner` 中：

| 防护栏 | 类型 | 行为 |
|--------|------|------|
| `NoneInputGuard` | input | 始终放行（预留 LLM 分类器位置） |
| `RegexOutputGuard` | output | 替换 API key 为 `[REDACTED_API_KEY]` |
| `PathCheckToolGuard` | tool | 阻断 `..` 和绝对 `/` 路径 |

### PathCheckToolGuard

`arf/guardrails/path_check.py` — 在所有工具调用前执行参数校验：

- 检查所有字符串参数值中是否包含 `..`（目录遍历）
- 检查字符串参数值是否以 `/` 开头（绝对路径）
- `workspace_root` 从 `tools_dir` 父目录推导

```python
class PathCheckToolGuard:
    def __init__(self, workspace_root: str):
        self._root = workspace_root

    async def check(self, tool_name: str, params: dict) -> GuardResult:
        for v in params.values():
            if isinstance(v, str) and ".." in Path(v).parts:
                return GuardResult(allowed=False, reason="Path traversal blocked")
            if isinstance(v, str) and v.startswith("/"):
                return GuardResult(allowed=False, reason="Absolute path blocked")
        return GuardResult(allowed=True)
```

## 双源隔离

| 区域 | 权限 | 内容 |
|------|------|------|
| 框架资源 (Ring 0) | 只读 | `arf/` 内置工具、技能、模型定义 |
| 用户工作区 (Ring 3) | 读写 | `workspaces/default/`、`tools/`、`skills/` |
| 系统路径 (禁止) | 无访问 | `tools/`、`skills/`、`models/` 目录（由 `file_writer` 应用层保护） |

## Hook 退出码契约

六个生命周期事件，Hook 作为独立子进程运行：

| 退出码 | 行为 |
|--------|------|
| 0 | 继续执行 |
| 1 | 阻断当前操作 |
| 2 | 注入消息到对话流 |

## 配置

```yaml
# agent.yaml — 防护栏配置
advanced:
  guardrails:
    input: none               # none | regex_block | llm_classifier
    output: regex_clean       # none | regex_clean | llm_classifier
    tool_params: path_check   # none | path_check | command_check

  sandbox:
    allow_escape: false       # 是否允许工作区外访问
    writable_dirs: []         # 额外可写目录白名单
```

## 当前限制与演进方向

| 当前 | 演进 |
|------|------|
| 工具在 Agent 进程内执行 | 每次调用独立沙箱隔离 |
| 路径检查仅覆盖顶层字符串参数 | 递归检查嵌套结构 |
| 无 per-invocation 资源配额 | CPU、内存、网络限制 |
| 无 deny→ask→allow 人机回路 | 高风险操作需用户审批 |
| 无 MCP 协议支持 | MCP 工具提供者集成 |
| 无符号链接检测 | `Path.resolve()` + `is_relative_to()` 校验 |

## 验证

```python
# Path traversal blocked
await guard.check("file_reader", {"path": "../../../etc/passwd"})
# → GuardResult(allowed=False, reason="Path traversal blocked: '../../../etc/passwd'")

# Absolute path blocked
await guard.check("file_reader", {"path": "/etc/passwd"})
# → GuardResult(allowed=False, reason="Absolute path blocked: '/etc/passwd'")

# Permission deny by pattern
pc.check("python_exec", {"code": "sudo rm -rf /"})  # → "deny"

# Safe tool auto-allowed
pc.check("file_reader", {"path": "hello.txt"})  # → "allow"

# Unknown tool asks
pc.check("python_exec", {"code": "print(1)"})  # → "ask"
```

## 与 OS 模式的对应

| OS 概念 | ARF 实现 |
|---------|----------|
| 系统调用门控 | `PathCheckToolGuard.check()` 校验每个 tool_call |
| 保护环 (Ring 0–3) | 框架资源只读 / 用户空间读写 |
| 访问控制列表 | `writable_dirs` 白名单 |
| 中断向量 | Hook 退出码 (0/1/2) |
| 进程隔离 | 工具同进程执行（演进方向：独立沙箱） |
