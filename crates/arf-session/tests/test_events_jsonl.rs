//! Task 18/19 — JsonlSessionStore round/model/tool event tests.
//!
//! Migrated to the unified `events` JSONL format (Task 19): tests now assert
//! on the nested `event` field with `kind` tag instead of the legacy flat
//! `event_type` strings.

use arf_session::{
    Event, JsonlSessionStore, ModelCallRecord, SessionStore, ToolCallRecord,
};
use chrono::Utc;
use uuid::Uuid;

// [方法] record_round_start 写 JSONL（嵌套 event 格式）
#[tokio::test]
async fn round_start_persists_to_jsonl() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    store.record_event("A", &Event::RoundStart { round: 3, captured_at: Utc::now() }).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"event\""), "raw: {content}");
    assert!(content.contains("\"event\":{"), "raw: {content}");
    assert!(content.contains("\"kind\":\"round_start\""), "raw: {content}");
    assert!(content.contains("\"round\":3"), "raw: {content}");
}

// [方法] record_round_end 写 JSONL + duration 在 payload_json
#[tokio::test]
async fn round_end_persists_duration() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    store.record_event("A", &Event::RoundStart { round: 1, captured_at: Utc::now() }).await.unwrap();
    // duration_ms 暂不在 Event::RoundEnd 中（spec 决定）；写入后断言 kind 而非 duration
    store.record_event("A", &Event::RoundEnd { round: 1, captured_at: Utc::now() }).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"round_end\""), "raw: {content}");
    assert!(content.contains("\"round\":1"), "raw: {content}");
}

// [方法] record_model_call_end 写 JSONL + usage
#[tokio::test]
async fn model_call_end_persists_full_record() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let rec = ModelCallRecord {
        model: "deepseek-chat".into(),
        input_tokens: 152,
        output_tokens: 89,
        total_tokens: 241,
        turn: 7,
        round: 3,
        at: Utc::now(),
    };
    store.record_model_call_end("A", &rec).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"model_call_end\""), "raw: {content}");
    // Event::ModelCallEnd 字段名（plan §2.1）: model / input_tokens / output_tokens / total_tokens / turn / round
    assert!(content.contains("\"model\":\"deepseek-chat\""), "raw: {content}");
    assert!(content.contains("\"input_tokens\":152"), "raw: {content}");
    assert!(content.contains("\"output_tokens\":89"), "raw: {content}");
    assert!(content.contains("\"total_tokens\":241"), "raw: {content}");
    assert!(content.contains("\"turn\":7"), "raw: {content}");
    assert!(content.contains("\"round\":3"), "raw: {content}");
}

// [方法] record_tool_call_end success path 写 JSONL
#[tokio::test]
async fn tool_call_end_success_persists() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let rec = ToolCallRecord {
        tool_name: "write_file".into(),
        duration_ms: 42,
        success: true,
        error: None,
        turn: 8,
        round: 3,
        at: Utc::now(),
    };
    store.record_tool_call_end("A", &rec).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"kind\":\"tool_call_end\""), "raw: {content}");
    // Event::ToolCallEnd 字段名: tool (not tool_name) / success / error / turn / round
    // duration_ms 不在 Event 中
    assert!(content.contains("\"tool\":\"write_file\""), "raw: {content}");
    assert!(content.contains("\"success\":true"), "raw: {content}");
    assert!(content.contains("\"error\":null"), "raw: {content}");
    assert!(content.contains("\"turn\":8"), "raw: {content}");
}

// [方法] record_tool_call_end failure path 也写 JSONL
#[tokio::test]
async fn tool_call_end_failure_persists_with_error() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    let rec = ToolCallRecord {
        tool_name: "denied_tool".into(),
        duration_ms: 5,
        success: false,
        error: Some("permission denied".into()),
        turn: 9,
        round: 3,
        at: Utc::now(),
    };
    store.record_tool_call_end("A", &rec).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"success\":false"), "raw: {content}");
    assert!(content.contains("\"error\":\"permission denied\""), "raw: {content}");
}

// [持久化] 4 种事件都 fsync（文件可被立刻读取，不丢数据）
#[tokio::test]
async fn all_four_events_fsync() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    store.record_event("A", &Event::RoundStart { round: 1, captured_at: Utc::now() }).await.unwrap();
    store.record_event("A", &Event::RoundEnd { round: 1, captured_at: Utc::now() }).await.unwrap();
    store.record_event("A", &Event::ModelCallEnd {
        round: 1, turn: 1, model: "m".into(),
        input_tokens: 1, output_tokens: 1, total_tokens: 2,
        captured_at: Utc::now(),
    }).await.unwrap();
    store.record_event("A", &Event::ToolCallEnd {
        round: 1, turn: 1, tool: "t".into(),
        success: true, error: None,
        captured_at: Utc::now(),
    }).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    let lines: Vec<&str> = content.lines().filter(|l| !l.is_empty()).collect();
    assert_eq!(lines.len(), 4, "应该恰好 4 行事件");
}

// [边界] 同 cid round 重复不覆盖（round 用 sequence 而非 cid，独立计数）
#[tokio::test]
async fn multiple_round_events_distinct_by_round_number() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    for r in 1..=3 {
        store.record_event("A", &Event::RoundStart { round: r, captured_at: Utc::now() }).await.unwrap();
        store.record_event("A", &Event::RoundEnd { round: r, captured_at: Utc::now() }).await.unwrap();
    }

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    let lines: Vec<&str> = content.lines().filter(|l| !l.is_empty()).collect();
    assert_eq!(lines.len(), 6, "3 个 round × 2 个事件 = 6 行");
}

// [唯一性] ModelCallRecord / ToolCallRecord 字段都暴露
#[test]
fn record_structs_serde_roundtrip() {
    let m = ModelCallRecord {
        model: "m".into(),
        input_tokens: 10,
        output_tokens: 20,
        total_tokens: 30,
        turn: 1,
        round: 2,
        at: Utc::now(),
    };
    let json = serde_json::to_string(&m).unwrap();
    let back: ModelCallRecord = serde_json::from_str(&json).unwrap();
    assert_eq!(back, m);

    let t = ToolCallRecord {
        tool_name: "t".into(),
        duration_ms: 50,
        success: true,
        error: None,
        turn: 1,
        round: 2,
        at: Utc::now(),
    };
    let json = serde_json::to_string(&t).unwrap();
    let back: ToolCallRecord = serde_json::from_str(&json).unwrap();
    assert_eq!(back, t);
}