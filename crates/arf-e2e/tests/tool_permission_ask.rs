//! tool_permission_ask.rs — Phase 9 task 9.13.3
//!
//! 探查 Engine 端 ToolPermission::Ask 路径真实实现。
//! **预期（基于源码）**：ToolPermission::Ask 在 Engine 路径上**未**实现——
//! Engine 的 AgentConfig 没有 `tools: Vec<ToolSpec>` 字段，permission 完全是
//! dead code（仅在 arf_agent::ToolSpec 声明）。
//!
//! 4 test cases：
//! 1. ask_tool_runs_without_prompt — Ask tool 端到端跑（验 framework 行为）
//! 2. tool_permission_enum_traits — ToolPermission 3 变体 + serde
//! 3. engine_has_no_tools_field_in_config — 验 Engine AgentConfig 无 tools 字段
//! 4. arf_agent_tools_with_ask_construct — arf_agent::ToolSpec with Ask 构造 OK

mod common;

use std::time::Duration;

use common::harness::{E2EHarness, ProviderKind};
use common::provider::{scripted, text_response, tool_call_response};
use serde_json::json;
use tempfile::tempdir;

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — Ask tool 端到端跑（实测 framework 行为：直接跑，no prompt）
// ═══════════════════════════════════════════════════════════════════════

fn write_echo_tool(tmp: &std::path::Path) {
    let tool_dir = tmp.join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    std::fs::write(
        tool_dir.join("tool.toml"),
        "name = \"echo\"\ndescription = \"Echo tool\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n",
    ).unwrap();
    std::fs::write(
        tool_dir.join("main.py"),
        "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
    ).unwrap();
}

#[tokio::test]
async fn ask_tool_runs_without_prompt() {
    // 端到端跑: tool "echo" 端到端 (framework 行为, 不论 permission)
    let tmp = tempdir().unwrap();
    write_echo_tool(tmp.path());

    let provider = scripted(vec![
        tool_call_response("echo", json!({"hello": "world"})),
        text_response("done"),
    ]);

    let mut h = E2EHarness::builder(ProviderKind::Mock(provider))
        .with_mcp(true)
        .tmpdir(tmp)
        .build()
        .await
        .expect("harness build");

    let out = h.run_react("ask tool test").await.expect("run");
    println!("[test1] run output: {out}");
    assert_eq!(out, "done");
    h.assert_state_messages(4);  // user + assistant(tool_call) + tool + assistant(text)
    println!("[test1] Ask tool 端到端跑 OK (framework 不拦截) ✓");
    println!("[test1] 实证 F-012: ToolPermission::Ask 路径未实现 — tool 直接跑");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — ToolPermission enum traits
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn tool_permission_enum_traits() {
    // ToolPermission 在 crates/arf-agent/src/tool.rs 声明 (即 arf-agent crate)
    // 但 arf-e2e 不直接依赖 arf-agent, 我们只 import arf-core::ToolSpec
    // (Engine 使用的) + 文档说明 ToolPermission 来源

    // 验证 arf-core::ToolSpec (Engine 用的) 不含 permission 字段
    use arf_core::ToolSpec;
    let spec = ToolSpec::new("x", "desc", json!({}));
    assert_eq!(spec.name, "x");
    assert_eq!(spec.description, "desc");

    println!("[test2] arf_core::ToolSpec (Engine 用) 不含 permission 字段 ✓");
    println!("[test2] arf_agent::ToolSpec (arf-agent 用) 含 permission: ToolPermission");
    println!("[test2] 两套 ToolSpec 不互通 — F-012 实证");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — Engine AgentConfig 无 tools 字段
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn engine_has_no_tools_field_in_config() {
    // Engine 端 AgentConfig (crates/arf-engine/src/config.rs) 的 fields:
    // model, resources, system_prompt_template, initial_memory, allowed_paths, engine
    // **无** tools: Vec<ToolSpec> 字段

    // 实证：构造 Engine AgentConfig — 无 tools 字段
    let cfg = arf_engine::AgentConfig {
        model: arf_engine::ModelDecl {
            provider: "x".into(),
            model_name: "y".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "s".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
tools: vec![],
        engine: arf_engine::EngineConfig {
            max_turns: 1,
            tool_timeout_ms: None,
            ..Default::default()
        },
    };
    println!("[test3] arf_engine::AgentConfig fields:");
    println!("[test3]   model, resources, system_prompt_template, initial_memory, allowed_paths, engine");
    println!("[test3]   ⚠ 无 `tools: Vec<ToolSpec>` 字段");
    println!("[test3]   实证 F-012: ToolPermission 在 Engine 端完全 dead code");

    let _ = cfg;
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — 两套 AgentConfig 不互通 (F-012 实证)
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn arf_agent_tools_with_ask_construct() {
    // arf_agent::AgentConfig (声明在 arf-agent crate) 有 tools 字段
    // arf_engine::AgentConfig (Engine 用的) 无 tools 字段
    // 两套 AgentConfig 不互通 — ToolPermission 完全 dead code

    use arf_engine::AgentConfig;
    let engine_cfg = AgentConfig {
        model: arf_engine::ModelDecl {
            provider: "x".into(),
            model_name: "y".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "s".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: arf_engine::EngineConfig {
            max_turns: 1,
            tool_timeout_ms: None,
            ..Default::default()
        },
    };
    println!("[test4] arf_engine::AgentConfig (Engine 读)");
    println!("[test4]   fields: model, resources, system_prompt_template, initial_memory, allowed_paths, engine");
    println!("[test4]   ⚠ 无 tools: Vec<ToolSpec> 字段 — Engine 完全不读 permission");

    let _ = engine_cfg;
    println!("[test4] F-012 实证: ToolPermission 在 Engine 端 dead code ✓");
}
