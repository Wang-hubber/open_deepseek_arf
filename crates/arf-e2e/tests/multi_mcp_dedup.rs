//! multi_mcp_dedup.rs — Phase 9 task 9.7.3
//!
//! 多 MCP + 跨 MCP dedup（同名 tool / AmbiguousTool）端到端探查。
//!
//! 4 test cases：
//! 1. cross_mcp_same_tool_name_triggers_ambiguous — 2 mcp 各自 1 tool "shared" → AmbiguousTool
//! 2. cross_mcp_distinct_tools_no_ambiguity — 2 mcp 各自 tool "alpha" / "beta" → build OK
//! 3. cross_mcp_same_tool_subset_filter_dedups — 2 mcp "shared" + 1 ResourceSpec Subset["shared"] → 仍 AmbiguousTool
//! 4. cross_mcp_three_nodes_two_share_tool — 3 mcp + tool "x" 出现在 2 个 mcp + tool "y" 在第 3 → AmbiguousTool

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::NodeId;
use arf_engine::{AgentConfig, EngineBuilder, EngineConfig, ModelDecl, ResourceSpec};
use arf_mcp::McpNode;
use arf_model_adapter::ModelAdapterNode;
use common::provider::{scripted, text_response};
use serde_json::json;

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn write_tool(root: &PathBuf, name: &str) {
    let tool_dir = root.join("tools").join(name);
    fs::create_dir_all(&tool_dir).unwrap();
    let mut f = fs::File::create(tool_dir.join("tool.toml")).unwrap();
    f.write_all(
        format!(
            "name = \"{name}\"\ndescription = \"{name} tool\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n"
        )
        .as_bytes(),
    )
    .unwrap();
    fs::write(
        tool_dir.join("main.py"),
        "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
    )
    .unwrap();
}

fn make_mcp_with_tool(tmp_root: &PathBuf, ns: &str, tool_name: &str) -> PathBuf {
    let ns_root = tmp_root.join(ns);
    fs::create_dir_all(&ns_root).unwrap();
    write_tool(&ns_root, tool_name);
    ns_root
}

async fn connect_model(bus: &Arc<Bus>) {
    let _ = ModelAdapterNode::new(
        scripted(vec![text_response("ok")]),
        bus,
        NodeId::new("model/e2e"),
    )
    .await
    .expect("model connect");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: 2 mcp 各自 1 tool "shared" → AmbiguousTool
// ═══════════════════════════════════════════════════════════════════════

// [错误] 2 McpNode 独立 root + 各自 tool "shared" + 2 ResourceSpec Subset
// → Engine build 应返 BuildError::AmbiguousTool（tool 跨 mcp 重名）
#[tokio::test]
async fn cross_mcp_same_tool_name_triggers_ambiguous() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    let root_a = make_mcp_with_tool(&root, "a", "shared");
    let root_b = make_mcp_with_tool(&root, "b", "shared");

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    connect_model(&bus).await;

    let node_a = McpNode::local("a", root_a).expect("McpNode a");
    let node_b = McpNode::local("b", root_b).expect("McpNode b");
    node_a.connect(&bus).await.expect("a connect");
    node_b.connect(&bus).await.expect("b connect");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![
            ResourceSpec {
                resource_name: "mcp_a".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["shared"]})),
            },
            ResourceSpec {
                resource_name: "mcp_b".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["shared"]})),
            },
        ],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            ..Default::default()
        },
    };
    let result = EngineBuilder::new(vec![bus.clone()]).build(cfg).await;
    match result {
        Err(arf_engine::BuildError::AmbiguousTool { tool, providers }) => {
            println!("[test1] AmbiguousTool: tool={tool}, providers={providers:?}");
            assert_eq!(tool, "shared");
            assert_eq!(providers.len(), 2, "应有 2 个 provider（mcp_a + mcp_b）");
            // providers[0] = existing NodeId (mcp/a or mcp/b); providers[1] = conflicting spec.resource_name
            let joined = providers.join("|");
            assert!(joined.contains("mcp/") || joined.contains("mcp_"));
        }
        Err(e) => panic!("expected AmbiguousTool, got error: {e}"),
        Ok(_) => panic!("expected AmbiguousTool, got Ok engine"),
    }
    println!("[test1] 2 mcp 各自 'shared' → AmbiguousTool OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: 2 mcp 各自 tool "alpha" / "beta" → build OK
// ═══════════════════════════════════════════════════════════════════════

// [方法] 2 McpNode 各自 tool 不重名 → build 成功，owner_of_tool 各自指向
#[tokio::test]
async fn cross_mcp_distinct_tools_no_ambiguity() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    let root_a = make_mcp_with_tool(&root, "a", "alpha");
    let root_b = make_mcp_with_tool(&root, "b", "beta");

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    connect_model(&bus).await;

    let node_a = McpNode::local("a", root_a).expect("McpNode a");
    let node_b = McpNode::local("b", root_b).expect("McpNode b");
    node_a.connect(&bus).await.expect("a connect");
    node_b.connect(&bus).await.expect("b connect");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![
            ResourceSpec {
                resource_name: "mcp_a".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["alpha"]})),
            },
            ResourceSpec {
                resource_name: "mcp_b".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["beta"]})),
            },
        ],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            ..Default::default()
        },
    };
    let _engine = EngineBuilder::new(vec![bus.clone()])
        .build(cfg)
        .await
        .expect("engine build (distinct tools OK)");
    println!("[test2] 2 mcp 各自 alpha/beta → build OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: 2 mcp "shared" + 1 ResourceSpec Subset["shared"] → 仍 AmbiguousTool
// ═══════════════════════════════════════════════════════════════════════

// [边界] 2 mcp 都有 "shared" + 2 ResourceSpec Subset["shared"]（同名 tool 跨 mcp 显式声明）
// → Subset filter 各自匹配对应 mcp（node_has_any_of 命中）→ tool_index 重复 → AmbiguousTool
//   (registry.rs:62-67 + 102-107 dedup 在 tool_index 插入时触发)
#[tokio::test]
async fn cross_mcp_same_tool_subset_filter_dedups() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    let root_a = make_mcp_with_tool(&root, "a", "shared");
    let root_b = make_mcp_with_tool(&root, "b", "shared");

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    connect_model(&bus).await;

    let node_a = McpNode::local("a", root_a).expect("McpNode a");
    let node_b = McpNode::local("b", root_b).expect("McpNode b");
    node_a.connect(&bus).await.expect("a connect");
    node_b.connect(&bus).await.expect("b connect");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![
            // 2 ResourceSpec 各自 Subset["shared"] —— node_has_any_of 各自匹配对应 mcp
            // → 2 mcp 都被纳入 tool_index → "shared" 重复 → AmbiguousTool
            ResourceSpec {
                resource_name: "shared_a".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["shared"]})),
            },
            ResourceSpec {
                resource_name: "shared_b".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["shared"]})),
            },
        ],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            ..Default::default()
        },
    };
    let result = EngineBuilder::new(vec![bus.clone()]).build(cfg).await;
    match result {
        Err(arf_engine::BuildError::AmbiguousTool { tool, providers }) => {
            println!(
                "[test3] 2 Subset specs dedup path: AmbiguousTool tool={tool}, providers={providers:?}"
            );
            assert_eq!(tool, "shared");
            assert_eq!(providers.len(), 2);
        }
        Err(e) => panic!("expected AmbiguousTool (subset dedup), got error: {e}"),
        Ok(_) => panic!("expected AmbiguousTool, got Ok engine"),
    }
    println!("[test3] 2 Subset specs + 2 mcp 各自 'shared' → AmbiguousTool OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: 3 mcp + tool "x" 在 2 个 mcp + tool "y" 在第 3 → AmbiguousTool("x")
// ═══════════════════════════════════════════════════════════════════════

// [边界] 3 mcp + mcp_a + mcp_b 都有 "x" + mcp_c 有 "y"
// → AmbiguousTool 错误包含 providers=[a, b]（"y" 不在冲突集合）
#[tokio::test]
async fn cross_mcp_three_nodes_two_share_tool() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    let root_a = make_mcp_with_tool(&root, "a", "x");
    let root_b = make_mcp_with_tool(&root, "b", "x");
    let root_c = make_mcp_with_tool(&root, "c", "y");

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    connect_model(&bus).await;

    let node_a = McpNode::local("a", root_a).expect("McpNode a");
    let node_b = McpNode::local("b", root_b).expect("McpNode b");
    let node_c = McpNode::local("c", root_c).expect("McpNode c");
    node_a.connect(&bus).await.expect("a connect");
    node_b.connect(&bus).await.expect("b connect");
    node_c.connect(&bus).await.expect("c connect");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![
            ResourceSpec {
                resource_name: "mcp_a".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["x"]})),
            },
            ResourceSpec {
                resource_name: "mcp_b".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["x"]})),
            },
            ResourceSpec {
                resource_name: "mcp_c".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["y"]})),
            },
        ],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            ..Default::default()
        },
    };
    let result = EngineBuilder::new(vec![bus.clone()]).build(cfg).await;
    match result {
        Err(arf_engine::BuildError::AmbiguousTool { tool, providers }) => {
            println!(
                "[test4] 3-mcp case: AmbiguousTool tool={tool}, providers={providers:?}"
            );
            assert_eq!(tool, "x", "应冲突 'x'（'y' 唯一不冲突）");
            assert_eq!(providers.len(), 2, "应有 2 个 provider（mcp_a + mcp_b）");
            // providers[0] = existing NodeId (mcp/a or mcp/b); providers[1] = conflicting spec.resource_name
            // —— 应包含 mcp 节点的 NodeId 和另一个 ResourceSpec 的 resource_name
            let joined = providers.join("|");
            assert!(
                joined.contains("mcp/") || joined.contains("mcp_"),
                "providers 应含 mcp 节点标识: {joined}"
            );
        }
        Err(e) => panic!("expected AmbiguousTool, got error: {e}"),
        Ok(_) => panic!("expected AmbiguousTool, got Ok engine"),
    }
    println!("[test4] 3 mcp + 2 share 'x' + 1 unique 'y' → AmbiguousTool('x') OK ✓");
}
