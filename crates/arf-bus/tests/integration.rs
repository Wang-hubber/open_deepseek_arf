//! Integration tests — multi-node scenarios on real tokio runtime.

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, SendError, ToMatch};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::broadcast;

// ── Helpers ─────────────────────────────────────────────────────────

fn node(id: &str) -> NodeInfo {
    NodeInfo {
        node_id: NodeId::new(id),
        node_type: "test".into(),
        capabilities: serde_json::json!({}),
        online_since: 0,
    }
}

fn all_filter() -> MessageFilter {
    MessageFilter {
        types: None,
        to_match: ToMatch::All,
    }
}

fn directed_filter() -> MessageFilter {
    MessageFilter {
        types: None,
        to_match: ToMatch::DirectedToMe,
    }
}

fn broadcast_filter() -> MessageFilter {
    MessageFilter {
        types: None,
        to_match: ToMatch::BroadcastOnly,
    }
}

fn action_directed_filter() -> MessageFilter {
    MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::DirectedToMe,
    }
}

fn action_broadcast_filter() -> MessageFilter {
    MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::BroadcastOnly,
    }
}

fn fast_bus() -> Bus {
    Bus::new(Duration::from_millis(500), Duration::from_secs(10), 64)
}

/// Helper: drain all immediately available messages from a NodeHandle.
/// Call this before making assertions about specific messages.
fn drain_all(handle: &mut arf_bus::NodeHandle) -> Vec<(String, String)> {
    let mut msgs = Vec::new();
    loop {
        match handle.try_recv() {
            Ok(Some(m)) => msgs.push((m.msg_type, m.from.0)),
            Ok(None) => return msgs,
            Err(broadcast::error::TryRecvError::Lagged(_)) => {} // continue draining
            Err(_) => return msgs,                               // Closed
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// 1. 上线/下线广播 — 3 节点互相感知
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn online_offline_broadcast_three_nodes() {
    let bus = fast_bus();
    let mut a = bus.connect(node("A"), all_filter()).await.unwrap();
    let mut b = bus.connect(node("B"), all_filter()).await.unwrap();
    let c = bus.connect(node("C"), all_filter()).await.unwrap();

    // A connected first → sees B and C node_online (2 msgs)
    // B connected second → sees C node_online only (1 msg)
    // C connected last → sees nothing (rx created after all node_online)
    let a_msgs = drain_all(&mut a);
    let online_a: Vec<_> = a_msgs.iter().filter(|(t, _)| t == "node_online").collect();
    assert_eq!(
        online_a.len(),
        2,
        "A should see B and C online, got {a_msgs:?}"
    );

    // B drains C's node_online
    drain_all(&mut b);

    // Disconnect C → A and B see node_offline
    c.disconnect().await;

    // A should now see node_offline
    assert!(
        drain_all(&mut a)
            .iter()
            .any(|(t, f)| t == "node_offline" && f == "C"),
        "A should see C node_offline"
    );

    // B should now see node_offline
    assert!(
        drain_all(&mut b)
            .iter()
            .any(|(t, f)| t == "node_offline" && f == "C"),
        "B should see C node_offline"
    );

    a.disconnect().await;
    b.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 2. 定向消息过滤
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn directed_and_broadcast_filters_work_end_to_end() {
    let bus = fast_bus();
    let sender = bus.connect(node("sender"), all_filter()).await.unwrap();

    // worker: DirectedToMe → node_online (broadcast) is filtered out
    let mut worker = bus
        .connect(node("worker"), directed_filter())
        .await
        .unwrap();
    // watcher: BroadcastOnly → node_online (broadcast) passes
    let mut watcher = bus
        .connect(node("watcher"), broadcast_filter())
        .await
        .unwrap();

    // Drain watcher's node_online ×2 (sender + worker)
    drain_all(&mut watcher);
    // worker has nothing to drain (node_online filtered by DirectedToMe)

    // Send directed to worker
    sender
        .send("task", vec![NodeId::new("worker")], serde_json::json!("do"))
        .await
        .unwrap();
    // Send broadcast
    sender
        .send("task", vec![], serde_json::json!("all"))
        .await
        .unwrap();

    // Worker (DirectedToMe) sees the directed, not the broadcast
    let w_msgs = drain_all(&mut worker);
    assert!(
        w_msgs.iter().any(|(t, _)| t == "task"),
        "worker should see directed task"
    );
    assert_eq!(w_msgs.len(), 1, "worker should see exactly 1 message");

    // Watcher (BroadcastOnly) sees the broadcast, not the directed
    let w2_msgs = drain_all(&mut watcher);
    assert!(
        w2_msgs.iter().any(|(t, _)| t == "task"),
        "watcher should see broadcast task"
    );
    assert_eq!(w2_msgs.len(), 1, "watcher should see exactly 1 message");

    worker.disconnect().await;
    watcher.disconnect().await;
    sender.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 3. 心跳超时
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn heartbeat_timeout_zombie_node_offline() {
    let bus = Bus::new(Duration::from_millis(30), Duration::from_millis(60), 16);
    let mut observer = bus.connect(node("observer"), all_filter()).await.unwrap();
    // Zombie is dropped without disconnect — its forwarding task exits,
    // and Bus eventually times out its entry (Phase 6 task 6.0.2 semantic).
    {
        let _zombie = bus.connect(node("zombie"), all_filter()).await.unwrap();
        // _zombie dropped at end of block — forwarding task exits,
        // no more HeartbeatAck sent, Bus will mark zombie offline.
    }

    // Drain zombie's node_online from observer
    drain_all(&mut observer);

    tokio::time::sleep(Duration::from_millis(200)).await;

    // Observer should see node_offline for zombie
    let msgs = drain_all(&mut observer);
    assert!(
        msgs.iter()
            .any(|(t, f)| t == "node_offline" && f == "zombie"),
        "observer should see zombie offline, got {msgs:?}"
    );

    // Graph should not list zombie
    let g = bus.graph();
    let ids: Vec<&str> = g.nodes.iter().map(|n| n.node_id.as_str()).collect();
    assert!(!ids.contains(&"zombie"));

    observer.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 4. Trace 全量消费
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn trace_node_sees_everything() {
    let bus = fast_bus();
    let mut trace = bus.connect(node("trace"), all_filter()).await.unwrap();
    let a = bus.connect(node("A"), all_filter()).await.unwrap();
    let b = bus.connect(node("B"), all_filter()).await.unwrap();

    // Drain node_online messages from trace (2: A and B)
    drain_all(&mut trace);

    a.send("chat", vec![], serde_json::json!("hello"))
        .await
        .unwrap();
    b.send("reply", vec![NodeId::new("A")], serde_json::json!("hi"))
        .await
        .unwrap();

    let msgs = drain_all(&mut trace);
    assert!(
        msgs.iter().any(|(t, _)| t == "chat"),
        "trace should see chat, got {msgs:?}"
    );
    assert!(
        msgs.iter().any(|(t, _)| t == "reply"),
        "trace should see reply, got {msgs:?}"
    );

    a.disconnect().await;
    b.disconnect().await;
    trace.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 5. Lag 恢复
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn slow_consumer_lagged_then_recovers() {
    let bus = Bus::new(Duration::from_secs(10), Duration::from_secs(30), 2);
    let mut slow = bus.connect(node("slow"), all_filter()).await.unwrap();

    // Send capacity+1 to overflow: msg0+msg1 fill buffer, msg2 overwrites msg0
    // (forwarding task internally sees Lagged on its broadcast_rx and skips it
    // — Phase 6 task 6.0.2 silently swallows Lagged).
    for i in 0..3 {
        let msg = arf_core::Message::new("msg", NodeId::new("sys"), vec![], serde_json::json!(i));
        bus.send(msg).await.unwrap();
    }

    // Phase 6 task 6.0.2 silently swallows Lagged inside the forwarding
    // task, so the user does not directly observe Lagged. Drain whatever
    // messages the forwarding task has produced so far; the count depends
    // on scheduling (forwarding task may drain the broadcast between sends).
    let mut drained = 0;
    while let Ok(Some(_)) = slow.try_recv() {
        drained += 1;
        if drained > 5 {
            break;
        }
    }
    assert!(
        drained >= 1,
        "expected at least 1 message after sends, got {drained}"
    );

    // After catching up, new message arrives normally
    let msg = arf_core::Message::new(
        "msg",
        NodeId::new("sys"),
        vec![],
        serde_json::json!("fresh"),
    );
    bus.send(msg).await.unwrap();
    assert_eq!(
        slow.recv().await.unwrap().payload,
        serde_json::json!("fresh")
    );

    slow.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 6. 并发 connect/disconnect/send
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn concurrent_connect_disconnect_send_no_panic() {
    let bus = Arc::new(Bus::new(
        Duration::from_secs(10),
        Duration::from_secs(30),
        256,
    ));

    let mut handles = Vec::new();
    for i in 0..10 {
        let bus = bus.clone();
        handles.push(tokio::spawn(async move {
            let id = format!("node-{i}");
            let h = bus.connect(node(&id), all_filter()).await.unwrap();
            h.send("ping", vec![], serde_json::json!(i)).await.unwrap();
            h.disconnect().await;
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    let g = bus.graph();
    assert_eq!(g.nodes.len(), 0, "all nodes should have disconnected");
    let bus = Arc::into_inner(bus).unwrap();
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 7. Late joiner + graph() 发现已有节点
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn late_joiner_discovers_peers_via_graph() {
    let bus = fast_bus();
    let early = bus.connect(node("early"), all_filter()).await.unwrap();
    let mut late = bus.connect(node("late"), all_filter()).await.unwrap();

    // late's rx was created after early's node_online → recv would block
    assert!(late.try_recv().unwrap().is_none());

    // But graph() reveals early
    let g = bus.graph();
    assert_eq!(g.nodes.len(), 2);
    let ids: Vec<&str> = g.nodes.iter().map(|n| n.node_id.as_str()).collect();
    assert!(ids.contains(&"early"));
    assert!(ids.contains(&"late"));

    early.disconnect().await;
    late.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 8. 多 filter 不同子集
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn multi_filter_different_subsets() {
    let bus = fast_bus();
    let sender = bus.connect(node("sender"), all_filter()).await.unwrap();

    // trace: sees everything (All, types=None)
    let mut trace = bus.connect(node("trace"), all_filter()).await.unwrap();
    // worker: only "action" directed to self
    let mut worker = bus
        .connect(node("worker"), action_directed_filter())
        .await
        .unwrap();
    // watcher: only "action" broadcast
    let mut watcher = bus
        .connect(node("watcher"), action_broadcast_filter())
        .await
        .unwrap();

    // Drain: trace sees all node_online ×2 (worker+watcher), watcher sees them too (broadcast passes),
    // worker sees none (DirectedToMe filters broadcast node_online)
    drain_all(&mut trace);
    drain_all(&mut watcher);

    // Send 4 messages:
    sender
        .send("action", vec![], serde_json::json!(1))
        .await
        .unwrap(); // #1 broadcast
    sender
        .send("action", vec![NodeId::new("worker")], serde_json::json!(2))
        .await
        .unwrap(); // #2 directed worker
    sender
        .send("noise", vec![], serde_json::json!(3))
        .await
        .unwrap(); // #3 noise broadcast
    sender
        .send("action", vec![NodeId::new("watcher")], serde_json::json!(4))
        .await
        .unwrap(); // #4 directed watcher

    // trace: sees all 4 (All filter)
    let trace_msgs = drain_all(&mut trace);
    let app_msgs: Vec<_> = trace_msgs
        .iter()
        .filter(|(t, _)| t != "node_offline")
        .collect();
    assert_eq!(
        app_msgs.len(),
        4,
        "trace should see all 4 app messages, got {trace_msgs:?}"
    );

    // worker: sees only #2 (action directed to worker)
    let worker_msgs = drain_all(&mut worker);
    assert_eq!(
        worker_msgs.len(),
        1,
        "worker should see 1 msg, got {worker_msgs:?}"
    );
    assert_eq!(worker_msgs[0].0, "action");

    // watcher: sees only #1 (action broadcast)
    let watcher_msgs = drain_all(&mut watcher);
    assert_eq!(
        watcher_msgs.len(),
        1,
        "watcher should see 1 msg, got {watcher_msgs:?}"
    );
    assert_eq!(watcher_msgs[0].0, "action");

    sender.disconnect().await;
    trace.disconnect().await;
    worker.disconnect().await;
    watcher.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 9. 心跳超时 + 同 NodeId 重连
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn heartbeat_timeout_then_reconnect_same_id() {
    let bus = Bus::new(Duration::from_millis(30), Duration::from_millis(50), 16);
    let mut observer = bus.connect(node("observer"), all_filter()).await.unwrap();
    // Same scenario as above: drop handle (Phase 6 semantic).
    {
        let _zombie1 = bus.connect(node("zombie"), all_filter()).await.unwrap();
        // dropped
    }

    drain_all(&mut observer); // drain zombie node_online

    // Wait for timeout
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Observer should see node_offline for zombie
    let msgs = drain_all(&mut observer);
    assert!(
        msgs.iter()
            .any(|(t, f)| t == "node_offline" && f == "zombie"),
        "should see zombie offline, got {msgs:?}"
    );

    // Reconnect with same NodeId
    let zombie2 = bus.connect(node("zombie"), all_filter()).await.unwrap();
    drain_all(&mut observer); // drain zombie2 node_online

    zombie2
        .send("alive", vec![], serde_json::json!("back"))
        .await
        .unwrap();
    let msgs2 = drain_all(&mut observer);
    assert!(
        msgs2.iter().any(|(t, f)| t == "alive" && f == "zombie"),
        "zombie2 should be able to send, got {msgs2:?}"
    );

    zombie2.disconnect().await;
    observer.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 10. disconnect 时消息在广播缓冲
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn message_delivered_before_node_offline_after_disconnect() {
    let bus = fast_bus();
    let mut receiver = bus.connect(node("receiver"), all_filter()).await.unwrap();
    let sender = bus.connect(node("sender"), all_filter()).await.unwrap();

    drain_all(&mut receiver); // drain sender node_online

    // Sender sends a message then disconnects
    sender
        .send("last_words", vec![], serde_json::json!("goodbye"))
        .await
        .unwrap();
    sender.disconnect().await;

    // Receiver should see: the message FIRST, then node_offline
    let msgs = drain_all(&mut receiver);
    let last_words_pos = msgs.iter().position(|(t, _)| t == "last_words");
    let offline_pos = msgs
        .iter()
        .position(|(t, f)| t == "node_offline" && f == "sender");
    assert!(
        last_words_pos.is_some(),
        "receiver should see last_words, got {msgs:?}"
    );
    assert!(
        offline_pos.is_some(),
        "receiver should see node_offline, got {msgs:?}"
    );
    assert!(
        last_words_pos < offline_pos,
        "message must arrive before node_offline"
    );

    receiver.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 11. 快速 connect/disconnect 循环
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn rapid_connect_disconnect_cycle_no_leak() {
    let bus = fast_bus();

    for i in 0..10 {
        let h = bus
            .connect(node(&format!("n-{i}")), all_filter())
            .await
            .unwrap();
        h.disconnect().await;
    }

    assert_eq!(bus.graph().nodes.len(), 0);

    // Can still use normally
    let handle = bus.connect(node("after"), all_filter()).await.unwrap();
    assert_eq!(bus.graph().nodes.len(), 1);
    handle.disconnect().await;

    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════════
// 12. shutdown 时有在线节点
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn shutdown_with_online_nodes_closes_receivers() {
    let bus = fast_bus();
    let mut handle = bus.connect(node("survivor"), all_filter()).await.unwrap();

    bus.shutdown().await;

    assert!(matches!(
        handle.recv().await,
        Err(broadcast::error::RecvError::Closed)
    ));
    assert!(matches!(
        handle
            .send("after_shutdown", vec![], serde_json::json!(null))
            .await,
        Err(SendError::BusClosed)
    ));
}

// ═══════════════════════════════════════════════════════════════════════════════
// §9.A finalization (Phase 6 task 6.0.5) — 2 e2e tests
// ═══════════════════════════════════════════════════════════════════════════════

// [§9.A 收尾] facade Node 模式：NodeHandle 多 Bus 订阅 + barrier 协调
// 拓扑：1 个 facade node 同时 subscribed bus_a 和 bus_b，每个 Bus
// 上的所有 Node 共同响应 barrier。
#[tokio::test]
async fn barrier_facade_with_attach_to_two_buses() {
    use std::sync::Arc;
    let bus_a = Arc::new(Bus::new(
        std::time::Duration::from_millis(500),
        std::time::Duration::from_secs(30),
        32,
    ));
    let bus_b = Arc::new(Bus::new(
        std::time::Duration::from_millis(500),
        std::time::Duration::from_secs(30),
        32,
    ));

    // facade handle：在两条 Bus 上都有订阅
    let mut facade = bus_a
        .connect(node("facade"), all_filter())
        .await
        .unwrap();
    facade.attach_to(bus_b.clone(), all_filter()).await.unwrap();

    let _other_a = bus_a.connect(node("other_a"), all_filter()).await.unwrap();
    let _other_b = bus_b.connect(node("other_b"), all_filter()).await.unwrap();

    // ack listeners — spawned FIRST so they subscribe before we call barrier.
    // (Ack task subscribes then loops: only acts on barrier_request.)
    let bus_a_for_task = bus_a.clone();
    let bus_b_for_task = bus_b.clone();
    let facade_id = NodeId::new("facade");
    let ack_task_a = tokio::spawn(async move {
        let mut rx_a = bus_a_for_task.subscribe();
        // Subscribe is done above (broadcast_rx registered); now loop on messages
        // until we see barrier_request matching our facade_id correlation_id
        // and send ack. We do this each time barrier() is called.
        while let Ok(msg) = rx_a.recv().await {
            if msg.msg_type != "barrier_request" { continue; }
            if let Some(cid) = msg.payload.get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| uuid::Uuid::parse_str(s).ok())
            {
                let ack = arf_core::Message::with_from_bus(
                    "barrier_ack",
                    facade_id.clone(),
                    vec![],
                    serde_json::json!({ "correlation_id": cid }),
                    bus_a_for_task.id,
                );
                let _ = bus_a_for_task.send(ack).await;
            }
        }
    });
    let ack_task_b = tokio::spawn({
        let bus_b_for_task = bus_b.clone();
        let facade_id = NodeId::new("facade");
        async move {
            let mut rx_b = bus_b_for_task.subscribe();
            while let Ok(msg) = rx_b.recv().await {
                if msg.msg_type != "barrier_request" { continue; }
                if let Some(cid) = msg.payload.get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| uuid::Uuid::parse_str(s).ok())
                {
                    let ack = arf_core::Message::with_from_bus(
                        "barrier_ack",
                        facade_id.clone(),
                        vec![],
                        serde_json::json!({ "correlation_id": cid }),
                        bus_b_for_task.id,
                    );
                    let _ = bus_b_for_task.send(ack).await;
                }
            }
        }
    });

    // Give listeners time to subscribe (avoid race with barrier broadcast)
    tokio::time::sleep(std::time::Duration::from_millis(20)).await;

    // barrier：bus_a 上协调 [facade]。facade 同时在 bus_b 上存在，
    // 但 bus_a.barrier() 只在 bus_a 范围内等。barrier 内部走 bus_a 广播，
    // ack 监听 A 也订阅了 bus_a，所以能收到 request 并响应 ack。
    // (这条 e2e 主要验证：每 Bus 单独 barrier 都能正常 ack 协调。)
    let receipt = bus_a
        .barrier(vec![NodeId::new("facade")], std::time::Duration::from_millis(800))
        .await;

    assert_eq!(receipt.acked, vec![NodeId::new("facade")]);
    assert!(receipt.missing.is_empty());
    assert!(!receipt.timed_out);

    // 让 listener 收到 Exit（channel close）后自然退出
    ack_task_a.abort();
    ack_task_b.abort();
    drop(facade);
    // Best-effort cleanup: drop Arcs; spawned tasks may keep references briefly
    drop(bus_a);
    drop(bus_b);
}

// [§9.A 收尾] NodeHandle 完整 lifecycle：connect → attach_to → send_via → recv
#[tokio::test]
async fn node_handle_full_lifecycle_two_buses() {
    use std::sync::Arc;
    let bus_a = Arc::new(Bus::new(
        std::time::Duration::from_millis(500),
        std::time::Duration::from_secs(30),
        32,
    ));
    let bus_b = Arc::new(Bus::new(
        std::time::Duration::from_millis(500),
        std::time::Duration::from_secs(30),
        32,
    ));

    let mut handle = bus_a
        .connect(node("multi"), all_filter())
        .await
        .unwrap();
    handle.attach_to(bus_b.clone(), all_filter()).await.unwrap();

    // Bus B 上注册 tool_node（让定向消息 targets 在线）
    let _tool_node = bus_b
        .connect(node("tool_node"), all_filter())
        .await
        .unwrap();

    // 在 bus_a 发 model_call
    handle
        .send_via(bus_a.id, "model_call", vec![], serde_json::json!({"prompt": "hello"}))
        .await
        .unwrap();
    // 在 bus_b 发 tool_exec
    handle
        .send_via(
            bus_b.id,
            "tool_exec",
            vec![NodeId::new("tool_node")],
            serde_json::json!({"tool": "read"}),
        )
        .await
        .unwrap();

    // 用 subscriptions() 验证订阅列表
    let subs = handle.subscriptions();
    assert_eq!(subs.len(), 2);
    assert_eq!(subs[0], bus_a.id);
    assert_eq!(subs[1], bus_b.id);

    // recv() 收到两条消息（顺序不定，因 forwarding task 调度）
    let mut got_model = false;
    let mut got_tool = false;
    for _ in 0..4 {
        // 4 = 2 node_online (own + other's attach) + 2 messages
        let msg = handle.recv().await.unwrap();
        match msg.msg_type.as_str() {
            "model_call" => {
                assert_eq!(msg.payload["prompt"], "hello");
                got_model = true;
            }
            "tool_exec" => {
                assert_eq!(msg.payload["tool"], "read");
                got_tool = true;
            }
            _ => {} // node_online, etc.
        }
        if got_model && got_tool {
            break;
        }
    }
    assert!(got_model && got_tool);

    handle.disconnect().await;
    let _ = Arc::try_unwrap(bus_a).map(|b| async move { b.shutdown().await });
    let _ = Arc::try_unwrap(bus_b);
}
