# 任务 9.11.1：Compactor + 默认 Summarizer（LLM-backed）

> Phase 9 — 9.11 I 压缩大类 · 第 1 task（依赖 9.10.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.11.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.10.x 探查了持久化。**本 task (9.11.1) 探查 context_compact 能力——**
framework 提供 `Compactor` + `Summarizer` trait，能否让 app 通过 `Compactor::compact(state, keep_tail)`
端到端把 state.messages 压缩为 1 system + tail？

**Framework 现状**（待探查确认）：
- `arf_compactor::Compactor::compact(state, keep_tail)`（compactor/lib.rs:82-146）：
  - 1 summary + keep_tail messages
  - 调 `summarizer.summarize()` 拿 summary
  - state.messages = [summary_sys_msg] + tail
  - state.over_view.context_tokens = before * 0.15
- `arf_compactor::Summarizer` trait（compactor/lib.rs:51-54）：1 async 方法 `summarize(&[ModelMessage]) -> String`
- `Compactor::with_instruction(custom)` 覆盖默认 instruction
- `CompactResult { summary, before_tokens, after_tokens, messages_before, messages_after }`

**关键探查问题**（不预设答案）：
1. `Compactor::compact()` 端到端 work？state.messages 真的从 N → 1 + keep_tail？
2. `CompactResult` 4 字段值正确？
3. 默认 instruction 是什么？能被 `with_instruction()` 覆盖？
4. 边界：messages <= keep_tail + 1 时 compact "不做事"？
5. 边界：context_tokens 估算公式 before * 0.15 实际效果？
6. mock Summarizer（ConcatenateSummarizer）端到端可工作？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-compactor/src/lib.rs` 单测已测 compact_reduces_messages_and_tokens / compact_preserves_tail_in_order / when_context_over_builds_rule
- **本 task 不重复**：单测字段 round-trip
- **本 task 聚焦**：端到端 probe——Compactor + mock Summarizer + state 验证

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`compact_default.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `compactor_compacts_state_messages` | state 10 messages + keep_tail=3 → compact → state.messages 长度 4（1 summary + 3 tail）；tail 内容保留 |
| 2 | `compactor_with_custom_summarizer` | 自定义 Summarizer impl trait（返回 "CUSTOM_SUMMARY: " 前缀）→ compact → 验证 summary 用自定义结果 |
| 3 | `compactor_compact_result_fields` | CompactResult 4 字段（summary / before_tokens / after_tokens / messages_before/after）值正确；token_reduction_pct 计算正确 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub fn compact\|pub fn new\|pub fn with_instruction\|pub trait Summarizer\|pub struct CompactResult" crates/arf-compactor/src/lib.rs
```

逐行解释：
- Compactor::compact 是 1 接缝（line 82-146）：split → summarize → reassemble
- Summarizer trait 1 方法（line 51-54）：summarize(&[ModelMessage]) -> String
- CompactResult 6 字段（line 30-37）：summary, before_tokens, after_tokens, messages_before, messages_after + token_reduction_pct()

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test compact_default -- --nocapture --test-threads=1 2>&1 | tee /tmp/compact_default_run.log
```

逐行解释：
- 3 test 应全过（mock Summarizer + Compactor + state 验证）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/compact_default_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：Compactor 单一职责（"compact 1 state"）？
- A2：Summarizer trait 1 方法 → 1 实现正交？
- A3：summary 存 state.messages[0] 单点声明？
- A4：context_tokens 估算 0.15 系数 + before/after tokens 在 single source？

**C. 输出**：`audit-probe-9.11.1.md`。
