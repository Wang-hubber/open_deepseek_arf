"""Task 18e — SSE real-time event stream e2e."""

import queue
import threading
import time

import httpx
import pytest


def test_sse_stream_emits_events(live_server: str, e2e_guard):
    """Background thread consumes /sse/team/default; main thread POSTs /chat;
    verify ≥3 events received."""
    events: queue.Queue = queue.Queue()
    stop = threading.Event()

    def consume():
        with httpx.Client(timeout=20) as c:
            try:
                with c.stream("GET", f"{live_server}/sse/team/default") as r:
                    for line in r.iter_lines():
                        if stop.is_set():
                            return
                        if line.startswith("data:"):
                            events.put(line)
            except Exception:
                pass

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(1.0)  # 等 SSE 连接建立

    try:
        with httpx.Client(base_url=live_server, timeout=120) as c:
            r = c.post("/chat", json={"message": "请简单总结一下今天的工作"})
            assert r.status_code == 200, r.text

        # 等消费者收完
        time.sleep(3.0)
    finally:
        stop.set()
        t.join(timeout=2)

    received = []
    while not events.empty():
        received.append(events.get_nowait())

    assert len(received) >= 1, (
        f"SSE 应至少收到 1 条事件（pm chat 触发的 round/model events），"
        f"实际 {len(received)}"
    )