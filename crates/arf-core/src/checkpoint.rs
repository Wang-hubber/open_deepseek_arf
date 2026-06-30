//! Checkpoint — fixed injection points in the ReAct loop (Phase 6 §1.5).

use serde::{Deserialize, Serialize};

use crate::{ActionMessage, State};

/// Where a rule may fire (5 invariant positions).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Checkpoint {
    /// Before Engine publishes `ModelCall` to Bus.
    BeforeModelCall,
    /// After ModelAdapter's `model_response` arrives and Engine appends the assistant message.
    AfterModelCall,
    /// Before Engine publishes `ToolExec` to Bus.
    BeforeToolExec,
    /// After ToolNode's `tool_result` arrives and Engine appends the tool message.
    AfterToolExec,
    /// Round boundary, before Engine returns final output to App.
    RoundEnd,
}

/// Checkpoint rule: 4-tuple (name, trigger, when, build). Phase 6 §2.P3.
///
/// **No route** — routes are single-sourced in `AgentConfig.routes`. The Engine
/// dispatches the message returned by `build(state)` via the route registered
/// for that message's `msg_type`.
///
/// Closures are stored as `Box<dyn Fn(...)>` with HRTB (`for<'a>`) lifetimes so
/// the closures can borrow `&State` of any lifetime. (No `Clone` derive:
/// trait objects for `dyn Fn` are not Clone. If clone is needed, wrap in `Rc<CheckpointRule>`.)
pub struct CheckpointRule {
    pub name: String,
    pub trigger: Checkpoint,
    /// Returns true if the rule should fire at this checkpoint.
    pub when: Box<dyn for<'a> Fn(&'a State) -> bool + Send + Sync>,
    /// Construct the side-effect message from state.
    pub build: Box<dyn for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync>,
}

impl CheckpointRule {
    /// Construct a `CheckpointRule` with all 4 fields. Closures must satisfy
    /// HRTB (`for<'a> Fn(&'a State) -> ...`).
    pub fn new<W, B>(name: impl Into<String>, trigger: Checkpoint, when: W, build: B) -> Self
    where
        W: for<'a> Fn(&'a State) -> bool + Send + Sync + 'static,
        B: for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync + 'static,
    {
        Self {
            name: name.into(),
            trigger,
            when: Box::new(when),
            build: Box::new(build),
        }
    }

    /// Evaluate the `when` predicate against the given state.
    pub fn fires(&self, state: &State) -> bool {
        (self.when)(state)
    }

    /// Construct the side-effect message (call only if `fires` returned true).
    pub fn build_msg(&self, state: &State) -> Box<dyn ActionMessage> {
        (self.build)(state)
    }
}
