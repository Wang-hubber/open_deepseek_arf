# 任务 9.11.3：自定义 Summarizer

> Phase 9 — 9.11 I 压缩大类 · 第 3 task（依赖 9.11.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.11.1（Compactor + 默认 Summarizer 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.11.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.11.1 探查了默认 Summarizer（JoinSummarizer），9.11.2 探查了 `when_context_over` factory。
**本 task (9.11.3) 探查自定义 Summarizer 的扩展性——**
app 用 `impl Summarizer for CustomSummarizer` 写自己的 summarizer（拼接、截断、调用 LLM、...），
能否被 `Compactor::new(Arc<dyn Summarizer>)` 接受？

**Framework 现状**（待探查确认）：
- `Summarizer` trait（compactor/lib.rs:51-54）：1 async 方法 `summarize(&[ModelMessage]) -> String`
- `Compactor::new(summarizer: Arc<dyn Summarizer>)`（compactor/lib.rs:66-71）
- `CompactError::Llm(String)` / `CompactError::NoSummary` / `CompactError::Serde(serde_json::Error)` 错误类型

**关键探查问题**（不预设答案）：
1. `impl Summarizer for CustomSummarizer` 端到端 work？3+ 个不同 impl（拼接 / 截断 / 模拟 LLM）？
2. 错误路径：summarizer 返回 `Err(CompactError::Llm("..."))` 时 Compactor 透传？state **未**被修改？
3. Compactor 与自定义 Summarizer 通过 `Arc<dyn Summarizer>` 解耦？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-compactor/src/lib.rs` 单测已用 ConcatenateSummarizer（line 224-238）—— 拼接
- 9.11.1 端到端 test 1 / test 2 已用 JoinSummarizer / PrefixSummarizer
- **本 task 不重复**：基础拼接
- **本 task 聚焦**：多种自定义策略（截断、bullet-point、模拟 LLM failure）+ trait 扩展性 + 错误路径

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`compact_custom_summarizer.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `truncate_summarizer_keeps_first_n_chars` | 自定义 TruncateSummarizer 取前 N char → state 压缩后 summary 长度 ≤ N |
| 2 | `bullet_point_summarizer_formats_per_role` | 自定义 BulletPointSummarizer 格式 "• role: content" → summary 含 bullet 格式 |
| 3 | `error_summarizer_propagates_error` | 自定义 ErrorSummarizer 永远返 `Err(CompactError::Llm("..."))` → Compactor::compact 返 Err，state **未**被修改 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub trait Summarizer\|pub enum CompactError\|impl Compactor" crates/arf-compactor/src/lib.rs
```

逐行解释：
- trait 1 方法（line 51-54）
- error 3 variant（line 19-27）
- Compactor::new 接 Arc<dyn Summarizer>（line 66-71）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test compact_custom_summarizer -- --nocapture --test-threads=1 2>&1 | tee /tmp/compact_custom_summarizer_run.log
```

逐行解释：
- 3 test 应全过（3 个不同 Summarizer impl + Compactor + 错误路径）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/compact_custom_summarizer_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：Summarizer trait 1 方法 atomic（"given msgs, return summary"）？
- A2：自定义 impl 与 ConcatenateSummarizer / PrefixSummarizer / 默认 LLM impl 正交（互不耦合）？
- A3：summarize 入参是 &[ModelMessage]（完整 model interface）？
- A4：错误处理（CompactError::Llm / NoSummary）路径 single 接缝？

**C. 输出**：`audit-probe-9.11.3.md`。
