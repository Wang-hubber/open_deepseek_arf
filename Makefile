.PHONY: install test test-rust test-py lint clean

install:
	pip install -e ".[dev]"

test: test-rust test-py

test-rust:
	. "$(HOME)/.cargo/env" && cargo test --workspace

test-py:
	pytest tests/ -q

lint:
	. "$(HOME)/.cargo/env" && cargo fmt --check
	. "$(HOME)/.cargo/env" && cargo clippy --workspace --all-targets

clean:
	cargo clean
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -exec rm -rf {} +