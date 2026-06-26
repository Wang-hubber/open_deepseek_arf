# Phase 0 — 项目脚手架

## 产出

- Cargo workspace: `arf-core`, `arf-bus`, `arf-state`, `arf-engine`, `arf-agent`, `py-arf`
- maturin mixed Rust/Python project: `py-arf/`
- Makefile: `lint` / `test` / `ci`
- Python 环境: `.venv2`（Python 3.14 + maturin + PyO3 0.29）
- 镜像: Aliyun（crates.io + PyPI），Tsinghua（rustup）

## 目录结构

```
crates/
├── arf-core/     # 共享类型：Message, NodeId, SessionId
├── arf-bus/      # J-RPC 广播总线
├── arf-state/    # messages + tasks 生命周期
├── arf-engine/   # ReAct 主循环
└── arf-agent/    # 声明式配置 + 被动状态机

py-arf/
├── Cargo.toml       # Rust 侧：pyo3 0.29, cdylib → _arf
├── pyproject.toml   # maturin mixed project
├── src/lib.rs       # PyO3 bindings entry
└── python/arf/      # Python wrapper package
    └── __init__.py
```

## 常用命令

```bash
# Rust workspace test
. "$HOME/.cargo/env" && cargo test

# Python build (editable)
VIRTUAL_ENV=.venv2 .venv2/bin/python -m maturin develop

# Makefile
make lint     # cargo fmt --check + clippy
make test     # cargo test + pytest
make ci       # lint + test
```

## 镜像配置

- **crates.io**: `~/.cargo/config.toml` → `sparse+https://mirrors.aliyun.com/crates.io-index/`
- **PyPI**: `~/.pip/pip.conf` → `http://mirrors.aliyun.com/pypi/simple/`
- **rustup**: 通过 `RUSTUP_DIST_SERVER=https://mirrors.aliyun.com/rustup` 环境变量
