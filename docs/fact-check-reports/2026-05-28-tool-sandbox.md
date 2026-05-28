# ARF Fact-Check Report — 2026-05-28 — Tool Sandbox

## Summary
- **Total tests**: 59
- **Passed**: 59
- **Automated findings**: 5 (1 resolved — confirmed design intent, 2 Warning, 2 Info)

## Findings

### Resolved — DOC: "ask without approval → allow" is YOLO mode by design (§2.5, line 175)

**原 Doc 声称**: "若审批通道未配置，降级为 deny"

**代码实际**: `_step_classify_tool_calls()` 中，当 `perm == "ask"` 但 `needs_approval == False` 时（审批通道未启用），代码跳过审批流程，直接落到 `valid_calls.append(tc)` ——等同于 **allow**。

**结论**: 这是设计意图。`approval_enabled=False` 时系统处于 YOLO 模式，不做权限控制，直接放行。文档已更新为："若审批通道未开启（`approval_enabled=False`，即 YOLO 模式），跳过权限控制直接放行"。

### Warning — DOC BUG: RegexOutputGuard 替换标签不准确 (§2.2, line 78)

**Doc 声称**: "API key / 手机号替换为 `[REDACTED]`"

**代码实际**: 
- API key 替换为 `[REDACTED_API_KEY]`
- 手机号替换为 `[REDACTED_PHONE]`

**测试**: `test_redact_labels_are_specific`

**建议**: 更新文档中的替换标签为实际值。

### Warning — DOC BUG: _walk_strings 处理范围描述不完整 (§2.3, line 114-115)

**Doc 声称**: "递归遍历嵌套 dict/list 中所有字符串值"

**代码实际**: 还处理 `tuple` 和 `set`（`isinstance(obj, (list, tuple, set))`）

**测试**: `test_walk_strings_handles_tuple_and_set`

**建议**: 更新文档为 "递归遍历嵌套 dict/list/tuple/set 中所有字符串值"。

### Info — 完整性: PathSandbox 方法未在文档中提及

`PathSandbox` 有三个方法未在文档中列出：
- `validate_command(command)` — 检查危险命令（`;`, `&&`, `|`, `$()`, `` ` ``, `rm -rf /`, `sudo`）
- `resolve_path(path_str)` — 解析路径
- `allowed_dirs()` — 返回可写目录列表

**测试**: `test_validate_command_exists`, `test_resolve_path_exists`, `test_allowed_dirs_exists`

### Info — 完整性: 默认白名单/黑名单未在文档中完整列出

`ToolPermissionChecker` 中存在文档未列出的：
- `_DEFAULT_ALLOW_TOOLS`: 7 个工具（`file_reader`, `web_search`, `web_fetch`, `memory_store`, `resource_loader`, `resource_registrar`, `resource_scaffold`）
- `_BUILTIN_DENY_PATTERNS`: 6 个模式（`rm -rf /`, `sudo `, `chmod 777 /`, `> /dev/sda`, `curl.*|.*sh`, `wget.*|.*sh`）

文档 §2.7 的配置示例中 `deny_patterns` 展示了 `["rm -rf", "sudo", "chmod 777"]` 但没有说明内置规则。

**测试**: `test_default_allow_tools`, `test_builtin_deny_patterns`

## Verified Claims

### §2.2 防护栏
- [x] NoneInputGuard 始终放行
- [x] PathCheckToolGuard 硬阻断路径穿越
- [x] ToolPermissionChecker deny/ask/allow 分级
- [x] RegexOutputGuard 过滤 API key 和手机号
- [x] DefaultGuardRunner 组合四个防护栏
- [x] 文档表格列出的 4 个 guard 全部存在

### §2.3 PathCheckToolGuard
- [x] `check(tool_name, params)` 方法签名
- [x] 路径穿越 (`..`) 阻断
- [x] 绝对路径 (`/`) 阻断
- [x] `allow_escape=True` 跳过所有检查
- [x] 安全相对路径放行
- [x] `_walk_strings` 递归遍历嵌套结构
- [x] ResourceQuota: `max_path_count`, `max_path_depth`, `deny_symlinks` 默认 `True`
- [x] `count_one()` 计数和 `reset()` 重置
- [x] 深度配额超限阻断
- [x] 数量配额超限阻断
- [x] 6 步检查顺序（首次失败即返回）

### §2.5 权限分级
- [x] deny pattern 匹配 → `"deny"`
- [x] deny list 匹配 → `"deny"`
- [x] ask list 匹配 → `"ask"`
- [x] allow list 匹配 → `"allow"`
- [x] 都不匹配 → `"ask"`（安全默认）
- [x] deny 优先级高于 ask
- [x] deny 优先级高于 allow
- [x] ask 优先级高于 allow
- [x] `approval_enabled` 默认 `False`
- [x] `_pending_approvals` 和 `_approval_results` 存在

### §2.3 PathSandbox
- [x] `validate_path` 拒绝 `..` traversal
- [x] `validate_path` 允许安全路径
- [x] `has_symlink` 逐段检查符号链接
- [x] `root` property 存在

### §2.7 配置 wiring
- [x] `PermissionsConfig` 字段: deny, ask, allow, deny_patterns
- [x] `GuardrailsConfig` 字段: input="none", output="regex_clean", tool_params="path_check"
- [x] `SandboxConfig` 字段: allow_escape=False, writable_dirs=[]
- [x] `ToolPermissionChecker.__init__` 接受 config 参数
- [x] SandboxConfig → PathCheckToolGuard 完整 wiring（base.py:268-273）
- [x] PermissionsConfig → ToolPermissionChecker 完整 wiring（base.py:275-276）

### §2.6 Hook
- [x] `SubprocessHookRunner` 存在

### 其他
- [x] guardrails `__init__.py` 导出 DefaultGuardRunner, NoneInputGuard, RegexOutputGuard, PathCheckToolGuard
- [x] DefaultGuardRunner 创建所有默认 guard

## Test Suite
- **文件**: `tests/fact_check/test_tool_sandbox.py`
- **结构**: 14 个 TestClass，59 个测试方法
- **覆盖**: 文档 §2.2-§2.7 全部章节
