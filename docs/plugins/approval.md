# Approval Plugin — 人机审批通道

`pre_action`（execute_tools 阶段）对 `ask_list` 中的工具暂停执行，等待人工确认。

---

## 审批流程

1. 检查待执行工具是否在 `ask_list` 中
2. 发射 `approval_required` 事件（含工具名、参数、超时时间）
3. 等待 `asyncio.Event`（默认 60s 超时）
4. 批准 → 发射 `approval_resolved`，继续执行
5. 拒绝/超时 → 抛出 `ApprovalDenied`

## 公共 API

- `approve(decision_id, approved)` — 外部调用批准/拒绝

## 配置

```yaml
plugins:
  - approval

plugins_config:
  approval:
    timeout: 60        # 审批超时秒数
    ask_list:          # 需要审批的工具
      - write_file
      - delete_file
      - move_file
```

## 事件

- `approval_required` — 等待审批（含 tool_name、params、decision_id、timeout）
- `approval_resolved` — 审批完成（含 decision_id、approved）
