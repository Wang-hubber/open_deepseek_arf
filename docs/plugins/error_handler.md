# Error Handler Plugin — 错误恢复路由

`error` hook 捕获引擎异常，按异常类型和错误文本执行分类恢复。

---

## 五种恢复动作

| 动作 | 触发条件 | 行为 |
|------|---------|------|
| **fallback (compact)** | 上下文溢出 / token 超限 | 触发 compaction 后继续 |
| **retry** | 瞬时传输错误（timeout/connection） | 指数退避重试（1s→2s→4s…），带随机抖动 |
| **skip** | 审批拒绝 / guard 阻止 | 跳过当前 turn，继续下一轮 |
| **repair (fallback)** | 消息合约违规 | 修复消息格式后继续 |
| **abort** | 未知错误 / 超出重试预算 | 中止会话 |

## 配置

```yaml
plugins:
  - error_handler

advanced:
  recovery:
    max_continuation: 3      # 最大 fallback 次数
    max_compaction: 3        # 最大压缩回退次数
    max_transport_retry: 3   # 最大重试次数
    backoff_base: 1.0        # 退避基数（秒）
    backoff_max: 30.0        # 最大退避（秒）
```

## 异常类

- `MessageContractError` — 消息合约违规
- `ApprovalDenied` — 审批拒绝/超时
- `PermissionDenied` — 权限拒绝
- `SandboxViolation` — 沙箱违规
