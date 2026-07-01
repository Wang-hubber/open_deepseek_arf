# py-arf/tests/e2e — Phase 6 Python E2E Tests

Real Node full-chain E2E tests via py-arf Python bindings.

## 运行

```bash
# 配 key
export MINIMAX_API_KEY=sk-...

# maturin develop（重新构建 py-arf）
../.venv/bin/python -m maturin develop -m py-arf/Cargo.toml

# 跑 E2E（必须从 py-arf/ 目录运行，确保 asyncio_mode=auto 配置生效）
cd py-arf && ../.venv/bin/python -m pytest tests/e2e/ -v
```

## Env vars

| Var | 用途 |
|-----|------|
| `MINIMAX_API_KEY` | MiniMaxProvider live tests（主用） |
| `MINIMAX_TOKEN` | 同上 fallback |

未配 key → 涉及 live API 的测试自动 skip。

## 测试列表

- `test_engine_roundtrip.py` — 5 测试
- `test_mcp_facade.py` — 2 测试
- `test_recovery.py` — 2 测试
- `test_pool.py` — 2 测试
- `test_bus_lifecycle.py` — 5 测试 (default construct, connect, broadcast, filter, shutdown)
- `test_model_adapter.py` — 4 测试
- 合计 20 测试

注：brief 文档列出的 `test_bus_lifecycle.py` 计划 4 测试，本目录交付 5 测试，
把 brief 中的 4 个拆成 5 个更细粒度的场景（构造/方法/边界各 1+ 个）。

## 设计文档

`docs/dev/phase6/task-6.20-6.22-e2e-testing.md`