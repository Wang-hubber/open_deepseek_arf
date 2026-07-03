# audit-probe-9.11.1：Compactor + 默认 Summarizer (LLM-backed) 端到端探查

> Task 9.11.1 探查产出 — **Framework Compactor + Summarizer trait 能否让 app 端到端压缩 state？**
> 父 task doc：`docs/v1.x/phase9/task-9.11.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> **本 task 探查：Compactor::compact + 默认/自定义 Summarizer 端到端 work**

---

## §A 探查环境

- working tree：HEAD `b67b54b`（task 9.10.5）+ uncommitted `crates/arf-e2e/tests/compact_default.rs`
- 测试文件：`crates/arf-e2e/tests/compact_default.rs`（3 test cases）
- 驱动：mock Summarizer (JoinSummarizer / PrefixSummarizer) + Compactor + State 直接操作
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test compact_default -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 0.00s`**
- 关键运行输出：
  ```
  test compactor_compacts_state_messages ...
  [compact] result: messages_before=10 messages_after=4 before_tokens=1000 after_tokens=150
  [compact] state.messages.len() = 4
  [compact] tail preserved + summary inserted ✓
  ok
  test compactor_with_custom_summarizer ...
  [compact/custom] summary with custom prefix: CUSTOM_SUMMARY: [system] custom instruction text; [user] Ple...
  ok
  test compactor_compact_result_fields ...
  [compact/result] all 6 fields OK; reduction_pct = 85 ✓
  [compact/boundary] small state: no-op with empty summary ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/compact_default.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：Compactor::compact 端到端压缩

```
单元              : context_compact × §2.9
能力等级           : D（PASS）
判定依据          : state 10 messages + keep_tail=3 → compact() →
                   state.messages 长度 4（1 summary + 3 tail）✓
                   tail 内容保留（message 7/8/9 在 positions 1/2/3）✓
                   state.messages[0] 含 "[COMPACTED SUMMARY]" 头（compactor/lib.rs:127）✓
file:line         : crates/arf-compactor/src/lib.rs:82-146  Compactor::compact
                   crates/arf-compactor/src/lib.rs:122-128  state.messages = [summary] + tail
```

### 单元 2：自定义 Summarizer impl trait

```
单元              : custom_summarizer × §2.9
能力等级           : E (Extensible，PASS)
判定依据          : app 自定义 PrefixSummarizer impl Summarizer trait
                   + Compactor::with_instruction("custom instruction text") 覆盖默认
                   → summary 含 "CUSTOM_SUMMARY: " 前缀 ✓
                   → summary 含 role/content 格式 "[user] message 0" ✓
file:line         : crates/arf-compactor/src/lib.rs:51-54   Summarizer trait
                   crates/arf-compactor/src/lib.rs:73-76   with_instruction
                   crates/arf-compactor/src/lib.rs:120      self.summarizer.summarize(...)
```

### 单元 3：CompactResult 6 字段 + 边界

```
单元              : context_compact × §2.9（结果 + 边界）
能力等级           : D（PASS）
判定依据          : CompactResult 6 字段（summary / before_tokens / after_tokens /
                   messages_before / messages_after + token_reduction_pct()）：
                   - messages_before=10, messages_after=4
                   - before_tokens=1000 (10×100), after_tokens=150 (1000×0.15)
                   - state.over_view.context_tokens = 150 ✓
                   - token_reduction_pct = 85% ✓
                   边界：state 3 messages + keep_tail=5 → no-op，summary="" ✓
file:line         : crates/arf-compactor/src/lib.rs:30-47   CompactResult + token_reduction_pct
                   crates/arf-compactor/src/lib.rs:135-136  context_tokens = before * 0.15
                   crates/arf-compactor/src/lib.rs:87-96    边界：messages <= keep_tail + 1 no-op
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `context_compact × §2.9`（Compactor::compact） | **D** | 10 messages → 4 messages 端到端 OK |
| `custom_summarizer × §2.9`（自定义 Summarizer） | **E** | PrefixSummarizer + with_instruction 端到端 OK |
| `context_compact × §2.9`（结果字段 + 边界） | **D** | 6 字段 + token_reduction_pct + 边界全 OK |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `Compactor::compact` 1 接缝 split → summarize → reassemble：**D**
- `Summarizer` trait 1 方法 `summarize(&[ModelMessage]) -> String`：**D**（E 等级让 app 可扩展）
- 默认 `DEFAULT_INSTRUCTION`（compactor/lib.rs:57）：**D**（"You are a conversation summarizer..."）
- `with_instruction` 覆盖默认：**D**
- 边界 `messages.len() <= keep_tail + 1` no-op：**D**（不浪费 LLM call）
- `context_tokens` 估算 `before * 0.15`：**D**（粗估，sum of message content 实际是 more precise）

### 注意事项（潜在 issue，非 lesion）

1. **context_tokens 估算 0.15 系数是 hard-code**（compactor/lib.rs:135）—— 不考虑 message 实际内容长度。**建议**：传 `token_count_fn: impl Fn(&[ModelMessage]) -> usize` 给 Compactor，或 state 自带 `len_tokens()` 方法。
2. **Compactor.instruction 字段私有**（compactor/lib.rs:62）—— app 无法 reflect 拿当前 instruction，只能通过 `with_instruction()` 改。**合理**（无 reflect 需求），但**注意**字段 private 让 unit test 难写（test 2 用注释说明）。
3. **CompactResult 没有 timestamp / duration**——若 app 想知道 "何时 compact" / "花了多久"，须自己加。**建议**：CompactResult 加 `compacted_at: DateTime<Utc>` 字段。
4. **没有 Compactor::undo 或 diff 接口**——compact 之后旧 messages 丢失，app 想要"撤销 compact"或"对比 compact 前后"无 framework 钩子。**建议**：提供 `Compactor::compact_with_backup(state, keep_tail) -> (CompactResult, Backup)` 模式。
5. **Compactor 自身是 mutator + 调外部 LLM（async）**——若 summarizer 抛错，state.messages **未**被更新（compactor/lib.rs:120 之前 self.summarizer.summarize 抛错时 line 132 state.messages = new_msgs 不会执行）。**合理**（不半成品状态），但**注意** app 须 catch CompactError。

---

## §E 探查回归

- 9.10.1-9.10.5 既有 17 test pass
- 9.11.1 新增 3 test pass
- 综合：9.10 + 9.11.1 = 20 test，**全 pass**
- 与 F-010 / F-011 / F-012 病灶**无关**——本 task 探查 Compactor，与 SessionStore 不直接相关
- 与 9.4.x pool 病灶**无关**

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| Compactor::compact 端到端 | ✓ test 1 pass（10 → 4 messages） |
| 自定义 Summarizer impl trait | ✓ test 2 pass（E 等级端到端 OK） |
| CompactResult 6 字段 + 边界 | ✓ test 3 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.11.1 探查显示 framework **Compactor + Summarizer trait** 端到端 work（3/3 pass，D/E 等级）——
> app 可用 mock Summarizer 直接压缩 state，也可用 trait 扩展接 LLM-backed summarizer。
> 这是 phase 9 压缩类别**首个 task**（9.11.x 3 个 task 中的 1），**0 新 F-lesion**。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/compact_default.rs`（~165 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.11.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**
- 待 commit
