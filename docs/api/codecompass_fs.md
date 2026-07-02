# codecompass-fs — 端到端代码理解 agent 示例

> **Phase 8 示例** — 用一个完整的 agent app 验证 ARF 框架的 MVP 能力集。
> 设计文档：`docs/dev/phase8/example-codecompass-fs-design.md`

## 1. 它是什么

codecompass-fs 是一个**单进程**代码理解 agent demo，包含：

- 1 个主 Engine（主 ReAct 对话循环）
- 2 个 peer Engine（互发 peer_message）
- 1 个 subagent 槽位（按需 spawn 处理委派任务）
- 4 个 MCP 节点（fs 本地、code/git/web 远端 stub）
- 内置 SessionStore（多会话存档 + 切换）
- 内置 Compactor（context 超阈值触发压缩）
- 内置 Mock LLM Adapter（无需 API key 即可运行）

## 2. 它能做什么

| 能力 | 实现位置 | 怎么验证 |
|---|---|---|
| **多会话存档切换** | `SessionStore`（Python 端 JSON 持久化） | CLI 启动后输入编号切换 |
| **多轮对话** | 现有 Engine ReAct 循环 | CLI 连续输入多轮 |
| **中断恢复** | checkpoint snapshot 持久化 | 重启自动从最后 checkpoint 续跑 |
| **多 MCP 节点** | 4 个 namespace 同时挂 Bus | `bus.graph()` 含 4 个 `mcp/*` |
| **DAG / 并发 tool** | 框架内置 `tool_call_set`（同 round 并发） | 1 round 内多个 tool_call 并发 |
| **subagent 委派** | `SubagentDelegate` ActionMessage + `SubagentLauncher` | `/delegate <task>` CLI 命令 |
| **peer agent 协作** | `PeerMessage`/`PeerReply` ActionMessage | `/peer <sid> <msg>` CLI 命令 |
| **skill 渐进披露** | `tools/*/SKILL.md` 由 MCP 自动扫描 | `bus.graph().nodes` 反映 skills |
| **context compact** | `Compactor` + `when_context_over` CheckpointRule | `/compact` CLI 命令 |
| **memory 操作** | `MemoryOp` ActionMessage（接口就位） | 通过 subagent_launcher 接口预留 |

## 3. 怎么跑

### 3.1 跑测试

```bash
cd /home/wangxie/open_deepseek_arf
PYTHONPATH=py-arf/python:examples/python/codecompass_fs \
    python3 -m pytest tests/e2e/test_codecompass_fs.py -v
```

预期：22 个测试全部通过。

### 3.2 跑 CLI

```bash
cd /home/wangxie/open_deepseek_arf/examples/python/codecompass_fs
python3 cli.py
```

会看到：
```
══════════════════════════════════════════════════════════════════════
 SESSIONS
══════════════════════════════════════════════════════════════════════
(no sessions yet)
  [N] + new session
  [Q] quit

> N
  title: 重构 cache 模块
  → created sess-abc12345

── Session: sess-abc12345  Title: 重构 cache 模块 ──

Commands (during chat):
  /sessions         list all sessions
  /switch <id>      switch to a different session
  /delete <id>      delete a session
  /compact          run compaction on current session
  /delegate <task>  delegate a task to a subagent
  /peer <id> <msg>  send a peer_message to another session
  /quit             save snapshot and exit
  /help             show this help

Just type to chat.
> 看一下 src/cache.py 的 LRU 实现
  << Ack: 看一下 src/cache.py 的 LRU 实现
> ^C
[bye] saving snapshot...
```

### 3.3 切换到 live LLM

```bash
export ARF_API_KEY=sk-...   # DeepSeek key
python3 cli.py --mode live
```

## 4. 项目结构

```
examples/python/codecompass_fs/
├── __init__.py
├── app.py                  # Bus + 4 MCP + 3 Engine + SessionStore + Compactor
├── cli.py                  # CLI 入口（session list + 多轮对话）
└── subagent_launcher.py    # F7: 子 agent 委派 helper

tests/e2e/
└── test_codecompass_fs.py  # 22 个 E2E 测试
```

## 5. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 单进程多 Engine | ✅ | 简化部署，演示 Bus 隔离能力 |
| Mock LLM by default | ✅ | 无 API key 即可跑 + E2E 可重复 |
| SessionStore Python 实现 | ✅ | MVP 够用；Rust 端 `arf-session` 是真生产实现 |
| Compactor Python 实现 | ✅ | 同上；接口对齐 `arf-compactor` |
| MCP 多 namespace | ✅ | 演示 multi-node 协同 |
| 远端 MCP stub | ✅ | MVP 阶段避免 HTTP 复杂度；可替换为真 MCP server |

## 6. 验证状态（2026-07-02）

- ✅ Rust workspace 全过（17 crates, ~870 tests）
- ✅ Python E2E 全过（22 tests in `tests/e2e/test_codecompass_fs.py`）
- ✅ codecompass-fs CLI 可启动、可创建 session、可多轮对话

## 7. 后续工作

- 替换远端 MCP stub 为真 HTTP server（`crates/arf-mcp/tests/fixtures/` 模式）
- 集成 arf-session Rust impl 替代 Python SessionStore
- 集成 arf-compactor Rust impl 替代 Python Compactor
- human_handoff / permission gating UI 集成
- streaming UI 渲染
