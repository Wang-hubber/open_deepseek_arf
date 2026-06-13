# Compaction Plugin — 结构化上下文压缩

Token 感知的上下文窗口压缩。当 token 用量达到阈值时，将旧消息压缩为 LLM 摘要，插入 structured boundary marker + summary message。

遵循 Claude Code compaction protocol。

---

## 压缩协议

达到阈值时，在消息列表中插入两条标记：

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

完整历史保留在 trace JSONL 文件中。消息列表仅保留 active context（boundary + summary + 最近消息）。

---

## 摘要结构

系统提示词引导模型生成包含以下章节的结构化摘要：

| 章节 | 内容 |
|------|------|
| **Decisions Made** | 关键决策、架构选择，含 WHY |
| **Current Task & Progress** | 当前任务、已完成、待完成 |
| **Key Context** | 重要事实、约束、用户偏好、技术要求 |
| **Files Modified** | 变更的文件及变更内容 |
| **Open Questions** | 未解决的问题或待决策项 |

---

## 配置

```yaml
plugins:
  - compaction

plugins_config:
  compaction:
    model: deepseek-v4-flash    # 用于生成摘要的模型
    threshold: 0.75             # 触发阈值（window_size 的百分比）
    window_size: 131072         # Context window token 上限
    keep_count: 8              # 压缩后保留的最近非工具消息数
```

## 触发方式

- **auto** — `round_end` hook 检测 `last_token_usage >= threshold × window_size`，自动触发
- **manual** — Agent 调用 `compact` 工具主动压缩
- **冷却** — 触发后冷却 2 轮，防止连续重复压缩

## 事件

- `compaction_start` — 压缩开始（含 trigger、pre_tokens、compacting_count、keeping_count）
- `compaction_end` — 压缩完成（含 compacted_count、kept_count、summary_length、total_compactions）
