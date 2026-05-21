# Trace Viewer 全生命周期展示增强

## 背景

后端 trace 系统已采集 15 种事件类型，但前端 TraceView 只渲染了部分 graph 节点。
大量 lifecycle 事件和 hook 细节未展示。

## 目标

将 TraceView 升级为 session 全生命周期监控面板：所有事件按时间戳统一时间线排列，
支持元数据展开和 session 级别导出。

## 设计

### 统一时间线

所有事件（lifecycle + graph）按 `created_at` 时间戳混排为一条时间线，不再区分
session 级 / turn 级。时间线左侧标注时间戳（HH:MM:SS），事件卡片按发生顺序排列。

```
 10:00:48 │ lifecycle.session_start       HTTP, workspace=...
 10:00:48 │ hook_execution SessionStart   exit=0, 172ms
 10:00:57 │ graph.hook PreModelCall       quick_thinking, 187ms
 10:00:57 │ graph.classify                medium → quick_thinking
 10:01:00 │ graph.call_model              quick_thinking, 3850 tokens, 3.2s
 10:01:00 │ graph.hook PostModelCall      ok, 188ms
 10:01:10 │ graph.hook PreToolUse         search_file, 203ms
 10:01:10 │ graph.execute_tools           search_file, ok, 450ms
 10:01:10 │ graph.hook PostToolUse        search_file, ok, 203ms
 10:01:12 │ lifecycle.compaction          5 turns → 3, tokens: 12K→8K
 10:01:15 │ graph.respond                 truncated=false
 10:01:15 │ lifecycle.handoff             user_agent_complete, intent=...
 10:01:33 │ lifecycle.session_end         stream_done, 6msgs, 45s
 10:01:33 │ hook_execution SessionEnd     session_archiver, exit=0
```

### 事件卡片

每个卡片包含：
- **左侧时间**：`HH:MM:SS.mmm`
- **事件类型标签**：彩色 tag 区分 lifecycle / graph / hook
- **摘要行**：关键字段（model、duration、status、tokens 等，按事件类型适配）
- **metadata 折叠**：点击展开原始 JSON（等宽字体、默认折叠）
- **复制按钮**：复制单条 trace 的 JSON

### 顶部汇总面板

Session 详情顶部卡片（现有 event_count / total_tokens / total_duration_ms 之上）增加：
- 模型调用次数 / 工具调用次数
- Hook 成功/失败/阻塞统计

### Session 导出

顶部工具栏 **"导出 Session Trace"** 按钮 → `GET /api/traces/export?session_id=...` → 浏览器下载完整 JSON。

## 事件类型 → 摘要展示

| 事件类型 | 标签色 | 摘要行关键字段 |
|---|---|---|
| lifecycle.session_start | 蓝 | transport, workspace |
| lifecycle.session_end | 蓝 | trigger, message_count, duration |
| lifecycle.hook_execution | 紫 | hook_name, hook_event, exit_code, duration |
| lifecycle.handoff | 青 | phase, intent, turns_used |
| lifecycle.compaction | 橙 | turns_before→after, tokens_before→after |
| lifecycle.prompt_snapshot | 灰 | prompt_hash, tools_count |
| lifecycle.model_switch | 黄 | from→to model, tool |
| lifecycle.init | 灰 | stage, model/tool/skill counts |
| lifecycle.config | 灰 | action |
| graph.classify | 绿 | classification, resolved_model, duration |
| graph.call_model | 绿 | model, tokens, duration |
| graph.hook | 紫 | hook_event, tool_name/model, status |
| graph.execute_tools | 绿 | tool_name, status, duration |
| graph.respond | 绿 | truncated, response_snippet |
| graph.recovery | 红 | recovery_type, error_msg |

## 文件变更

| 文件 | 变更 |
|------|------|
| `frontend/src/views/TraceView.vue` | 核心改动：统一时间线渲染、新事件卡片、metadata 展开、导出按钮、汇总面板 |
| `frontend/src/types/index.ts` | 补充 lifecycle metadata 类型（TraceMetadataLifecycle） |

## 后端

无需变更。

## 验收

- [ ] 15 种事件类型全部出现在时间线中
- [ ] 事件按时间戳排序，左侧显示时间
- [ ] 每个事件可展开 metadata JSON
- [ ] 每个事件可复制单条 JSON
- [ ] SessionStart / SessionEnd 的 hook_execution 可见
- [ ] "导出 Session Trace" 按钮可用，下载完整 JSON
- [ ] 汇总面板展示 hook / model_call / tool_call 统计
- [ ] 现有 turn-by-turn 视图可切换保留（兼容）
