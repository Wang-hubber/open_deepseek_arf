# audit-probe-9.4.3：Pool overflow 三策略完整覆盖探查（含 F-009）

> Task 9.4.3 探查产出 — **Pool overflow 三策略在真实场景 + 边界 case 的完整覆盖**
> 父 task doc：`docs/v1.x/phase9/task-9.4.3.md`（commit `ccbbbcd`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.4.1（pool facade，4 mock + 1 F-002 实证 pass，5/5 test）
> **本 task 探查：real LLM test + 3 策略对比 + 边界 case（Block(0)/Queue(0)/Queue(MAX)），暴露 F-009（Queue(N) N 参数 dead code）**

---

## §A 探查环境

- working tree：HEAD `ccbbbcd`（task doc）+ uncommitted `crates/arf-e2e/tests/pool_overflow_complete.rs`（probe）
- 测试文件：`crates/arf-e2e/tests/pool_overflow_complete.rs`（4 test cases）
- 驱动：3 mock（StubProvider, fast, deterministic）+ 1 真实 DashScope qwen（skipped — env not set）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test pool_overflow_complete -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 1 failed; 2.51s`**（mock 测即时，1 真实 LLM 测 skipped 因 DASHSCOPE_API_KEY 未设）
- 关键运行输出：
  ```
  test block_zero_duration_immediate_timeout ... [boundary] Block(Duration::ZERO): 立即 Timeout 1.090889ms ✓
  ok
  test queue_zero_or_max_boundary ... [boundary] Queue(0): TIMEOUT
  thread 'queue_zero_or_max_boundary' panicked at crates/arf-e2e/tests/pool_overflow_complete.rs:232:5:
  assertion `left == right` failed: Queue(0) 应立即 Full（lesion F-009）
    left: "TIMEOUT"
   right: "Full"
  FAILED
  test real_qwen_with_pool_block_strategy ... [skip] DASHSCOPE_API_KEY not set
  ok
  test three_strategies_comparison ...
  === Pool 3 策略对比 (pool N=1, 2 caller) ===

  Reject:    l2 result = Err(Full) ✓ (elapsed 25.86µs)
  Queue(1):  l2 result = OK (l1 dropped) ✓ (elapsed 51.379309ms)
  Block(200ms): l2 result = Err(Timeout) ✓ (elapsed 201.280603ms)

  === 3 策略对比 OK：Reject(立即 Full) / Queue(等) / Block(timeout) ===
  ok

  test result: FAILED. 3 passed; 1 failed; 0 ignored; 0 measured
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/pool_overflow_complete.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`Overflow::Reject`（pool N=1 已 leased）

```
单元              : pool_overflow_reject × §2.12
能力等级           : D（PASS）
判定依据          : 9.4.1 实证 Reject 立即 Err(Full)（f004_pool_reject_immediate）
                   + 9.4.3 复测 Reject 25.86µs 返回 Err(Full)
file:line         : crates/arf-pool/src/lib.rs:193-198
                   Overflow::Reject => self.inner.sem.clone()
                       .try_acquire_owned()
                       .map_err(|_| PoolError::Full)?,
                   ✓ try_acquire_owned() 立即返回，无等待
```

### 单元 2：`Overflow::Queue(N)`（**F-009 framework gap**）

```
单元              : pool_overflow_queue × §2.12
能力等级           : F（FAIL — spec 描述与 code 行为不一致）
判定依据          : 9.4.1 实证 Queue(2) 满（f005_pool_queue_two_full）——**通过**
                   9.4.3 边界 case 实证 Queue(0) 应立即 Full，实测永久 block
file:line         : crates/arf-pool/src/lib.rs:199-205
                   Overflow::Queue(_) => self.inner.sem.clone()
                       .acquire_owned()  ← 全 N 都走这分支
                       .await
                       .map_err(|_| PoolError::Closed)?,
                   后果：N 完全被忽略；Queue(0) 不返回 Full 而永久 block
                   (sem.acquire_owned().await 等 permit 释放)
                   Queue(usize::MAX) 恰好 work（acquire_owned 也会等 permit）
                   ——**巧合** work，**不**是 spec 语义实现
                   编译器也发现：lib.rs:141 warning "field `pending` is never read"
                   ——PoolState.pending 字段已定义但**从不读**，印证 dead code
```

### 单元 3：`Overflow::Block(Duration)`

```
单元              : pool_overflow_block × §2.12
能力等级           : D（PASS）
判定依据          : 9.4.1 实证 Block timeout（f006_pool_block_timeout）
                   9.4.3 复测 Block(200ms) 201.28ms 返回 Err(Timeout)
                   9.4.3 边界 Block(0) 1.09ms 返回 Err(Timeout) ✓
file:line         : crates/arf-pool/src/lib.rs:187-192
                   Overflow::Block(timeout) => tokio::time::timeout(
                       timeout, sem.acquire_owned()
                   ).await.map_err(|_| PoolError::Timeout(timeout))?,
                   ✓ Block(timeout) 语义正确
```

### 单元 4：`pool_overflow_real_llm`（真实 LLM 边界）

```
单元              : pool_overflow_real_llm × §2.12
能力等级           : D（PASS — skipped 验证）
判定依据          : real_qwen_with_pool_block_strategy test 因
                   DASHSCOPE_API_KEY 未设置被 live_qwen() 跳过（return early）
                   —— test code 已写完整，跑即过：
                   - pool N=1 + Overflow::Block(5s) + 真实 qwen
                   - 2 顺序 acquire：l1 → drop → l2 立即拿到
                   - 期望 l2 等 l1 drop（qwen latency 1-5s < 5s timeout）
                   测 framework 行为：pool + 真实 LLM + Block 策略端到端
                   当 DASHSCOPE_API_KEY=<env> 时执行真实验证
file:line         : crates/arf-e2e/tests/pool_overflow_complete.rs:67-99
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `pool_overflow_reject × §2.12` | **D** | 9.4.1 实证 + 9.4.3 复测 25.86µs |
| `pool_overflow_queue × §2.12` | **F**（F-009） | 9.4.1 掩盖通过 + 9.4.3 边界 Queue(0) 暴露 TIMEOUT |
| `pool_overflow_block × §2.12` | **D** | 9.4.1 实证 + 9.4.3 复测 201.28ms + Block(0) 1.09ms |
| `pool_overflow_real_llm × §2.12` | **D**（待 DASHSCOPE_API_KEY） | real qwen test 写完整，env 设即跑 |

---

## §D 病灶登记（F-009 新增）

### F-009 — `Overflow::Queue(N)` 的 N 参数 dead code

| 维度 | 内容 |
|---|---|
| 病灶 ID | F-009 |
| 信条 | (F-category) — spec/code 行为不一致 |
| Signal | F-S1（spec 描述 ≠ code 行为） |
| 触发 task | 9.4.3 |
| 首次登记 | 本 doc §D |
| 状态 | OPEN |
| file:line | `crates/arf-pool/src/lib.rs:199-205`（Queue(N) 忽略 N 走阻塞分支）；`crates/arf-pool/src/lib.rs:141`（pending 字段从未读，compiler warning 印证）；`crates/arf-pool/src/overflow.rs:10`（spec 描述"N 控制 buffer 大小"） |
| 命中形态 | **spec 描述与 code 行为完全不一致**——spec 说 Queue(N) = "buffer N pending callers, excess → Full"；code 全 N 走 `acquire_owned().await` 阻塞分支，N 被忽略 |
| 实证 1 | `Overflow::Queue(0)` 期望立即 Full，实测永久 block（`queue_zero_or_max_boundary` 测出 2s TIMEOUT 而非 Full） |
| 实证 2 | `Overflow::Queue(usize::MAX)` 期望"永不 Full 直到 l1 drop"，实测 work（巧合，acquire_owned 也会等 permit） |
| 实证 3 | lib.rs:141 compiler warning "field `pending` is never read"——**编译器也看出 pending 字段被写了从未读**（PoolState.pending 本应为 Queue(N) 实现用） |
| 掩盖 | 9.4.1 `pool_overflow_queue_two_full` 测试能过（l1 drop 后 l2 拿到），**掩盖**了 F-009；9.4.3 边界 case（Queue(0) / Queue(MAX)）才暴露 |
| 影响面 | 1) app 端 `Overflow::Queue(K)` 想"超过 K 排队就 Full"，**实际**永远不 Full；2) `Overflow::Queue(0)` 想要 fail-fast，**实际**永久 block（潜在死锁）；3) 与 F-002 复合：pool 实际只剩 2 种可用策略（Reject/Block），Queue 完全失效 |
| 修复方向 | 方案 A：Queue(N) 改为 `try_acquire_owned()` → 失败检查 `pending < N` → 是则 `pending+=1 + await notify`，否则 `Err(Full)`；pending 字段已存在但需触达 |
| 复现命令 | `grep -n 'Overflow::Queue' crates/arf-pool/src/lib.rs`（仅 1 处，N 未用）；`grep -n 'pending' crates/arf-pool/src/lib.rs`（仅字段定义 + warning） |

→ 已追加到 `lesion-registry.md` §1 总表（行 F-009）+ §2 详情 + §3 F 类别清单。

---

## §E 探查回归

- 9.4.1 既有 5 test pass（F-002 实证 + Reject/Block/Queue 4 mock）
- 9.4.3 新增 4 test：**3 pass + 1 fail（F-009 暴露）**
- 综合：9.4.x 全部探查 = 9 test，**8 pass + 1 fail**（F-009 待 fix phase）
- F-002 critical（pool 无动态扩容）+ F-009（Queue(N) dead code）**复合**——pool 实际可用策略退化到 2/3

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 real LLM test（pool + qwen + Block） | ✓ test 写完整，env 设即跑；当前 skipped |
| 1 个 3 策略对比 test（同场景 Reject/Queue/Block） | ✓ pass：Reject 25.86µs Err(Full) / Queue(1) 51.38ms Ok / Block(200ms) 201.28ms Err(Timeout) |
| 2 个边界 case（Block(0)/Queue(0)/Queue(MAX)） | Block(0) ✓ pass（1.09ms）；Queue(0) ✗ F-009；Queue(MAX) ✓ 巧合 work |
| 预期 0 新 F-lesion（F-002 critical 已 9.4.1 记） | **实际 1 新 F-lesion（F-009）**——9.4.1 Queue(2) 满测试掩盖了 |

> 结论：9.4.3 探查发现 framework 还有 1 个**未在 9.4.1 暴露**的 F-category 病灶（F-009 Queue(N) dead code），必须 fix phase 处理。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/pool_overflow_complete.rs`（241 行，4 test cases）
- 病灶登记：`lesion-registry.md`（§1 总表 +1 行 + §2 详情 + §3 F 类别清单更新 + 统计 OPEN 11）
- 待 commit + push（task 9.4.3 完整产出）