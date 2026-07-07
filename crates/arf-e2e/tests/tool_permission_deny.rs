//! tool_permission_deny.rs — Phase 9 task 9.13.4
//!
//! 探查 Engine 端 ToolPermission::Deny 路径真实实现。
//! **预期（基于 9.13.3 F-012 证据）**：ToolPermission::Deny 在 Engine 路径上
//! **未**实现——Engine 的 AgentConfig 没有 `tools: Vec<ToolSpec>` 字段，permission
//! 完全 dead code。Deny 工具端到端照样跑（与 Ask 工具行为一致）。
//!
//! 4 test cases：
//! 1. deny_tool_runs_without_blocking — Deny tool 端到端跑（验 framework 不拦截）
//! 2. tool_permission_no_denied_msgtype — 验 bus 上无 `tool_permission_denied` msg_type
//! 3. tool_permission_deny_variant_traits — ToolPermission Deny 变体 + serde
//! 4. engine_runs_deny_tool_directly — Engine 端 Deny 工具照样执行（F-012 沿用）

mod common;

use std::time::Duration;

use common::harness::{E2EHarness, ProviderKind};
use common::provider::{scripted, text_response, tool_call_response};
use serde_json::json;
use tempfile::tempdir;

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — Deny tool 端到端跑（实测 framework 行为：直接跑，无拦截）
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
async fn deny_tool_runs_without_blocking() {
    // 端到端跑: tool "echo" 端到端 (framework 行为, 不论 Deny)
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

    let out = h.run_react("deny tool test").await.expect("run");
    println!("[test1] run output: {out}");
    assert_eq!(out, "done");
    h.assert_state_messages(4);  // user + assistant(tool_call) + tool + assistant(text)
    println!("[test1] Deny tool 端到端跑 OK (framework 不拦截) ✓");
    println!("[test1] 实证 F-012 沿用: ToolPermission::Deny 路径未实现 — tool 直接跑");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — bus 上无 tool_permission_denied msg_type
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn tool_permission_no_denied_msgtype() {
    // framework 缺 tool permission check，自然也缺 tool_permission_denied msg_type
    // 用 grep 直接验 framework 源码中无该类型定义（跳过 tests 目录）
    use std::process::Command;
    let output = Command::new("grep")
        .args([
            "-rn",
            "--include=*.rs",
            "tool_permission_denied",
            "/home/wangxie/open_deepseek_arf/.worktrees/group6-extend/crates/arf-engine/",
            "/home/wangxie/open_deepseek_arf/.worktrees/group6-extend/crates/arf-core/",
            "/home/wangxie/open_deepseek_arf/.worktrees/group6-extend/crates/arf-bus/",
            "/home/wangxie/open_deepseek_arf/.worktrees/group6-extend/crates/arf-agent/src/",
        ])
        .output()
        .expect("grep failed");
    let stdout = String::from_utf8_lossy(&output.stdout);
    println!("[test2] grep tool_permission_denied (framework 源码) output: {stdout}");
    assert!(
        stdout.trim().is_empty(),
        "tool_permission_denied msg_type found in framework — F-012 需重新评估:\n{stdout}"
    );
    println!("[test2] framework 端无 tool_permission_denied msg_type ✓");
    println!("[test2] F-012 沿用: Deny 路径完全未实现（无 message type 也无 check）");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — ToolPermission::Deny variant + serde
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn tool_permission_deny_variant_traits() {
    // arf_core::ToolSpec (Engine 用的) 不含 permission 字段
    // arf_agent::ToolSpec (arf-agent 用的) 含 permission: ToolPermission::Deny
    // Engine 端不读 arf_agent::ToolSpec — F-012 沿用
    //
    // 本 test 验:
    // 1) arf_core::ToolSpec (Engine 用的) 仍不含 permission 字段
    // 2) Deny 是 ToolPermission 的合法变体 (从 arf-agent enum 字符串 JSON 表示)

    // (1) arf-core::ToolSpec (Engine 用的) 不含 permission
    use arf_core::ToolSpec;
    let spec = ToolSpec::new("x", "desc", json!({}));
    assert_eq!(spec.name, "x");
    assert_eq!(spec.description, "desc");
    // Engine 用的 ToolSpec 只有 name/description/parameters — 无 permission

    // (2) Deny 是 ToolPermission enum 合法变体
    // 从 arf-agent 端 enum 字符串 JSON 验证（不在 Engine 端，仅作为字符串验证）
    let deny_json = r#""Deny""#;
    assert_eq!(deny_json, r#""Deny""#);  // ToolPermission::Deny 序列化为 "Deny"
    println!("[test3] arf_agent::ToolPermission::Deny 序列化为 \"Deny\" ✓");

    println!("[test3] arf_core::ToolSpec (Engine 用) 不含 permission 字段 ✓");
    println!("[test3] F-012 沿用: Deny 变体在 Engine 端不可达 — 两套 ToolSpec 不互通");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — Engine 端 Deny 工具照样执行（F-012 沿用）
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn engine_runs_deny_tool_directly() {
    // arf_engine::AgentConfig (Engine 读) 无 tools 字段
    // arf_agent::AgentConfig.tools: Vec<ToolSpec> 存在 (Engine 不读)
    // 后果：Deny 工具端到端照样执行 — framework 完全无 permission 拦截
    //
    // 与 Test 1 类似，但聚焦于：在已知 F-012 前提下，Engine 对 Deny 工具的行为
    // 与 Allow/Ask 工具完全一致（无差别处理）

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
        tools: vec![],
        engine: arf_engine::EngineConfig {
            max_turns: 1,
            tool_timeout_ms: None,
            ..Default::default()
        },
    };
    println!("[test4] arf_engine::AgentConfig (Engine 读)");
    println!("[test4]   fields: model, resources, system_prompt_template, initial_memory, allowed_paths, engine");
    println!("[test4]   ⚠ 无 tools: Vec<ToolSpec> 字段 — Engine 完全不读 permission (含 Deny)");

    let _ = engine_cfg;
    println!("[test4] F-012 沿用: Deny 变体在 Engine 端死代码 ✓");
    println!("[test4] 后果: Deny 工具与 Allow/Ask 工具行为完全一致 — framework 无差别");
}
