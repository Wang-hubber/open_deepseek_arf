//! Task 18/19 — SqliteSessionStore round/model/tool event tests.
//!
//! Migrated to the unified `events` table (Task 19). Tests assert on the new
//! `kind` column instead of the old `event_type`.

use arf_session::{Event, ModelCallRecord, SessionStore, SqliteSessionStore, ToolCallRecord};
use chrono::Utc;
use serde_json::json;

// [方法] Sqlite 4 事件写 events 表（kind 列）
#[tokio::test]
async fn sqlite_all_four_events_persist() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    store.record_event("A", &Event::RoundStart { round: 1, captured_at: Utc::now() }).await.unwrap();
    store.record_event("A", &Event::RoundEnd { round: 1, captured_at: Utc::now() }).await.unwrap();
    store.record_event("A", &Event::ModelCallEnd {
        round: 1, turn: 1, model: "deepseek-chat".into(),
        input_tokens: 100, output_tokens: 50, total_tokens: 150,
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("A", &Event::ToolCallEnd {
        round: 1, turn: 2, tool: "read_file".into(),
        success: true, error: None,
        captured_at: Utc::now(),
    }).await.unwrap();

    // 验证存到 events 表（4 行）
    let conn = store.conn.lock().await;
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE session_id = 'A'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 4, "应写入 4 行事件");
}

// [方法] 多 round 各自分开写
#[tokio::test]
async fn sqlite_multiple_round_events_distinct() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    for r in 1..=3 {
        store.record_event("A", &Event::RoundStart { round: r, captured_at: Utc::now() }).await.unwrap();
        store.record_event("A", &Event::RoundEnd { round: r, captured_at: Utc::now() }).await.unwrap();
    }
    let conn = store.conn.lock().await;
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE session_id = 'A' AND kind = 'round_start'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 3);
    let count_end: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE session_id = 'A' AND kind = 'round_end'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count_end, 3);
}

// [方法] model_call_end 字段正确存（payload_json 包含完整 record）
#[tokio::test]
async fn sqlite_model_call_end_fields() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    store.record_event("A", &Event::ModelCallEnd {
        round: 2, turn: 5, model: "qwen3-max".into(),
        input_tokens: 200, output_tokens: 100, total_tokens: 300,
        captured_at: Utc::now(),
    }).await.unwrap();

    let conn = store.conn.lock().await;
    let payload_json: String = conn
        .query_row(
            "SELECT payload_json FROM events
             WHERE session_id = 'A' AND kind = 'model_call_end'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let v: serde_json::Value = serde_json::from_str(&payload_json).unwrap();
    assert_eq!(v["model"], "qwen3-max");
    assert_eq!(v["input_tokens"], 200);
    assert_eq!(v["output_tokens"], 100);
    assert_eq!(v["total_tokens"], 300);
    assert_eq!(v["turn"], 5);
    assert_eq!(v["round"], 2);
}

// [方法] tool_call_end success + failure 都存（payload_json 区分）
#[tokio::test]
async fn sqlite_tool_call_end_success_and_failure() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    store.record_event("A", &Event::ToolCallEnd {
        round: 1, turn: 1, tool: "ok_tool".into(),
        success: true, error: None,
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("A", &Event::ToolCallEnd {
        round: 1, turn: 2, tool: "fail_tool".into(),
        success: false, error: Some("denied by policy".into()),
        captured_at: Utc::now(),
    }).await.unwrap();

    let conn = store.conn.lock().await;
    let mut stmt = conn
        .prepare(
            "SELECT payload_json FROM events
             WHERE session_id = 'A' AND kind = 'tool_call_end'
             ORDER BY captured_at ASC",
        )
        .unwrap();
    let payloads: Vec<serde_json::Value> = stmt
        .query_map([], |r| {
            let s: String = r.get(0)?;
            Ok(serde_json::from_str(&s).unwrap_or(serde_json::Value::Null))
        })
        .unwrap()
        .filter_map(Result::ok)
        .collect();
    assert_eq!(payloads.len(), 2);

    // success 行
    assert_eq!(payloads[0]["tool"], "ok_tool");
    assert_eq!(payloads[0]["success"], true);
    assert_eq!(payloads[0]["error"], serde_json::Value::Null);

    // failure 行
    assert_eq!(payloads[1]["tool"], "fail_tool");
    assert_eq!(payloads[1]["success"], false);
    assert_eq!(payloads[1]["error"], "denied by policy");
}

// [边界] session 间隔离
#[tokio::test]
async fn sqlite_events_isolated_per_session() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    store.record_event("A", &Event::RoundStart { round: 1, captured_at: Utc::now() }).await.unwrap();
    store.record_event("A", &Event::RoundEnd { round: 1, captured_at: Utc::now() }).await.unwrap();
    store.record_event("B", &Event::RoundStart { round: 1, captured_at: Utc::now() }).await.unwrap();

    let conn = store.conn.lock().await;
    let a: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE session_id = 'A'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let b: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE session_id = 'B'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(a, 2);
    assert_eq!(b, 1);
}

// [兼容] 老行（缺新列字段）能被新代码读出（不阻塞 pending_outbound 查询）
// 旧数据可能 msg_type=NULL / source_node=NULL —— pending_outbound 不应 panic
#[tokio::test]
async fn sqlite_legacy_row_with_null_msg_type_does_not_break_pending() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    // 手动 insert 一行 kind=outbound_sent 但 msg_type=NULL（模拟老数据）
    let conn = store.conn.lock().await;
    conn.execute(
        "INSERT INTO events (session_id, captured_at, kind, msg_type, correlation_id, attempt)
         VALUES ('legacy', '2026-01-01T00:00:00Z', 'outbound_sent', NULL, NULL, 1)",
        [],
    ).unwrap();
    drop(conn);

    // pending_outbound 不应 panic；NULL msg_type/correlation_id 不会出现在结果
    let pending = store.pending_outbound("legacy").await.unwrap();
    assert!(pending.is_empty(), "NULL correlation_id 行不出现在 pending");
}

// [持久化] record_structs 字段都序列化/反序列化
#[test]
fn sqlite_record_structs_serde_roundtrip() {
    let m = ModelCallRecord {
        model: "x".into(),
        input_tokens: 1,
        output_tokens: 2,
        total_tokens: 3,
        turn: 4,
        round: 5,
        at: Utc::now(),
    };
    let j = serde_json::to_string(&m).unwrap();
    let back: ModelCallRecord = serde_json::from_str(&j).unwrap();
    assert_eq!(back, m);
}