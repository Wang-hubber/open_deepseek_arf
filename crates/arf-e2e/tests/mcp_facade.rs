//! [E2E] MCP facade — `tool_call_set` execution and Bus-level forwarding.
//!
//! Test angles covered:
//! - [方法] Local McpNode discovers filesystem tools, executes `tool_call_set`,
//!   emits `tool_result_set` back to the requester.
//! - [边界] tool_call to an unknown tool name returns a graceful error result
//!   (status: "error") instead of crashing.
//! - [方法] Engine.run() with a hand-rolled facade bridging two Buses — the
//!   classic Top↔Sub translation pattern from §2.P7.
//!
//! These tests use real `Bus` + real `McpNode`. The facade in Test 3 is a
//! minimal hand-rolled loop; its only job is to translate `tool_exec` ↔
//! `tool_call_set`.

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, Route, State, ToMatch};
use arf_engine::{AgentConfig, EngineBuilder, ModelConfig};
use arf_mcp::{McpNode, types::ToolCallSet, types::ToolCallItem};
use arf_model_adapter::ModelAdapterNode;
use common::provider::{scripted, text_response};
use serde_json::json;
use serde_json::Value;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

/// Write a Python-based echo tool to `tmpdir/tools/echo/`.
fn write_echo_tool(tmp: &std::path::Path) {
    let tool_dir = tmp.join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    std::fs::write(
        tool_dir.join("tool.toml"),
        "name = \"echo\"\ndescription = \"Echo back the input\"\nruntime = \"python\"\nentrypoint = \"echo.py\"\n",
    )
    .unwrap();
    std::fs::write(
        tool_dir.join("echo.py"),
        "import sys, json\nparams = json.load(sys.stdin)\nprint(json.dumps({\"echoed\": params.get(\"text\", \"\")}))\n",
    )
    .unwrap();
}

/// Send a `tool_call_set` to the McpNode and await a matching `tool_result_set`.
///
/// Important: the requester must be a registered Bus node (it has a `NodeId`
/// in the graph) because McpNode replies DIRECTED to the original sender.
/// Merely calling `bus.subscribe()` is not enough — non-registered senders
/// fail the directed-send target check.
async fn request_tool_call_set(
    bus: &Bus,
    mcp_id: &NodeId,
    cid: Uuid,
    call_set: ToolCallSet,
    timeout: Duration,
) -> anyhow::Result<Value> {
    let requester_id = NodeId::new(format!("test/requester/{}", cid));
    let info = NodeInfo {
        node_id: requester_id.clone(),
        node_type: "test-requester".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(vec!["tool_result_set".into()]),
        to_match: ToMatch::All,
    };
    let mut handle = bus.connect(info, filter).await?;
    let _ = handle
        .send(
            "tool_call_set",
            vec![mcp_id.clone()],
            json!({
                "correlation_id": cid.to_string(),
                "session_id": call_set.session_id,
                "calls": call_set.calls,
                "timeout_ms": call_set.timeout_ms,
            }),
        )
        .await;

    let deadline = std::time::Instant::now() + timeout;
    loop {
        let m = tokio::time::timeout_at(deadline.into(), handle.recv())
            .await
            .map_err(|_| anyhow::anyhow!("timeout"))??;
        if m.msg_type == "tool_result_set" {
            // McpNode's reply payload only contains the ToolResultSet
            // serialization (no correlation_id). Match by session_id instead.
            let session = m
                .payload
                .get("session_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if session == call_set.session_id {
                return Ok(m.payload);
            }
        }
    }
}

// ── Test 1: McpNode executes tool_call_set and returns tool_result_set ──

// [方法] McpNode 直接接收 tool_call_set，返回 tool_result_set — 单 Bus 链路。
// 这是 facade 概念的"底层桩"，后续测试在它之上加 facade 转发。
#[tokio::test]
async fn mcp_local_node_executes_tool_call_set() -> anyhow::Result<()> {
    let tmp = tempfile::tempdir().unwrap();
    write_echo_tool(tmp.path());

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        16,
    ));
    let mcp = McpNode::local("facade-test", tmp.path().to_path_buf())?;
    mcp.connect(&bus)
        .await
        .map_err(|e| anyhow::anyhow!("mcp connect: {e}"))?;

    // Give McpNode's spawned message_loop a moment to subscribe before sending.
    tokio::time::sleep(Duration::from_millis(50)).await;

    let cid = Uuid::new_v4();
    let call_set = ToolCallSet {
        session_id: "s1".into(),
        calls: vec![ToolCallItem {
            id: "call_0".into(),
            tool: "echo".into(),
            params: json!({"text": "hello"}),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(2000),
    };

    let payload = request_tool_call_set(
        &bus,
        &NodeId::new("mcp/facade-test"),
        cid,
        call_set,
        Duration::from_secs(3),
    )
    .await?;

    let results = payload
        .get("results")
        .and_then(|v| v.as_array())
        .expect("results array");
    assert_eq!(results.len(), 1);
    assert_eq!(results[0]["call_id"], "call_0");
    assert_eq!(results[0]["status"], "success");
    assert_eq!(results[0]["result"]["echoed"], "hello");
    Ok(())
}

// ── Test 2: tool_call to unknown tool returns a graceful error result ────

// [边界] 未知 tool name → executor 返回 status:"error" + error msg。
// 验证 McpNode 不 panic、链路不挂、调用方收到结构化错误。
#[tokio::test]
async fn mcp_local_node_unknown_tool_returns_error() -> anyhow::Result<()> {
    let tmp = tempfile::tempdir().unwrap();
    write_echo_tool(tmp.path());

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        16,
    ));
    let mcp = McpNode::local("facade-test", tmp.path().to_path_buf())?;
    mcp.connect(&bus)
        .await
        .map_err(|e| anyhow::anyhow!("mcp connect: {e}"))?;

    // Give McpNode's spawned message_loop a moment to subscribe before sending.
    tokio::time::sleep(Duration::from_millis(50)).await;

    let cid = Uuid::new_v4();
    let call_set = ToolCallSet {
        session_id: "s_err".into(),
        calls: vec![ToolCallItem {
            id: "call_0".into(),
            tool: "no_such_tool".into(),
            params: json!({}),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(1000),
    };

    let payload = request_tool_call_set(
        &bus,
        &NodeId::new("mcp/facade-test"),
        cid,
        call_set,
        Duration::from_secs(3),
    )
    .await?;

    let results = payload
        .get("results")
        .and_then(|v| v.as_array())
        .expect("results array");
    assert_eq!(results.len(), 1);
    assert_eq!(results[0]["call_id"], "call_0");
    assert_eq!(results[0]["status"], "error");
    assert!(results[0]["error"].as_str().is_some());
    Ok(())
}

// ── Test 3: facade forwards `tool_exec` across two Buses ────────────────

// [方法] 双 Bus 拓扑：
//   Top Bus:    Engine + ModelAdapterNode + Facade (tool_exec listener)
//   Sub Bus:    Facade-sub (tool_result_set listener) + McpNode
//
// Facade 任务：
//   1. 在 Top Bus 上收 tool_exec → 翻译为 tool_call_set 投到 Sub Bus
//   2. 在 Sub Bus 上收 tool_result_set → 翻译为 tool_result 回 Top Bus（保留 cid）
//
// 验证 Engine.run() 完整跑通一个 tool_call + 终结 round。
#[tokio::test]
async fn facade_forwards_tool_exec_across_buses() -> anyhow::Result<()> {
    let tmp = tempfile::tempdir().unwrap();
    write_echo_tool(tmp.path());

    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));

    let mcp = McpNode::local("e2e", tmp.path().to_path_buf())?;
    mcp.connect(&sub_bus)
        .await
        .map_err(|e| anyhow::anyhow!("mcp: {e}"))?;
    let mcp_id = NodeId::new("mcp/e2e");
    let facade_id = NodeId::new("facade/mcp_proxy");
    // The forwarder on sub bus must register as `facade/sub` so that McpNode
    // can send tool_result_set back to a node that exists on the sub bus.
    let facade_sub_id = NodeId::new("facade/sub");

    // The forwarder (Top→Sub):
    let top_facade_handle = top_bus
        .connect(
            NodeInfo {
                node_id: facade_id.clone(),
                node_type: "facade".into(),
                capabilities: json!({}),
                online_since: 0,
            },
            MessageFilter {
                types: Some(vec!["tool_exec".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await?;
    let sub_facade_handle = sub_bus
        .connect(
            NodeInfo {
                node_id: NodeId::new("facade/sub"),
                node_type: "facade-sub".into(),
                capabilities: json!({}),
                online_since: 0,
            },
            MessageFilter {
                types: Some(vec!["tool_result_set".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await?;

    // Channel from forwarder → translator: carries (orig_cid, sub_reply_payload, engine_from).
    let (fwd_tx, mut fwd_rx) = tokio::sync::mpsc::unbounded_channel::<(String, Value, NodeId)>();

    let sub_bus_for_fwd = sub_bus.clone();
    let facade_id_for_fwd = facade_id.clone();
    let _forwarder = tokio::spawn(async move {
        let mut top_h = top_facade_handle;
        loop {
            let m = match top_h.recv().await {
                Ok(m) => m,
                Err(_) => return,
            };
            if m.msg_type != "tool_exec" {
                continue;
            }
            let orig_cid = m
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .map(String::from)
                .unwrap_or_default();
            let tool_name = m
                .payload
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let args = m
                .payload
                .get("arguments")
                .cloned()
                .unwrap_or(json!({}));
            let fwd_cid = Uuid::new_v4();
            let call_set = ToolCallSet {
                session_id: "fwd".into(),
                calls: vec![ToolCallItem {
                    id: "call_0".into(),
                    tool: tool_name.clone(),
                    params: args,
                    blocked_by: vec![],
                    blocking: vec![],
                }],
                timeout_ms: Some(2000),
            };
            let _ = sub_bus_for_fwd
                .send(Message::with_from_bus(
                    String::from("tool_call_set"),
                    // From the registered sub-bus facade node, NOT the top-bus
                    // facade id (which doesn't exist on sub bus).
                    NodeId::new("facade/sub"),
                    vec![mcp_id.clone()],
                    json!({
                        "correlation_id": fwd_cid.to_string(),
                        "session_id": call_set.session_id,
                        "calls": call_set.calls,
                        "timeout_ms": call_set.timeout_ms,
                    }),
                    sub_bus_for_fwd.id,
                ))
                .await;
            // Hand off the (orig_cid, fwd_cid, sender) to the translator via a
            // separate channel. For simplicity, the translator looks up its
            // own matching tool_result_set by fwd_cid; the orig_cid is needed
            // for the reply. We use another channel so the translator knows
            // which fwd_cid to match.
            let _ = fwd_cid;
            // Send a sentinel so translator knows there's a request pending.
            let _ = fwd_tx.send((orig_cid, Value::Null, m.from));
        }
    });

    // Translator (Sub→Top): listens on sub_facade_handle and matches tool_result_set
    // by correlation_id to the latest outstanding forward.
    // Translation: extract results[0], translate status:success → content=result,
    // status:error → content="error: ...". Stamp original correlation_id back.
    let _translator = {
        let mut sub_h = sub_facade_handle;
        let top_bus_for_trans = top_bus.clone();
        let facade_id_for_trans = facade_id.clone();
        tokio::spawn(async move {
            while let Some((orig_cid, _payload, engine_from)) = fwd_rx.recv().await {
                let reply = loop {
                    let r = match sub_h.recv().await {
                        Ok(m) => m,
                        Err(_) => return,
                    };
                    if r.msg_type != "tool_result_set" {
                        continue;
                    }
                    break r.payload;
                };
                let results = reply
                    .get("results")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();
                let (content, ok) = if let Some(r0) = results.first() {
                    if r0["status"] == "success" {
                        (r0["result"].to_string(), true)
                    } else {
                        (
                            format!("error: {}", r0["error"].as_str().unwrap_or("")),
                            false,
                        )
                    }
                } else {
                    (String::new(), false)
                };
                let _ = top_bus_for_trans
                    .send(Message::with_from_bus(
                        String::from("tool_result"),
                        facade_id_for_trans.clone(),
                        vec![engine_from],
                        json!({
                            "correlation_id": orig_cid,
                            "name": "echo",
                            "content": content,
                            "ok": ok,
                        }),
                        top_bus_for_trans.id,
                    ))
                    .await;
            }
        })
    };

    // Build engine + model node on top bus.
    let provider = scripted(vec![
        common::provider::tool_call_response("echo", json!({"text": "facade-test"})),
        text_response("facade loop done"),
    ]);
    let _model_node = ModelAdapterNode::new(
        provider,
        &top_bus,
        NodeId::new("model/e2e"),
    )
    .await?;
    let cfg = AgentConfig {
        agent_id: "facade-agent".into(),
        model_config: ModelConfig {
            provider: "scripted".into(),
            model: "scripted-v1".into(),
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        max_turns: 5,
        tool_timeout_ms: Some(5000),
        permissions: Default::default(),
        routes: {
            let mut r = std::collections::HashMap::new();
            r.insert(
                "model_call".into(),
                Route::strict(vec![NodeId::new("model/e2e")]),
            );
            r.insert(
                "tool_exec".into(),
                Route::strict(vec![facade_id.clone()]),
            );
            r
        },
        checkpoint_rules: vec![],
        processors: Default::default(),
        on_member_failed: None,
        tools_include: None,
        tools_exclude: vec![],
        skills_include: None,
        skills_exclude: vec![],
    };
    let mut engine = EngineBuilder::new(vec![top_bus.clone()]).build(cfg).await?;
    let mut state = State::new();
    let cancel = CancellationToken::new();

    let out = tokio::time::timeout(
        Duration::from_secs(10),
        engine.run(&mut state, "use the echo tool".into(), cancel),
    )
    .await
    .expect("engine run timed out")
    .expect("engine run failed");
    assert_eq!(out, "facade loop done");
    // 2026-07-02: state.messages 现仅含对话，无 system prefix
    // messages: user + assistant(t1) + tool(t1) + assistant(text) = 4
    assert_eq!(state.messages.len(), 4);
    assert_eq!(state.messages[1].tool_calls[0].name, "echo");

    _forwarder.abort();
    _translator.abort();
    Ok(())
}
