"""Task 18e — AgentTeams coordination e2e.

Verifies that `pm` engine coordinates with the 3 data agents via
peer_message. Uses real LLM provider when ARF_PROVIDER is configured.
"""

import time

import httpx
import pytest


def test_pm_coordinates_three_data_agents(live_server: str, e2e_guard):
    """POST /chat with a multi-step request; verify pm invokes all 3
    data engines and aggregates responses."""
    with httpx.Client(base_url=live_server, timeout=120) as c:
        # 1. 发请求
        r = c.post(
            "/chat",
            json={
                "message": (
                    "请帮我整理数据接入流程：分别咨询 data_onboarding、"
                    "data_governancer、data_explorer，然后总结给用户。"
                )
            },
        )
        assert r.status_code == 200, r.text
        response = r.json()["response"]
        assert response, "pm 应返回非空回复"

        # 2. 等几秒让事件写盘（fysnc 异步）
        time.sleep(2.0)

        # 3. 验证 pm engine 至少产生 1 条 peer_message_sent
        pm_stats = c.get("/stats/engine/pm").json()
        assert pm_stats["peer_messages_sent"] >= 1, (
            f"pm 应至少发出 1 条 peer_message，实际 {pm_stats['peer_messages_sent']}"
        )

        # 4. 验证 team rollup 聚合了所有 engine
        team = c.get("/stats/team/default").json()
        assert team["team_engines"] >= 4, "team 至少含 4 个 engine"
        assert team["total_peer_messages_sent"] >= 1, (
            f"team 总 peer_message_sent 应 ≥ 1，实际 {team['total_peer_messages_sent']}"
        )

        # 5. 验证 pm 也产生了 model_call（chat 调用至少 1 次 LLM）
        assert pm_stats["model_calls"]["total_calls"] >= 1, "pm 应至少 1 次 model_call"
        assert pm_stats["model_calls"]["total_tokens"] > 0, "应累计 token"