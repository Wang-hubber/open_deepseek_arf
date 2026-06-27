# Phase 0 — 项目脚手架

## 目标

为 V1.x 框架的 Rust 核心和 Python 绑定搭建可编译、可测试、可 CI 的开发骨架。本阶段不写业务逻辑，只确保后续 7 个 Phase 有地方写代码，且写完能一键验证。

## 关键决策

### 为什么是 Rust workspace？

V1.x 的六要素中，Bus、State、Engine、Agent 是性能敏感的核心路径。这四个组件用 Rust 实现，通过 Cargo workspace 组织：

- **共享编译缓存**：`Cargo.toml`（根）声明 workspace，`cargo test` 一条命令编译+测试全部 crate
- **依赖清晰**：每个 crate 在 `Cargo.toml` 里显式声明依赖谁，编译器强制检查
- **单测零成本**：每个 crate 自带 `#[cfg(test)]`，不需要额外的测试框架

### 为什么是 PyO3 + maturin？

Rust 核心编译成 `.so`/`.dylib`，通过 PyO3 暴露给 Python。maturin 处理交叉编译、wheel 打包、editable install 等繁琐步骤。最终用户只需要 `pip install arf`。

ModelAdapter 和 MCP 这两个组件用纯 Python 实现——它们做的是 API 格式翻译和外部工具调用，不涉及性能瓶颈，放在 Python 侧更灵活。

### crate 的依赖关系

```
arf-core  ──── 零依赖，共享类型
    │
    ├── arf-bus    (Phase 1)
    ├── arf-state  (Phase 2)
    │
    ├── arf-engine (Phase 3) — 依赖 core + bus + state
    └── arf-agent  (Phase 4) — 依赖 core + engine

py-arf  — PyO3 桥，依赖所有 Rust crate
```

## 创建的文件及作用

### Rust crate

| 文件 | 作用 |
|------|------|
| `Cargo.toml`（根） | workspace 清单，6 个 member |
| `crates/arf-core/Cargo.toml` + `src/lib.rs` | 共享类型：`Message`, `NodeId`, `SessionId` |
| `crates/arf-bus/Cargo.toml` + `src/lib.rs` | Phase 1 主战场：消息总线 |
| `crates/arf-state/Cargo.toml` + `src/lib.rs` | Phase 2 主战场：状态管理 |
| `crates/arf-engine/Cargo.toml` + `src/lib.rs` | Phase 3 主战场：运行引擎 |
| `crates/arf-agent/Cargo.toml` + `src/lib.rs` | Phase 4 主战场：Agent 骨架 |

每个 `lib.rs` 当前只有一个 `add(2, 2) → 4` 的占位测试，证明该 crate 可编译、可测试。Phase 开始后替换为真正的实现。

### Python 绑定

| 文件 | 作用 |
|------|------|
| `py-arf/Cargo.toml` | cdylib crate，把 Rust 编译成 `_arf.so` |
| `py-arf/pyproject.toml` | maturin 构建配置，声明 `module-name = "arf._arf"` |
| `py-arf/src/lib.rs` | PyO3 入口，当前暴露 `__version__ = "1.0.0-alpha.0"` |
| `py-arf/python/arf/__init__.py` | Python 包包装，`from arf._arf import __version__` |
| `py-arf/python/arf/examples/phase0_hello.py` | 教学示例：验证 `import arf` 可用 |
| `py-arf/tests/test_import.py` | pytest：验证版本号和示例可运行 |

### 工程支撑

| 文件 | 作用 |
|------|------|
| `Makefile` | `make lint`（fmt + clippy）、`make test`（cargo + pytest）、`make ci`（lint + test） |
| `docs/v1.x/phase1/phase0-scaffold.md` | 本文档 |

## 环境配置

| 组件 | 来源 | 镜像 |
|------|------|------|
| Rust 1.96.0 | `rustup` | `RUSTUP_DIST_SERVER=https://mirrors.aliyun.com/rustup` |
| crates.io | `cargo` | `~/.cargo/config.toml` → `sparse+https://mirrors.aliyun.com/crates.io-index/` |
| Python 3.14 | 系统 | — |
| maturin 1.14 | PyPI | `~/.pip/pip.conf` → `http://mirrors.aliyun.com/pypi/simple/` |
| PyO3 0.29 | crates.io | 同上 |
| venv | `.venv2/` | 因为旧 `.venv` 是 root 权限 |

## 常用命令

```bash
# Rust 编译 + 测试
. "$HOME/.cargo/env" && cargo test --workspace

# Python 开发模式安装（修改 Rust 后重新运行）
VIRTUAL_ENV=.venv2 .venv2/bin/python -m maturin develop

# 验证 Python 导入
.venv2/bin/python -c "from arf import __version__; print(__version__)"

# 一键 CI
make ci
```

## 验证结果

```
cargo test --workspace
  5/5 crate tests passed

pytest py-arf/tests/
  2/2 python tests passed
```

## 下一阶段

Phase 1 — Bus 消息总线。在 `crates/arf-core/src/lib.rs` 中定义 `Message`、`NodeId`、`NodeInfo` 共享类型，在 `crates/arf-bus/src/lib.rs` 中实现 J-RPC 广播总线。
