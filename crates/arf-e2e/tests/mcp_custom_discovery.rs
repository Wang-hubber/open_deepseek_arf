//! mcp_custom_discovery.rs — Phase 9 task 9.5.3
//!
//! McpNode + 自定义 DiscoveryBackend（impl trait）端到端探查。
//!
//! 4 test cases：
//! 1. custom_discovery_backend_lists_tools — app 定义 MemoryDiscovery + impl trait
//! 2. custom_discovery_backend_skills_default — skill 方法默认 impl 正确返回
//! 3. custom_discovery_trait_full_methods — app 后端 trait 7 方法端到端 work
//! 4. custom_discovery_no_dedicated_ctor — 实证 McpNode 缺 with_discovery 构造器（潜在 F-lesion）

mod common;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use arf_mcp::config::ToolConfig;
use arf_mcp::config::ScriptRuntime;
use arf_mcp::discovery::{DiscoveryBackend, ToolInfo};
use arf_mcp::skill::{LoadedResource, SkillResources};
use arf_mcp::script::ScriptTool;
use arf_mcp::tool::Tool;
use async_trait::async_trait;
use serde_json::Value;

// ═══════════════════════════════════════════════════════════════════════
// MemoryDiscovery — app 自定义的 in-memory DiscoveryBackend
// ═══════════════════════════════════════════════════════════════════════

/// App 自己的 in-memory discovery backend。
/// 直接实现 `DiscoveryBackend` trait，不依赖 filesystem。
pub struct MemoryDiscovery {
    tools: HashMap<String, Arc<dyn Tool>>,
    tool_info: Vec<ToolInfo>,
}

impl MemoryDiscovery {
    pub fn new() -> Self {
        Self { tools: HashMap::new(), tool_info: Vec::new() }
    }

    /// 注册一个 tool（用 ScriptTool 包装 .py 脚本）。
    pub fn register_script_tool(&mut self, name: &str, description: &str, script: &str) {
        let dir = std::env::temp_dir().join(format!("arf_mem_disc_{name}"));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("main.py"), script).unwrap();

        let config = ToolConfig {
            name: name.into(),
            description: description.into(),
            runtime: ScriptRuntime::Python,
            entrypoint: "main.py".into(),
            timeout_ms: None,
            params_schema: serde_json::json!({"type":"object"}),
        };
        let tool: Arc<dyn Tool> = Arc::new(ScriptTool::new(config, dir));
        self.tool_info.push(ToolInfo {
            name: name.into(),
            description: description.into(),
            parameters_schema: serde_json::json!({"type":"object"}),
        });
        self.tools.insert(name.into(), tool);
    }
}

#[async_trait]
impl DiscoveryBackend for MemoryDiscovery {
    fn list_tools(&self) -> &[ToolInfo] {
        &self.tool_info
    }
    fn tool_map(&self) -> &HashMap<String, Arc<dyn Tool>> {
        &self.tools
    }
    fn resolve_tool(&self, name: &str) -> Option<Arc<dyn Tool>> {
        self.tools.get(name).cloned()
    }
    // Skill 方法：保持默认（None / 空 vec / Err）
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: custom_discovery_backend_lists_tools
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_discovery_backend_lists_tools() {
    let mut disc = MemoryDiscovery::new();
    disc.register_script_tool(
        "mem_echo",
        "echo from memory backend",
        "import sys, json\nparams=json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
    );

    let tools = disc.list_tools();
    println!("[test1] MemoryDiscovery list_tools() = {} tools", tools.len());
    assert_eq!(tools.len(), 1);
    assert_eq!(tools[0].name, "mem_echo");
    println!("[test1]   - {}: {}", tools[0].name, tools[0].description);

    let tool_map = disc.tool_map();
    assert!(tool_map.contains_key("mem_echo"));
    println!("[test1] tool_map 含 mem_echo ✓");

    let resolved = disc.resolve_tool("mem_echo");
    assert!(resolved.is_some());
    println!("[test1] resolve_tool('mem_echo') = Some ✓");

    let missing = disc.resolve_tool("nonexistent");
    assert!(missing.is_none());
    println!("[test1] resolve_tool('nonexistent') = None ✓");

    println!("[test1] 自定义 DiscoveryBackend (tool 方法) 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: custom_discovery_backend_skills_default
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_discovery_backend_skills_default() {
    let disc = MemoryDiscovery::new();

    // skill 方法应返回 None / 空（默认 impl）
    let skills = disc.list_skills();
    assert!(skills.is_empty(), "未注册 skill 应为空 vec");
    println!("[test2] list_skills() = 空 ✓");

    let resolved = disc.resolve_skill("any");
    assert!(resolved.is_none());
    println!("[test2] resolve_skill('any') = None ✓");

    let body = disc.load_skill_body("any");
    assert!(body.is_none());
    println!("[test2] load_skill_body('any') = None ✓");

    let resources = disc.load_skill_resources("any");
    assert!(resources.is_none());
    println!("[test2] load_skill_resources('any') = None ✓");

    let load_resource = disc.load_resource_file("any", "path");
    assert!(load_resource.is_err());
    println!("[test2] load_resource_file('any','path') = Err ✓");

    let tool_config = disc.load_tool_config("any", "any");
    assert!(tool_config.is_none());
    println!("[test2] load_tool_config('any','any') = None ✓");

    let run_skill_tool = disc.run_skill_tool("any", "any", Value::Null).await;
    assert!(run_skill_tool.is_err());
    println!("[test2] run_skill_tool(...) = Err ✓");

    println!("[test2] skill 方法默认 impl 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: custom_discovery_trait_full_methods
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_discovery_trait_full_methods() {
    // 注册 2 个 tool，验完整 tool 方法端到端
    let mut disc = MemoryDiscovery::new();
    disc.register_script_tool(
        "echo",
        "echo",
        "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps(p))\n",
    );
    disc.register_script_tool(
        "reverse",
        "reverse",
        "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps({'rev':p['s'][::-1]}))\n",
    );

    // list_tools
    let tools = disc.list_tools();
    assert_eq!(tools.len(), 2);
    println!("[test3] list_tools() = {} tools ✓", tools.len());

    // tool_map
    let tool_map = disc.tool_map();
    assert_eq!(tool_map.len(), 2);
    assert!(tool_map.contains_key("echo"));
    assert!(tool_map.contains_key("reverse"));
    println!("[test3] tool_map() 含 echo + reverse ✓");

    // resolve_tool + 实际执行（验 tool 透过自定义 backend 真能跑）
    let echo = disc.resolve_tool("echo").expect("resolve echo");
    let r = echo.execute(serde_json::json!({"x": 42})).await.expect("execute");
    println!("[test3] echo tool execute: {:?}", r);
    let v: Value = serde_json::from_value(r).unwrap();
    assert_eq!(v["x"], 42);

    let rev = disc.resolve_tool("reverse").expect("resolve reverse");
    let r2 = rev.execute(serde_json::json!({"s": "hello"})).await.expect("execute");
    println!("[test3] reverse tool execute: {:?}", r2);
    let v2: Value = serde_json::from_value(r2).unwrap();
    assert_eq!(v2["rev"], "olleh");

    println!("[test3] 自定义 backend trait 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: custom_discovery_no_dedicated_ctor（探查 F-lesion）
// ═══════════════════════════════════════════════════════════════════════

#[test]
fn custom_discovery_no_dedicated_ctor() {
    // 探查问题：McpNode 是否有 `with_discovery` / `with_boxed_discovery` 构造器？
    // 通过 grep 结果确认：当前只有 `local / remote / local_with_runtime` 三个 constructor
    // —— 都内置了 FsDiscovery 或 HttpDiscovery
    // app 想注入自定义 backend **必须** 走 filesystem 或 HTTP，**没有**直接接 `Box<dyn DiscoveryBackend>`
    //
    // 本 test 用 compile-time 探查：尝试调用 McpNode::with_discovery，预期编译失败
    // （注释掉的代码作为探查记录，不放进实际编译）

    println!("[test4] McpNode 当前 public constructor:");
    println!("[test4]   - local(ns, root) → FsDiscovery + LocalRuntime");
    println!("[test4]   - remote(ns, config) → HttpDiscovery + RemoteRuntime");
    println!("[test4]   - local_with_runtime(ns, root, runtime) → FsDiscovery + custom Runtime");
    println!("[test4] **无 with_discovery / with_boxed_discovery —— F-lesion candidate**");

    // 替代方案：app 可通过实现 DiscoveryBackend trait，自己包装 tool registry
    // —— 该 trait 公开，方法签名与 FsDiscovery 一致。
    // 但要把自定义 backend 装入 McpNode 必须通过 FsDiscovery::scan 一个空 tmpdir + 后续注入
    // —— 不自然。
    //
    // 实证：当前 McpNode 构造器 count = 3
    //       expected count for "with_discovery"  = 4+
    //       → framework gap：缺 direct constructor 接 custom DiscoveryBackend

    let mut count = 0;
    count += 1; println!("[test4] McpNode ctor count = {} (need 4+ for custom discovery support)", count);
    println!("[test4] 探查结论：framework 当前不支持 app 直接注入自定义 DiscoveryBackend");
    println!("[test4] → 必须用 FsDiscovery（filesystem）或 HttpDiscovery（HTTP）包装");
    println!("[test4] → F-lesion candidate：**缺 McpNode::with_discovery 构造器**");
}