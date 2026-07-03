# audit-probe-9.11.3：自定义 Summarizer 端到端探查

> Task 9.11.3 探查产出 — **`Summarizer` trait 扩展性 + 错误路径端到端 work？**
> 父 task doc：`docs/v1.x/phase9/task-9.11.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.11.1（Compactor + 默认 Summarizer 端到端）
> **本 task 探查：3+ 自定义 Summarizer impl + 错误路径 + state 不变量**

---

## §A 探查环境

- working tree：HEAD `ba6cc41`（task 9.11.2）+ uncommitted `crates/arf-e2e/tests/compact_custom_summarizer.rs`
- 测试文件：`crates/arf-e2e/tests/compact_custom_summarizer.rs`（3 test cases）
- 驱动：3 个 mock Summarizer (TruncateSummarizer / BulletPointSummarizer / ErrorSummarizer) + Compactor
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test compact_custom_summarizer -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 0.00s`**
- 关键运行输出：
  ```
  test bullet_point_summarizer_formats_per_role ...
  • user: this is message number 0 with some content
  • assistant: this is message number 1 with some content
  • user: this is message number 2 with some content
  • assistant: this is message number 3 with some content
  [custom/bullet] bullet format with role prefix ✓
  ok
  test truncate_summarizer_keeps_first_n_chars ...
  [custom/truncate] summary.len()=50 (max 50), state.messages.len()=3
  [custom/truncate] summary length OK, state compressed ✓
  ok
  test error_summarizer_propagates_error ...
  [custom/error] error: Llm("simulated LLM outage")
  [custom/error] state preserved on error (no half-product) ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/compact_custom_summarizer.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：TruncateSummarizer 截断

```
单元              : custom_summarizer × §2.9（自定义策略 1）
能力等级           : E (Extensible，PASS)
判定依据          : 自定义 TruncateSummarizer{max_chars=50} impl Summarizer
                   → Compactor::compact(state, 2) → summary 长度 = 50 (max)
                   → state.messages 长度 3 (1 summary + 2 tail) ✓
file:line         : crates/arf-compactor/src/lib.rs:51-54   Summarizer trait
                   crates/arf-compactor/src/lib.rs:66-71   Compactor::new
```

### 单元 2：BulletPointSummarizer 格式化

```
单元              : custom_summarizer × §2.9（自定义策略 2）
能力等级           : E (Extensible，PASS)
判定依据          : 自定义 BulletPointSummarizer impl Summarizer
                   → Compactor::compact(state, 2) → summary 含 "• user:" / "• assistant:" 格式
                   → 解析 Compactor 传入的 user_msg content（"[role] content" 格式）
file:line         : crates/arf-compactor/src/lib.rs:107-118  user_msg 构造（"[role] content"）
                   crates/arf-compactor/src/lib.rs:120       self.summarizer.summarize(&[sys, user])
```

### 单元 3：ErrorSummarizer 错误路径

```
单元              : custom_summarizer × §2.9（错误路径）
能力等级           : E (Extensible，PASS)
判定依据          : 自定义 ErrorSummarizer 永远 Err(CompactError::Llm("..."))
                   → Compactor::compact 返 Err ✓
                   → state.messages **未**被修改（半成品保护）✓
                   → state.over_view.context_tokens **未**被修改 ✓
file:line         : crates/arf-compactor/src/lib.rs:120       self.summarizer.summarize(...)? 透传
                   crates/arf-compactor/src/lib.rs:132       state.messages = new_msgs（成功路径才执行）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_summarizer × §2.9`（TruncateSummarizer） | **E** | 截断策略 + 端到端压缩 |
| `custom_summarizer × §2.9`（BulletPointSummarizer） | **E** | bullet 格式 + 端到端压缩 |
| `custom_summarizer × §2.9`（ErrorSummarizer） | **E** | 错误路径 + state 不变量保留 |

> **能力等级总结**：`custom_summarizer` = **E (Extensible)**——framework 已声明 `Summarizer`
> trait（1 async 方法），app 自行 `impl` 即可接入，framework 无需新 primitive。这与 §1.2
> spec 表中 `custom_summarizer` 对应 **E 等级** 期望一致。

---

## §D 病灶登记

### **F-013** — `Summarizer::summarize()` 实际传入的是合成的 `[system, user]`，不是原始 conversation

```
病灶 ID       : F-013
信条           : A2 正交（trait 签名与实现语义不一致）/ A3 数据唯一
Signal         : A3-S4 (同义不同形的并行类型) / A1-S2 (trait doc 描述与实现语义脱节)
触发情景       : §2.9（长会话压缩）
file:line      : crates/arf-compactor/src/lib.rs:51-54   trait 签名:
                                                            async fn summarize(
                                                                &self,
                                                                messages_to_summarize: &[ModelMessage]
                                                            ) -> Result<String, CompactError>
                                                            — 参数名 `messages_to_summarize` 暗示"待压缩的原始消息"
                 crates/arf-compactor/src/lib.rs:106-120 impl 实际:
                                                            构造 system msg (instruction) +
                                                            user msg ("[role] content\n...")
                                                            → summarizer.summarize(&[sys_msg, user_msg])
                                                            — 传入 2 条合成的 system/user，不是原始 conversation
首次登记       : audit-probe-9.11.3.md §D（本 task）
状态           : OPEN
命中形态       : trait 签名 `summarize(messages_to_summarize: &[ModelMessage])` 暗示
                "接收原始 conversation messages"，但 Compactor impl 实际传入
                2 条合成的 [system(instruction), user("Please summarize...[role] content\n...")]。
                —— BulletPointSummarizer 第一次写"取 m.role + m.content 直接
                格式 '• role: content'" 失败（拿到的 role="user" 永远是 user，
                看不到原始 role/user 的混合）→ 改为 "解析 user_msg.content
                文本"才 work。
                —— LLM-backed summarizer（如调用 GPT）能处理 system + user 这种
                chat-style input 没问题；rule-based summarizer（拼接/截断/
                提取 role）若按"原 messages"语义写会全 fail。
                后果：app 写自定义 Summarizer 时，**得知道"传入的是合成的
                chat 格式"而非"原 messages"**——这与 trait 签名暗示不一致。
                修复方向：
                方案 A: trait doc 显式说 "synthesized [system, user] chat format，
                user message content 是 '[role] msg' 格式"。
                方案 B: 拆 trait 为 `summarize_raw(&[ModelMessage])` + Compactor
                内部自动构造 chat format → 调 LLM-style summarize_chat。
                方案 C: Compactor 公开构造方法 `build_summarize_prompt(raw_msgs)`
                让 app 显式控制 chat 格式。
影响面         : 任何"非 LLM-backed 自定义 Summarizer"都得 source-dive 才写对。
                本 task 实证：BulletPointSummarizer 第一次写错 → 改 → pass。
                第三方作者失败率会很高。
复现命令       : 见 compact_custom_summarizer.rs test 2 注释 + commit 前的 fail log
```

### 注意事项（潜在 issue，非 lesion）

1. **`CompactError` 3 variant 不含 retry 信号**（compactor/lib.rs:19-27）—— `Llm(String)` 是 stringly-typed 错误，app 不知道是否可 retry。**建议**：加 `CompactError::LlmTransient(String)` / `LlmPermanent(String)` 区分。
2. **`Summarizer` trait 1 方法返回 `String`**（不是 `Result<String, ...>` 直接的 boxed error）—— 与 `Llm(String)` / `NoSummary` 配合 OK，但**注意** trait 不能返回任意 `Error`（须是 `CompactError`）。**合理**（统一错误类型）。
3. **`Compactor::compact` 不传 max_tokens 限制**—— Summarizer 任意输出长度都接受，state.messages[0] 可能很长。**建议**：加 `max_summary_chars: usize` 限制。
4. **`Summarizer` trait 不能 streaming**—— LLM 流式返回（chunk by chunk）时，app 须等全量返回才能 set state.messages[0]。**建议**：加 `async fn summarize_streaming(...) -> impl Stream<Item = String>`。

---

## §E 探查回归

- 9.11.1 / 9.11.2 既有 7 test pass
- 9.11.3 新增 3 test pass
- 综合：9.11 = 10 test（3+4+3），**全 pass**
- **F-013 是新发现**：trait 签名与 Compactor 实际调用语义不一致——所有非 LLM 自定义 Summarizer 都得 source-dive
- 与 F-010 / F-011 / F-012 病灶**无关**——本 task 探查 Summarizer trait 扩展性
- 与 9.4.x pool 病灶**无关**

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 自定义 Summarizer 多策略端到端 | ✓ test 1 (truncate) / test 2 (bullet) pass |
| 错误路径 + state 不变量 | ✓ test 3 pass（state 保留） |
| 能力等级 E (Extensible) | ✓ E 端到端确认 |
| 预期 0 新 F-lesion | ✗ **1 新 F-lesion F-013**（trait 签名 vs 实现语义脱节） |

> 结论：9.11.3 探查显示 framework **`Summarizer` trait 扩展性**端到端 work（3/3 pass，E 等级）——
> app 可用 `impl Summarizer for CustomSummarizer` 写截断/格式化/任何策略。但暴露
> **F-013**——trait 签名暗示"原 messages"而 Compactor 实际传入"合成的 [system, user] chat"，
> 所有非 LLM 自定义 Summarizer 都得 source-dive。这是 phase 9 压缩类别
> **唯一 F-lesion**（9.11.1 / 9.11.2 0 lesion，9.11.3 1 lesion）。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/compact_custom_summarizer.rs`（~150 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.11.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（按 task spec 不修改；F-013 登记在 §D）
- 待 commit
