# Tool Guard Plugin — 双层安全防护

`pre_action`（execute_tools 阶段）拦截所有工具调用，执行双层安全检查。

---

## 第 1 层：权限策略

通过 `PermissionRegistry` 按 deny → ask → allow 优先级检查：

- **deny** — 立即拒绝，发射 `guard_block` 事件，注入 blocked ToolResult
- **ask** — 路由到 ApprovalPlugin，等待人工审批
- **allow** — 直接放行，发射 `guard_pass` 事件

支持 `deny_patterns` 正则匹配工具名。

## 第 2 层：路径沙箱

通过 `PathSandbox` 扫描工具参数中的路径遍历攻击：
- `..` 穿越
- 绝对路径（`/etc/passwd`）
- 符号链接目标越界

检测到违规时发射 `guard_block` 事件，注入 blocked 结果，抛出 `SandboxViolation`。

## 配置

```yaml
plugins:
  - tool_guard

advanced:
  guardrails:
    permissions:
      deny: [rm, bash]
      ask: [file_writer, file_deleter]
      allow: [read_text_file, search_files]
      deny_patterns: ["mcp__*"]
    sandbox:
      workspace_root: ./workspace
      blacklist: [/etc, /proc, /sys]
```

## 事件

- `guard_block` — 工具被拒绝（含拒绝原因：deny_list / sandbox / permission）
- `guard_pass` — 工具通过所有检查
