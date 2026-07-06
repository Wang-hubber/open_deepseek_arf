//! Task 18 — SqliteSessionStore round/model/tool event tests.

use arf_session::{ModelCallRecord, SessionStore, SqliteSessionStore, ToolCallRecord};
use chrono::Utc;

// [方法] Sqlite 4 事件写 peer_events 表
#[tokio::test]
async fn sqlite_all_four_events_persist() {
    let store = SqliteSessionStore::in_memory().await.unwrap();

    store.record_round_start("A", 1).await.unwrap();
    store.record_round_end("A", 1, 100).await.unwrap();
    store
        .record_model_call_end(
            "A",
            &ModelCallRecord {
                model: "deepseek-chat".into(),
                input_tokens: 100,
                output_tokens: 50,
                total_tokens: 150,
                turn: 1,
                round: 1,
                at: Utc::now(),
            },
        )
        .await
        .unwrap();
    store
        .record_tool_call_end(
            "A",
            &ToolCallRecord {
                tool_name: "read_file".into(),
                duration_ms: 20,
                success: true,
                error: None,
                turn: 2,
                round: 1,
                at: Utc::now(),
            },
        )
        .await
        .unwrap();

    // 验证存到 peer_events 表（4 行）
    let conn = store.conn.lock().await;
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM peer_events WHERE session_id = 'A'",
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
        store.record_round_start("A", r).await.unwrap();
        store.record_round_end("A", r, 100 * r as u64).await.unwrap();
    }
    let conn = store.conn.lock().await;
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM peer_events WHERE session_id = 'A' AND event_type = 'round_start'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 3);
    let count_end: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM peer_events WHERE session_id = 'A' AND event_type = 'round_end'",
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
    store
        .record_model_call_end(
            "A",
            &ModelCallRecord {
                model: "qwen3-max".into(),
                input_tokens: 200,
                output_tokens: 100,
                total_tokens: 300,
                turn: 5,
                round: 2,
                at: Utc::now(),
            },
        )
        .await
        .unwrap();

    let conn = store.conn.lock().await;
    let payload_json: String = conn
        .query_row(
            "SELECT payload_json FROM peer_events
             WHERE session_id = 'A' AND event_type = 'model_call_end'",
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

    store
        .record_tool_call_end(
            "A",
            &ToolCallRecord {
                tool_name: "ok_tool".into(),
                duration_ms: 10,
                success: true,
                error: None,
                turn: 1,
                round: 1,
                at: Utc::now(),
            },
        )
        .await
        .unwrap();
    store
        .record_tool_call_end(
            "A",
            &ToolCallRecord {
                tool_name: "fail_tool".into(),
                duration_ms: 5,
                success: false,
                error: Some("denied by policy".into()),
                turn: 2,
                round: 1,
                at: Utc::now(),
            },
        )
        .await
        .unwrap();

    let conn = store.conn.lock().await;
    let mut stmt = conn
        .prepare(
            "SELECT payload_json FROM peer_events
             WHERE session_id = 'A' AND event_type = 'tool_call_end'
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
    assert_eq!(payloads[0]["tool_name"], "ok_tool");
    assert_eq!(payloads[0]["success"], true);
    assert_eq!(payloads[0]["error"], serde_json::Value::Null);
    assert_eq!(payloads[0]["duration_ms"], 10);

    // failure 行
    assert_eq!(payloads[1]["tool_name"], "fail_tool");
    assert_eq!(payloads[1]["success"], false);
    assert_eq!(payloads[1]["error"], "denied by policy");
    assert_eq!(payloads[1]["duration_ms"], 5);
}

// [边界] session 间隔离
#[tokio::test]
async fn sqlite_events_isolated_per_session() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    store.record_round_start("A", 1).await.unwrap();
    store.record_round_end("A", 1, 100).await.unwrap();
    store.record_round_start("B", 1).await.unwrap();

    let conn = store.conn.lock().await;
    let a: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM peer_events WHERE session_id = 'A'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let b: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM peer_events WHERE session_id = 'B'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(a, 2);
    assert_eq!(b, 1);
}

// [兼容] 旧 peer_events 表存在不影响新事件写入（schema migration 自动兼容）
#[tokio::test]
async fn sqlite_old_schema_does_not_break_new_writes() {
    let store = SqliteSessionStore::in_memory().await.unwrap();
    // 直接 INSERT 一行老 peer_message_sent（没有 round/model 字段）
    let conn = store.conn.lock().await;
    conn.execute(
        "INSERT INTO peer_events
            (session_id, captured_at, event_type, correlation_id, attempt)
         VALUES ('legacy', '2026-01-01T00:00:00Z', 'peer_message_sent', 'c1', 1)",
        [],
    )
    .unwrap();
    drop(conn);

    // 新 event 写入不冲突
    store.record_round_start("legacy", 1).await.unwrap();
    let conn = store.conn.lock().await;
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM peer_events WHERE session_id = 'legacy'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 2, "老事件 + 新事件 共 2 行");
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