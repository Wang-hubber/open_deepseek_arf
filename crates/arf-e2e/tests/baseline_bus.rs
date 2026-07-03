//! baseline_bus.rs — Phase 9 task 9.1.1
//!
//! 探查 Bus + 单一 Node + heartbeat baseline。
//! 不引 Engine / ModelAdapter / McpNode 等高层抽象。
//! 输出物是 audit-probe-9.1.1.md（独立文件，独立 commit）。

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use serde_json::json;

#[tokio::test]
async fn baseline_bus_single_node_heartbeat() {
    // 复用 harness 同款参数：heartbeat 500ms / timeout 2s / channel capacity 32
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));

    let my_id = NodeId::new("baseline/single-node");
    let info = NodeInfo {
        node_id: my_id.clone(),
        node_type: "baseline".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    // ToMatch::All — baseline 看全 broadcast；不预设类型过滤
    let filter = MessageFilter {
        types: None,
        to_match: ToMatch::All,
    };
    let _handle = bus.connect(info, filter).await.expect("connect to bus");

    // 等 1 heartbeat tick + 缓冲
    tokio::time::sleep(Duration::from_millis(800)).await;

    let g = bus.graph();
    let online_ids: Vec<_> = g.nodes.iter().map(|n| n.node_id.clone()).collect();
    assert!(
        online_ids.iter().any(|id| *id == my_id),
        "expected {my_id} online, got {online_ids:?}"
    );

    // uptime 已增长至少 500ms
    assert!(
        g.uptime_ms >= 500,
        "expected uptime_ms >= 500, got {}",
        g.uptime_ms
    );

    // message_count baseline 观察：1 tick 周期后是 0。
    // 真实行为：Bus::message_count 仅在 BusCommand::Send 分支递增
    // （crates/arf-bus/src/lib.rs:446），heartbeat tick / Connect / Disconnect
    // 都不增。详见 audit-probe-9.1.1.md §B heartbeat 探查。
    assert_eq!(
        g.message_count, 0,
        "expected baseline message_count = 0 (no BusCommand::Send issued), got {}",
        g.message_count
    );
}
