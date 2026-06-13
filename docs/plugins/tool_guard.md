# Tool Guard Plugin — 模式感知的安全防护

`pre_action`（execute_tools 阶段）拦截所有工具调用。根据当前会话模式执行不同策略。

---

## 模式感知门控

tool_guard 读取 ControlPlane 注入的 `effective_mode`（auto/plan/ask），在不同模式下执行不同策略：

### auto 模式 — 全部放行

跳过所有检查，所有工具直接执行。适用于信任环境或自动化脚本。

### plan 模式 — 只读

通过 `annotations.readOnlyHint` 判定工具是否有副作用：
- `readOnlyHint: true` → 放行
- `readOnlyHint: false` → 拒绝
- 未声明 → 拒绝 + **WARNING 日志** "add annotations to tool.yaml"

**工具必须声明 `annotations.readOnlyHint`，否则 plan 模式下会被拦截。**

### ask 模式 — 列表匹配

走 deny → ask → allow 标准权限检查：
- **deny** — 立即拒绝，发射 `guard_block` 事件，注入 blocked ToolResult
- **ask** — 路由到 ApprovalPlugin，等待人工审批
- **allow** — 直接放行，发射 `guard_pass` 事件

支持 `deny_patterns` 正则匹配工具名。

---

## 路径沙箱

通过 `PathSandbox` 扫描工具参数中的路径遍历攻击：
- `..` 穿越
- 绝对路径（`/etc/passwd`）

检测到违规时发射 `guard_block` 事件，注入 blocked 结果，抛出 `SandboxViolation`。

---

## 配置

所有 plugin 统一通过 `plugins_config` 配置。工具名使用裸名（框架自动解析为 namespaced 名）：

```yaml
plugins:
  - tool_guard

plugins_config:
  tool_guard:
    deny: [rm, bash, python_exec]
    ask: [write_file, delete_file, move_file]
    allow: [read_text_file, search_files]
    deny_patterns: ["mcp__*"]
    sandbox_check: true
```

---

## 事件

- `guard_block` — 工具被拒绝（含拒绝原因：deny_list / sandbox / plan_mode）
- `guard_pass` — 工具通过所有检查

---

## Tool 开发要求

每个工具的 `tool.yaml` 必须声明 `annotations.readOnlyHint`：

```yaml
# 只读工具
annotations:
  readOnlyHint: true

# 有副作用的工具
annotations:
  readOnlyHint: false
  destructiveHint: true   # 可选：标注破坏性
```

未声明的工具在 plan 模式下会被拒绝。
