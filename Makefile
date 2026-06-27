VENV ?= .venv

.PHONY: lint test ci

lint:
	. "$$HOME/.cargo/env" && cargo fmt --check
	. "$$HOME/.cargo/env" && cargo clippy -- -D warnings

test:
	. "$$HOME/.cargo/env" && cargo test
	cd py-arf && ../$(VENV)/bin/python -m pytest

ci: lint test
