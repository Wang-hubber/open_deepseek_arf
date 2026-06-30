# 任务 6.0.3：Message.from_bus 字段

> Phase 6 — Multi-Bus 基础设施（§9.A）第三项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §2.P7 / §7.2
> 前置：`task-6.0.1-node-trait` ✅、`task-6.0.2-nodehandle-multi-bus` ✅

## 设计思路

在 `Message` 上加 `from_bus: Option<BusId>` 字段，让接收方知道消息来自哪条 Bus（facade 转发必需）。所有 Bus 端 broadcast 站点必须在发送前把 `from_bus` 填为本 Bus 的 `id`。

**关键设计**：
- `#[serde(default)]` 兼容历史数据（旧 JSON 消息无该字段）
- `Message::new(...)` 默认 `from_bus: None`（公开 API 不破坏）
- arf-bus 内部用 `Message::with_from_bus(...)` 构造 outgoing messages

## 代码实现

### `crates/arf-core/src/lib.rs`：`Message` 加字段

```rust
pub struct Message {
    pub id: Uuid,
    pub msg_type: String,
    pub from: NodeId,
    pub to: Vec<NodeId>,
    pub payload: serde_json::Value,
    /// Source Bus identifier (Phase 6 multi-Bus). `None` for messages that
    /// didn't pass through a Bus (e.g., direct construction in tests).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from_bus: Option<crate::node::BusId>,
    pub timestamp: u64,
}

impl Message {
    pub fn new(
        msg_type: impl Into<String>,
        from: NodeId,
        to: Vec<NodeId>,
        payload: serde_json::Value,
    ) -> Self {
        Self {
            id: Uuid::new_v4(),
            msg_type: msg_type.into(),
            from,
            to,
            payload,
            from_bus: None,
            timestamp: current_timestamp_ms(),
        }
    }

    /// Construct a Message with an explicit `from_bus` stamp.
    /// Used by Bus-internal broadcast sites.
    pub fn with_from_bus(
        msg_type: impl Into<String>,
        from: NodeId,
        to: Vec<NodeId>,
        payload: serde_json::Value,
        from_bus: crate::node::BusId,
    ) -> Self {
        let mut m = Self::new(msg_type, from, to, payload);
        m.from_bus = Some(from_bus);
        m
    }
}
```

逐行：
- `#[serde(default)]`：旧 JSON 数据无此字段时不报错（兼容 Phase 1 历史数据）
- `skip_serializing_if = "Option::is_none"`：None 时不写该字段，序列化输出干净
- `with_from_bus()` 是内部 constructor；公开 API（用户写代码用 `Message::new`）不变

### `crates/arf-bus/src/lib.rs`：所有 broadcast 站点填 from_bus

5 处修改：

```rust
// 1. handle_connect — node_online
let online_msg = Message::with_from_bus(
    "node_online", node_id, vec![],
    serde_json::to_value(&info).unwrap_or_default(),
    self.id,  // BusId
);

// 2. handle_disconnect — node_offline (注意：原代码用 node_id.clone() 的 from,这里保持)
let offline_msg = Message::with_from_bus(
    "node_offline", node_id.clone(), vec![],
    serde_json::json!({}),
    self.id,
);

// 3. heartbeat.rs::handle_heartbeat_tick — node_offline (from=offline_node_id), heartbeat_request (from="bus")
let offline_msg = Message::with_from_bus(
    "node_offline", offline_node_id.clone(), vec![],
    ...,  // full NodeInfo payload
    self.id,  // 这里 self 是 &Bus
);
let hb_msg = Message::with_from_bus(
    "heartbeat_request", NodeId::new("bus"), vec![],
    serde_json::json!(null),
    self.id,
);

// 4. run_message_loop Send 分支 — 用户消息
let msg = Message::with_from_bus(
    msg_type, self.info.node_id.clone(), to, payload,
    sub.bus_id,  // 在 send_via 路径上；普通 send 走 primary_bus_id
);
// 或在 send / send_via 入口处构 Message 时已带 from_bus
```

### `connection.rs`：`NodeHandle::send` 自动应用 from_bus

```rust
pub async fn send(&self, msg_type: &str, to: Vec<NodeId>, payload: Value) -> Result<SendReceipt, SendError> {
    self.send_via(self.primary_bus_id, msg_type, to, payload).await
}

pub async fn send_via(&self, bus_id: BusId, msg_type: &str, to: Vec<NodeId>, payload: Value) -> Result<SendReceipt, SendError> {
    let sub = self.subscriptions.iter().find(|s| s.bus_id == bus_id).ok_or(SendError::NoSuchBus(bus_id))?;
    let msg = Message::with_from_bus(msg_type, self.info.node_id.clone(), to, payload, bus_id);
    // ... rest same ...
}
```

逐行：
- 用户 `NodeHandle::send` 调用链完全无感（接口不变）
- 内部 Message 一律带 from_bus 戳

## 测试

### `crates/arf-core/src/lib.rs` 加 5 个测试

```rust
// [序列化] Message 含 from_bus 字段能 round-trip
#[test]
fn message_with_from_bus_serialization_roundtrip() {
    let m = Message::with_from_bus("x", NodeId::new("a"), vec![], json!(null), BusId::new());
    let json = serde_json::to_string(&m).unwrap();
    let back: Message = serde_json::from_str(&json).unwrap();
    assert_eq!(back.from_bus, m.from_bus);
}

// [序列化] Message 无 from_bus 字段时反序列化得到 None（兼容旧数据）
#[test]
fn message_without_from_bus_deserializes_as_none() {
    // 历史数据格式：无 from_bus 字段
    let json = r#"{"id":"00000000-0000-0000-0000-000000000001","msg_type":"x","from":"a","to":[],"payload":null,"timestamp":0}"#;
    let m: Message = serde_json::from_str(json).unwrap();
    assert!(m.from_bus.is_none());
}

// [序列化] from_bus: None 时序列化输出不应包含字段
#[test]
fn message_without_from_bus_omits_field() {
    let m = Message::new("x", NodeId::new("a"), vec![], json!(null));
    let json = serde_json::to_string(&m).unwrap();
    assert!(!json.contains("from_bus"));
}

// [构造] Message::new 默认 from_bus = None
#[test]
fn message_new_defaults_from_bus_to_none() {
    let m = Message::new("x", NodeId::new("a"), vec![], json!(null));
    assert!(m.from_bus.is_none());
}

// [构造] Message::with_from_bus 设置 from_bus
#[test]
fn message_with_from_bus_sets_field() {
    let bid = BusId::new();
    let m = Message::with_from_bus("x", NodeId::new("a"), vec![], json!(null), bid);
    assert_eq!(m.from_bus, Some(bid));
}
```

### `crates/arf-bus` 加 3 个测试

```rust
// [广播] Bus.connect 后 node_online.from_bus 指向该 Bus
#[tokio::test]
async fn node_online_stamped_with_from_bus() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let _handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();
    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.from_bus, Some(bus.id));
    bus.shutdown().await;
}

// [广播] Bus.disconnect 后 node_offline.from_bus 仍指向该 Bus
#[tokio::test]
async fn node_offline_stamped_with_from_bus() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();
    let _ = rx.recv().await.unwrap();
    handle.disconnect().await;
    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.msg_type, "node_offline");
    assert_eq!(msg.from_bus, Some(bus.id));
    bus.shutdown().await;
}

// [广播] NodeHandle.send 自动给消息加 from_bus（不影响 send_via）
#[tokio::test]
async fn node_handle_send_stamps_from_bus() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();
    let _ = rx.recv().await.unwrap();
    sender.send("x", vec![], json!(null)).await.unwrap();
    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.msg_type, "x");
    assert_eq!(msg.from_bus, Some(bus.id));
    sender.disconnect().await;
    bus.shutdown().await;
}
```

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 | 覆盖角度 |
|------|--------|---------|
| `Message` (arf-core) | 5 | `[序列化][兼容][构造]` |
| arf-bus 广播链路 | 3 | `[广播][多Bus]` |
| **合计新增** | **8** | |

---

## 实现后实际测试发现

### 与初稿的差异

1. **`Bus::send` 也需要 stamp from_bus**——初稿只提到 broadcast 站点，但用户经 `Bus::send` 直接送的消息同样需要戳记。否则测试 `user_send_stamps_from_bus` 会失败：msg 经 message loop 转发后仍是 `from_bus=None`。
   修复：`Bus::send(mut msg)` 入参改 `mut`，函数体首行 `msg.from_bus = Some(self.id)`。

2. **`handle_connect` / `handle_disconnect` / `handle_heartbeat_tick` 都需要加 `bus_id: BusId` 形参**——这三个 free function 之前不持 `self`，改成接受 `bus_id: BusId` 参数更直接。`run_message_loop` 闭包捕获 `bus_id` 然后传下去。

3. **`run_message_loop` 捕获 BusId 用 shadowing 而非 `move`**——直接 `async move { ... id ... }` 会消费外部 `id` 变量，导致紧跟的 `Self { id, ... }` 无法再次构造 BusId。修复：
   ```rust
   let bus_id = BusId(Uuid::new_v4());
   let loop_handle = tokio::spawn({
       let bus_id = bus_id;
       async move { run_message_loop(..., bus_id).await; }
   });
   Self { id: bus_id, ... }
   ```
   即 spawn 块内 shadowing（捕获 by-move），然后 `Self` 仍持有原来的 `bus_id`。

### 实际测试结果

```
cargo test --workspace
... 
test result: ok. 134 passed (arf-core: 129 + 5 new from_bus tests)
test result: ok.  85 passed (arf-bus lib: 82 + 3 new stamping tests)
test result: ok.  12 passed (arf-bus integration)
... (其他 crate 全部 OK)
0 FAILED
```

### 兼容性验证

旧 JSON 数据（无 `from_bus` 字段）：
```json
{"id":"...","msg_type":"x","from":"a","to":[],"payload":null,"timestamp":0}
```
反序列化为 `from_bus: None` —— 通过 `#[serde(default)]` 实现。

新 JSON 数据（含 `from_bus` 字段）：
```json
{"id":"...","msg_type":"x","from":"a","to":[],"payload":null,"from_bus":"bus:<uuid>","timestamp":0}
```
反序列化得到 `Some(BusId(uuid))`。

输出格式（`skip_serializing_if = "Option::is_none"`）保证 None 时不写入，保持线协议干净。