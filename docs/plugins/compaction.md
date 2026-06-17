# Compaction Plugin — 渐进式上下文压缩

Token 感知的三层渐进压缩系统。根据 token 用量自动升级策略：L1 截断早期工具输出 → L2 收紧保留窗口 → L3 LLM 摘要。另含 tool_output hook 的 safeguard 机制，防止单次超大工具输出跳过多层保护。

遵循 Claude Code compaction protocol。

---

## 1. 三层渐进压缩

```
Token 用量                动作
──────────────────────────────────────────
< 50% window      无事发生
≥ 50% (L1)        截断早期轮次的工具输出，保留 keep_count×2 轮
≥ 70% (L2)        收紧保留窗口至 keep_count 轮
≥ 75% (L3)        LLM 摘要 + 重置状态，进入 2 轮冷却
```

L3 触发后压缩状态归零，冷却期内不会再次压缩。

### L1/L2 — 工具输出外部化

工具结果超过 `preview_chars`（默认 100 字符）时，完整内容写入 `data/{sid}/tool_outputs/`，消息中替换为预览 + 路径引用：

```
[Tool output truncated — 12483 chars, full at data/{sid}/tool_outputs/round_3_search_content_a1b2c3d4.txt]
The first 100 chars of the output...
```

排除 `read_file`、`search_content`、`search_files`、`directory_tree`——防止截断-读取循环。

### L3 — LLM 摘要

达到 75% 阈值时，调用 LLM 将旧消息压缩为结构化摘要，插入两类 boundary marker：

```json
// 1. compact_boundary — system 消息标记压缩边界
{
  "role": "system",
  "subtype": "compact_boundary",
  "compactMetadata": {
    "trigger": "auto",           // "auto" | "manual"
    "preTokens": 98000,          // 压缩前 token 数
    "compactedCount": 42,        // 被压缩的消息数
    "summaryLength": 1200,       // 摘要长度（字符）
    "round": 12                  // 发生时的 round
  }
}

// 2. isCompactSummary — user 消息包含结构化摘要
{
  "role": "user",
  "isCompactSummary": true,
  "content": "### Decisions Made\n- ...\n\n### Current Task & Progress\n- ..."
}
```

完整历史保留在 trace JSONL 中。消息列表仅保留 active context（boundary + summary + 最近消息）。

---

## 2. 摘要结构

| 章节 | 内容 |
|------|------|
| **Decisions Made** | 关键决策、架构选择，含 WHY |
| **Current Task & Progress** | 当前任务、已完成、待完成 |
| **Key Context** | 重要事实、约束、用户偏好、技术要求 |
| **Files Modified** | 变更的文件及变更内容 |
| **Open Questions** | 未解决的问题或待决策项 |

---

## 3. Tool Output Safeguard

`tool_output` hook 在每次工具执行后触发，检查单个工具输出是否超过动态阈值：

```
threshold = window × window_ratio / avg_tools_per_round
           └─ floor: 500 chars
```

`avg_tools_per_round` 由 EMA（α=0.3）维护，防止单轮多工具拉低阈值。超过阈值时自动外部化，不等待 L1/L2。

---

## 4. 配置

```yaml
plugins:
  - compaction

plugins_config:
  compaction:
    model: deepseek-v4-flash     # LLM 摘要模型
    threshold: 0.75              # L3 触发阈值（window_size 百分比）
    keep_count: 8                # 压缩后保留的最近非工具消息数
    truncation:
      l1_threshold: 0.50         # L1 工具输出截断阈值
      l2_threshold: 0.70         # L2 窗口收紧阈值
      preview_chars: 100         # 外部化工具输出的预览字符数
      window_ratio: 0.15         # safeguard 动态阈值系数
```

### window_size 优先级

1. **ModelConfig.context_window** — 框架自动注入
2. **plugins_config.window_size** — agent.yaml 手动覆盖
3. **默认值** — 131072（128K）

通常无需手动配置——框架从模型配置自动读取：

```yaml
model_defs:
  - model: deepseek-v4
    context_window: 163840    # ← compaction 自动使用这个值
```

---

## 5. 触发方式

| 方式 | 触发条件 | 机制 |
|------|---------|------|
| auto (L1/L2) | `round_end` hook，token ≥ L1/L2 阈值 | `_maybe_truncate_or_compact()` |
| auto (L3) | `round_end` hook，token ≥ L3 阈值 | LLM 摘要 + boundary marker |
| manual | Agent 调用 `compact` 工具 | `compact_now(ctx, trigger="manual")` |
| safeguard | `tool_output` hook，单输出 ≥ 动态阈值 | 立即外部化 |

L3 触发后有 2 轮冷却。

---

## 6. 事件

| 事件 | 触发时机 | data 字段 |
|------|---------|----------|
| `compaction_start` | 压缩开始 | trigger, pre_tokens, compacting_count, keeping_count |
| `compaction_end` | 压缩完成 | compacted_count, kept_count, summary_length, total_compactions |
| `safeguard_triggered` | 超大工具输出被截断 | tool_name, original_chars, round |
