//! Message filter tests.
//!
//! `MessageFilter::matches()` is defined in `arf-core`. Tests here
//! verify the full `to_match × to` matrix and type+to_match combinations.

// ═══════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use arf_core::{Message, MessageFilter, NodeId, ToMatch};

    fn msg_to(msg_type: &str, to: Vec<NodeId>) -> Message {
        Message::new(msg_type, NodeId::new("sender"), to, serde_json::json!(null))
    }

    fn msg_action(to: Vec<NodeId>) -> Message {
        msg_to("action", to)
    }

    // ═══════════════════════════════════════════════════════════════
    // types 过滤 (5 tests)
    // ═══════════════════════════════════════════════════════════════

    // [过滤] types=None → 全收
    #[test]
    fn filter_types_none_accepts_all() {
        let filter = MessageFilter {
            types: None,
            to_match: ToMatch::All,
        };
        let me = NodeId::new("me");
        assert!(filter.matches(&msg_action(vec![]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone()]), &me));
        assert!(filter.matches(&msg_action(vec![NodeId::new("other")]), &me));
    }

    // [过滤] type 匹配 → 通过
    #[test]
    fn filter_type_match_passes() {
        let filter = MessageFilter {
            types: Some(vec!["action".into()]),
            to_match: ToMatch::All,
        };
        assert!(filter.matches(&msg_action(vec![]), &NodeId::new("me")));
    }

    // [过滤] type 不匹配 → 拒绝
    #[test]
    fn filter_type_mismatch_rejects() {
        let filter = MessageFilter {
            types: Some(vec!["action".into()]),
            to_match: ToMatch::All,
        };
        let msg = Message::new("other", NodeId::new("a"), vec![], serde_json::json!(null));
        assert!(!filter.matches(&msg, &NodeId::new("me")));
    }

    // [过滤] 多 type 白名单：任一匹配即通过
    #[test]
    fn filter_multi_type_any_match_passes() {
        let filter = MessageFilter {
            types: Some(vec!["action".into(), "query".into(), "response".into()]),
            to_match: ToMatch::All,
        };
        let me = NodeId::new("me");
        assert!(filter.matches(&msg_to("action", vec![]), &me));
        assert!(filter.matches(&msg_to("query", vec![]), &me));
        assert!(filter.matches(&msg_to("response", vec![]), &me));
        assert!(!filter.matches(&msg_to("noise", vec![]), &me));
    }

    // [过滤] types=Some([]) → 空白名单全拒
    #[test]
    fn filter_empty_type_list_rejects_all() {
        let filter = MessageFilter {
            types: Some(vec![]),
            to_match: ToMatch::All,
        };
        assert!(!filter.matches(&msg_action(vec![]), &NodeId::new("me")));
    }

    // ═══════════════════════════════════════════════════════════════
    // ToMatch 过滤 (4 tests, 每个覆盖完整 to 矩阵)
    // ═══════════════════════════════════════════════════════════════

    // [过滤] All — 所有消息通过
    #[test]
    fn to_match_all_receives_everything() {
        let filter = MessageFilter {
            types: None,
            to_match: ToMatch::All,
        };
        let me = NodeId::new("me");
        let other = NodeId::new("other");
        assert!(filter.matches(&msg_action(vec![]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone()]), &me));
        assert!(filter.matches(&msg_action(vec![other.clone()]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone(), other.clone()]), &me));
        assert!(filter.matches(&msg_action(vec![other.clone(), NodeId::new("x")]), &me,));
    }

    // [过滤] BroadcastOnly — 只收广播
    #[test]
    fn to_match_broadcast_only() {
        let filter = MessageFilter {
            types: None,
            to_match: ToMatch::BroadcastOnly,
        };
        let me = NodeId::new("me");
        let other = NodeId::new("other");
        assert!(filter.matches(&msg_action(vec![]), &me));
        assert!(!filter.matches(&msg_action(vec![me.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![other.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![me.clone(), other.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![other.clone(), NodeId::new("x")]), &me,));
    }

    // [过滤] DirectedToMe — 只收定向到自己的
    #[test]
    fn to_match_directed_to_me() {
        let filter = MessageFilter {
            types: None,
            to_match: ToMatch::DirectedToMe,
        };
        let me = NodeId::new("me");
        let other = NodeId::new("other");
        assert!(!filter.matches(&msg_action(vec![]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![other.clone()]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone(), other.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![other.clone(), NodeId::new("x")]), &me,));
    }

    // [过滤] BroadcastAndDirectedToMe — 广播 + 定向到自己的
    #[test]
    fn to_match_broadcast_and_directed() {
        let filter = MessageFilter {
            types: None,
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let me = NodeId::new("me");
        let other = NodeId::new("other");
        assert!(filter.matches(&msg_action(vec![]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![other.clone()]), &me));
        assert!(filter.matches(&msg_action(vec![me.clone(), other.clone()]), &me));
        assert!(!filter.matches(&msg_action(vec![other.clone(), NodeId::new("x")]), &me,));
    }

    // ═══════════════════════════════════════════════════════════════
    // type + ToMatch 组合 (3 tests)
    // ═══════════════════════════════════════════════════════════════

    // [过滤] type ✅ to_match ❌ → 拒绝
    #[test]
    fn filter_type_pass_to_match_reject() {
        let filter = MessageFilter {
            types: Some(vec!["action".into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let me = NodeId::new("me");
        // type "action" matches, but broadcast doesn't pass DirectedToMe
        assert!(!filter.matches(&msg_action(vec![]), &me));
    }

    // [过滤] type ❌ to_match ✅ → 拒绝
    #[test]
    fn filter_type_reject_to_match_pass() {
        let filter = MessageFilter {
            types: Some(vec!["action".into()]),
            to_match: ToMatch::All,
        };
        let me = NodeId::new("me");
        let noise = Message::new(
            "noise",
            NodeId::new("a"),
            vec![me.clone()],
            serde_json::json!(null),
        );
        // to_match passes (All), but "noise" type doesn't match
        assert!(!filter.matches(&noise, &me));
    }

    // [过滤] type ❌ to_match ❌ → 双拒绝
    #[test]
    fn filter_both_reject() {
        let filter = MessageFilter {
            types: Some(vec!["action".into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let me = NodeId::new("me");
        let noise = Message::new("noise", NodeId::new("a"), vec![], serde_json::json!(null));
        assert!(!filter.matches(&noise, &me));
    }
}
