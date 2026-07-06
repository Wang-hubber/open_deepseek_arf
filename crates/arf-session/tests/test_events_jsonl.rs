//! Task 18 — JsonlSessionStore round/model/tool event tests.

use arf_session::{
    JsonlSessionStore, ModelCallRecord, SessionStore, ToolCallRecord,
};
use chrono::Utc;
use uuid::Uuid;

// [方法] record_round_start 写 JSONL
#[tokio::test]
async fn round_start_persists_to_jsonl() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    store.record_round_start("A", 3).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"event_type\":\"round_start\""));
    assert!(content.contains("\"round\":3"));
}

// [方法] record_round_end 写 JSONL + duration
#[tokio::test]
async fn round_end_persists_duration() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    store.record_round_start("A", 1).await.unwrap();
    store.record_round_end("A", 1, 4230).await.unwrap();

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    assert!(content.contains("\"event_type\":\"round_end\""));
    assert!(content.contains("\"round\":1"));
    assert!(content.contains("\"duration_ms\":4230"));
}

// [方法] record_model_call_end 写 JSONL + usage + turn/round
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
    assert!(content.contains("\"event_type\":\"model_call_end\""));
    assert!(content.contains("\"model\":\"deepseek-chat\""));
    assert!(content.contains("\"input_tokens\":152"));
    assert!(content.contains("\"output_tokens\":89"));
    assert!(content.contains("\"total_tokens\":241"));
    assert!(content.contains("\"turn\":7"));
    assert!(content.contains("\"round\":3"));
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
    assert!(content.contains("\"event_type\":\"tool_call_end\""));
    assert!(content.contains("\"tool_name\":\"write_file\""));
    assert!(content.contains("\"duration_ms\":42"));
    assert!(content.contains("\"success\":true"));
    assert!(content.contains("\"error\":null"));
    assert!(content.contains("\"turn\":8"));
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
    assert!(content.contains("\"success\":false"));
    assert!(content.contains("\"error\":\"permission denied\""));
}

// [持久化] 4 种事件都 fsync（文件可被立刻读取，不丢数据）
#[tokio::test]
async fn all_four_events_fsync() {
    let tmp = tempfile::tempdir().unwrap();
    let store = JsonlSessionStore::new(tmp.path());

    store.record_round_start("A", 1).await.unwrap();
    store.record_round_end("A", 1, 100).await.unwrap();
    store
        .record_model_call_end(
            "A",
            &ModelCallRecord {
                model: "m".into(),
                input_tokens: 1,
                output_tokens: 1,
                total_tokens: 2,
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
                tool_name: "t".into(),
                duration_ms: 1,
                success: true,
                error: None,
                turn: 1,
                round: 1,
                at: Utc::now(),
            },
        )
        .await
        .unwrap();

    // 4 行事件 + snapshot/save 没有 → 4 行
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
        store.record_round_start("A", r).await.unwrap();
        store.record_round_end("A", r, 100 * r as u64).await.unwrap();
    }

    let content = std::fs::read_to_string(tmp.path().join("events.A.jsonl")).unwrap();
    let lines: Vec<&str> = content.lines().filter(|l| !l.is_empty()).collect();
    assert_eq!(lines.len(), 6, "3 个 round × 2 个事件 = 6 行");
    // 第 1 个 round_end 的 duration_ms = 100
    assert!(content.contains("\"duration_ms\":100"));
    assert!(content.contains("\"duration_ms\":200"));
    assert!(content.contains("\"duration_ms\":300"));
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