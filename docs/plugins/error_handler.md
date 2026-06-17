# Error Handler Plugin — 错误分类恢复路由

`error` hook 拦截引擎异常，按异常类型和错误文本做分类恢复。不设恢复策略的未知错误原样上抛，引擎中止会话。

---

## 1. 恢复决策

ErrorHandler 通过 `ctx.hook_data["_recovery_decision"]` 向引擎返回恢复指令。引擎内置 6 种 handler：

| 决策 | 触发条件 | 引擎行为 |
|------|---------|---------|
| **persist_state** (compact) | 上下文溢出（context too large/exceed） | 状态落盘 + 触发 compaction，冷却计数递增 |
| **retry_turn** | 瞬时传输错误（timeout/rate/unavailable/connection） | 指数退避等待后重试当轮 |
| **persist_state** (repair) | 消息合约违规（MessageContractError） | 状态落盘 + 修复消息格式 |
| **inject_tool_error** | guard/approval 阻止（pre_action + 有待执行工具调用） | 注入工具错误结果，模型自行重试 |
| **inject_tool_error** | 工具执行失败（execute_tools 阶段） | 注入工具错误结果，模型自行重试 |
| **noop** | guard/approval 阻止（非 pre_action 或已无待执行工具） | 不做任何事，引擎正常继续 |

### 耗尽行为

retry 和 compact 有次数上限。达到上限后**不设恢复决策**——引擎将其视为不可恢复错误，抛 `SessionAbortedError` 中止会话。

---

## 2. 错误分类逻辑

```
异常进入
  ├─ "context too large" / "context exceed"       → persist_state (compact)
  │     └─ compact_attempts 耗尽                  → 不设决策 → 会话中止
  │
  ├─ "timeout" / "rate" / "unavailable" /
  │   "connection" / "timed out"                  → retry_turn
  │     └─ transport_attempts 耗尽                → 不设决策 → 会话中止
  │
  ├─ MessageContractError / "contract"            → persist_state (repair)
  │
  ├─ PermissionDenied / ApprovalDenied /
  │   SandboxViolation / ApprovalTimeout
  │     ├─ pre_action 阶段 + 有待执行工具          → inject_tool_error
  │     └─ 否则                                   → noop
  │
  ├─ execute_tools 阶段（工具执行失败）             → inject_tool_error
  │
  └─ 未知错误                                      → 不设决策 → 会话中止
```

`_error_phase` 由引擎在抛异常前注入 `ctx.hook_data`，取值 `pre_action` 或 `execute_tools`。

---

## 3. 恢复状态追踪

引擎在 state 中维护 `_recovery` 计数器：

```python
state["_recovery"] = {
    "transport_attempts": 0,   # retry_turn 已用次数
    "compact_attempts": 0,     # persist_state(compact) 已用次数
    "continuation_attempts": 0,  # 保留字段
}
```

每次触发对应决策时递增，达到上限后恢复策略耗尽。

---

## 4. 异常类

| 异常 | 来源 | 分类 |
|------|------|------|
| `MessageContractError` | 消息验证失败 | contract → repair |
| `PermissionDenied` | 权限拒绝 | guard → inject_tool_error / noop |
| `ApprovalDenied` | 审批拒绝 | guard → inject_tool_error / noop |
| `ApprovalTimeout` | 审批超时 | guard → inject_tool_error / noop |
| `SandboxViolation` | 沙箱违规 | guard → inject_tool_error / noop |
| `RateLimitError` | 速率限制 | (未显式分类，走未知错误) |
| `CircuitOpenError` | 熔断器断开 | (未显式分类，走未知错误) |

---

## 5. 配置

```yaml
plugins:
  - error_handler

plugins_config:
  error_handler:
    max_continuation: 3       # 最大 continuation 次数
    max_compaction: 2         # 最大 compact 回退次数（默认 2）
    max_transport_retry: 3    # 最大 transport 重试次数
    backoff_base: 1.0         # 退避基数（秒）
    backoff_max: 30.0         # 退避上限（秒）
```

退避公式：`delay = min(backoff_base × 2^attempts, backoff_max) + random(0, 1)`。

---

## 6. 事件

error hook 中引擎通过 trace 发射两个事件：

| 事件 | 触发时机 | data 字段 |
|------|---------|----------|
| `error_decision` | 每次恢复决策下发 | recovery, reason, error_type, error_message |
| `error_abort` | 会话中止前 | reason, error_type, error_message |

---

## 7. 引擎协议

```
异常捕获
  → 注入 ctx.hook_data["exception"] = exc
  → 注入 ctx.hook_data["_error_phase"] = "pre_action" | "execute_tools"
  → fire error hook → ErrorHandlerPlugin.on_hook()
  → 读取 ctx.hook_data["_recovery_decision"]
    ├─ 有决策 → 调用对应 recovery handler → 继续执行
    └─ 无决策 → 发射 error_abort 事件 → raise SessionAbortedError
```
