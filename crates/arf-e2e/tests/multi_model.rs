//! multi_model.rs — Phase 9 task 9.2.5
//!
//! 探查 Engine 的多 ModelAdapter 候选切换。
//! **真实 LLM** 端到端：DeepSeek V4-flash + DashScope qwen3.7-max-preview
//! 同时挂到 bus，验证 engine 按 AgentConfig.model.provider 路由到正确节点。
//!
//! 凭据安全：两把 API key **仅**经 env 变量传入（DEEPSEEK_API_KEY /
//! DASHSCOPE_API_KEY）；缺 key → 测试 env-gate skip（不 fail）。
//! 不使用 `#[ignore]`（遵循 common/env.rs 约定）。
//!
//! 5 test cases：
//! 1. multi_model_qwen_only_default        — 单 qwen，default provider
//! 2. multi_model_deepseek_only_default    — 单 deepseek，default provider
//! 3. multi_model_both_picks_qwen          — qwen + deepseek，default=qwen
//! 4. multi_model_both_picks_deepseek      — qwen + deepseek，override=deepseek
//! 5. multi_model_invalid_provider_errors  — override="nonexistent" → BuildError
//!
//! 输出物是 `docs/v1.x/phase9/audit-probe-9.2.5.md`（独立文件，独立 commit）。

mod common;

use common::harness::{E2EHarness, ProviderKind};

fn print_response(test_name: &str, out: &str) {
    println!("[multi_model] {test_name} response: {out}");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — 单 qwen，default provider（baseline）
// ═══════════════════════════════════════════════════════════════════════

// [方法] baseline：单 qwen 节点，AgentConfig 默认 provider = "openai"（OpenAIProvider.name()），
// engine 解析到 qwen 节点 → qwen 响应。验证 live_qwen() factory + harness primary 节点解析。
#[tokio::test]
async fn multi_model_qwen_only_default() {
    let Some(provider) = common::provider::live_qwen() else {
        return;
    };
    let mut h = E2EHarness::builder(ProviderKind::Live(provider))
        .build()
        .await
        .expect("build");
    let out = h.run_react("用一句话介绍你自己").await.expect("run");
    print_response("qwen_only_default", &out);
    assert!(!out.is_empty(), "qwen response must be non-empty");
    assert!(out.chars().count() > 5, "qwen response must be substantive");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — 单 deepseek，default provider
// ═══════════════════════════════════════════════════════════════════════

// [方法] 单 deepseek 节点，AgentConfig 默认 provider = "deepseek"（DeepSeekProvider.name()），
// engine 解析到 deepseek 节点 → deepseek 响应。验证 live_deepseek() factory。
#[tokio::test]
async fn multi_model_deepseek_only_default() {
    let Some(provider) = common::provider::live_deepseek() else {
        return;
    };
    let mut h = E2EHarness::builder(ProviderKind::Live(provider))
        .build()
        .await
        .expect("build");
    let out = h.run_react("用一句话介绍你自己").await.expect("run");
    print_response("deepseek_only_default", &out);
    assert!(!out.is_empty(), "deepseek response must be non-empty");
    assert!(out.chars().count() > 5, "deepseek response must be substantive");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — qwen + deepseek 共存，default provider = qwen
// ═══════════════════════════════════════════════════════════════════════

// [方法] 2 个 model 节点共存（primary=qwen, extra=deepseek），AgentConfig
// 默认 provider = qwen。engine 解析首个匹配 "openai" 能力（即 qwen 节点）。
// 验证 resolve_model 按 capabilities.provider 匹配 + BusGraph node 顺序。
#[tokio::test]
async fn multi_model_both_picks_qwen() {
    let Some(qwen) = common::provider::live_qwen() else { return; };
    let Some(deepseek) = common::provider::live_deepseek() else { return; };
    let mut h = E2EHarness::builder(ProviderKind::Live(qwen))
        .with_extra_providers(vec![deepseek])
        .build()
        .await
        .expect("build");
    let out = h.run_react("用一句话介绍你自己").await.expect("run");
    print_response("both_picks_qwen", &out);
    // qwen 倾向回答"通义千问 / qwen / 阿里巴巴"等关键词
    // 验证它确实命中 qwen（不是 deepseek）
    let is_qwen = out.contains("通义") || out.contains("qwen") || out.contains("Qwen")
        || out.contains("千问") || out.contains("阿里");
    let is_deepseek = out.contains("DeepSeek") || out.contains("深度求索") || out.contains("deepseek");
    println!(
        "[multi_model] both_picks_qwen: is_qwen={is_qwen}, is_deepseek={is_deepseek}"
    );
    assert!(
        is_qwen || !is_deepseek,
        "expected qwen response (or at least not deepseek); got: {out}"
    );
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — qwen + deepseek 共存，override provider = deepseek
// ═══════════════════════════════════════════════════════════════════════

// [方法] 2 个 model 节点共存，AgentConfig.model.provider 显式设 = "deepseek"。
// engine 解析匹配 "deepseek" 能力（即 deepseek 节点），即使 qwen 节点先在 bus 上。
// 验证 .model_provider() builder 覆盖默认 + engine 真正按 provider 名匹配。
#[tokio::test]
async fn multi_model_both_picks_deepseek() {
    let Some(qwen) = common::provider::live_qwen() else { return; };
    let Some(deepseek) = common::provider::live_deepseek() else { return; };
    let mut h = E2EHarness::builder(ProviderKind::Live(qwen))
        .with_extra_providers(vec![deepseek])
        .model_provider("deepseek")
        .build()
        .await
        .expect("build");
    let out = h.run_react("用一句话介绍你自己").await.expect("run");
    print_response("both_picks_deepseek", &out);
    // deepseek 倾向回答"深度求索 / DeepSeek"等关键词
    let is_deepseek = out.contains("DeepSeek") || out.contains("深度求索") || out.contains("deepseek");
    let is_qwen = out.contains("通义") || out.contains("qwen") || out.contains("Qwen")
        || out.contains("千问") || out.contains("阿里");
    println!(
        "[multi_model] both_picks_deepseek: is_deepseek={is_deepseek}, is_qwen={is_qwen}"
    );
    assert!(
        is_deepseek || !is_qwen,
        "expected deepseek response (or at least not qwen); got: {out}"
    );
}

// ═══════════════════════════════════════════════════════════════════════
// Test 5 — 无匹配 provider → BuildError（不需 LLM）
// ═══════════════════════════════════════════════════════════════════════

// [边界] AgentConfig.model.provider = "nonexistent" → engine 解析时无任何
// node_type="model" 节点 capabilities.provider="nonexistent" → BuildError::MissingNodes
// （registry.rs:266）。不需真实 LLM 调用 — error 在 build 阶段。
#[tokio::test]
async fn multi_model_invalid_provider_errors() {
    use common::provider::simple_mock;
    let provider = simple_mock("never-reached");
    let result = E2EHarness::builder(ProviderKind::Mock(provider))
        .model_provider("nonexistent-provider")
        .build()
        .await;
    assert!(result.is_err(), "expected build to fail, got Ok");
    let err_str = format!("{:?}", result.err().unwrap());
    println!("[multi_model] invalid_provider: {err_str}");
    assert!(
        err_str.contains("MissingNodes") || err_str.contains("nonexistent-provider"),
        "expected MissingNodes error mentioning nonexistent-provider; got: {err_str}"
    );
}
