"""[E2E] py-arf pool: ModelAdapterPool + McpPool real assembly.

[方法] [边界]

Mirrors crates/arf-e2e/tests/pool.rs. py-arf does NOT currently expose:
  - `ModelAdapterPool` (Rust crate `arf-pool`)
  - `McpPool` (Rust crate `arf-pool`)

Both Python tests are therefore stubbed with `pytest.skip` and a clear
comment pointing to the follow-up work (Phase 6 task 6.22.4 — extend
py-arf with Pool bindings). The Rust equivalents are
crates/arf-e2e/tests/pool.rs::model_adapter_pool_round_robins_three_providers
and pool.rs::mcp_pool_serializes_concurrent_tool_calls.
"""
import pytest


@pytest.mark.asyncio
async def test_python_model_adapter_pool_load_balances(live_bus, minimax_key):
    """[方法] ModelAdapterPool load-balances across N providers.

    NOT IMPLEMENTED — ModelAdapterPool bindings don't exist in py-arf yet.
    Follow-up: Phase 6 task 6.22.4.
    Rust equivalent: crates/arf-e2e/tests/pool.rs::model_adapter_pool_round_robins_three_providers
    """
    pytest.skip(
        "ModelAdapterPool Python bindings not exposed in py-arf yet — "
        "see Phase 6 task 6.22.4. Rust equivalent: "
        "crates/arf-e2e/tests/pool.rs::model_adapter_pool_round_robins_three_providers"
    )


@pytest.mark.asyncio
async def test_python_mcp_pool_serializes_tool_calls():
    """[边界] McpPool with capacity=1 serializes 5 concurrent tool_exec.

    NOT IMPLEMENTED — McpPool bindings don't exist in py-arf yet.
    Follow-up: Phase 6 task 6.22.4.
    Rust equivalent: crates/arf-e2e/tests/pool.rs::mcp_pool_serializes_concurrent_tool_calls
    """
    pytest.skip(
        "McpPool Python bindings not exposed in py-arf yet — "
        "see Phase 6 task 6.22.4. Rust equivalent: "
        "crates/arf-e2e/tests/pool.rs::mcp_pool_serializes_concurrent_tool_calls"
    )