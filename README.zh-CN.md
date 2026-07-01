# ARF — AI Resources & Runtime Framework

> **Engine drives ReAct loop. Model makes decisions. Agent holds state.**

ARF 提供 Agent 运行所需的全部基础设施——资源发现与热加载、内存管理、模型调度、安全沙箱、可观测性、A2A 通讯。框架通过 Protocol 定义接口隔离，依赖注入组装全部能力。

## 仓库结构

```
crates/       # Rust workspace（核心框架）
py-arf/       # Python 绑定（PyO3 + maturin）
examples/
  rust/       # Cargo workspace 成员示例
  python/     # py-arf 用法演示
tests/        # 跨语言集成测试入口
docs/
  api/        # 用户 API 参考（PyTorch/LangGraph 风格）
  dev/        # 开发者文档（Phase 设计、workflow）
  architecture/  # 高层架构说明
```

完整架构概念见 [docs/architecture/overview.md](docs/architecture/overview.md)。

## 快速开始

### Rust

```bash
cargo test --workspace
```

### Python

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

## 示例

- Rust：`cargo run --bin domain_controller`、`cargo run --bin recovery`
- Python：`python examples/python/ex01_minimal_mock.py` 等

## 文档

- [docs/api/](docs/api/) — 用户 API 参考
- [docs/dev/](docs/dev/) — 开发者文档
- [docs/architecture/](docs/architecture/) — 架构概念

## 变更日志

参见 [CHANGELOG.md](CHANGELOG.md)。

## License

MIT