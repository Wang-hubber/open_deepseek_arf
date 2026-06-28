//! ARF AgentConfig — declarative resource configuration skeleton.
//!
//! AgentConfig is a pure data structure that declares WHAT an agent needs:
//! models, tools, subagents, teammates, allowed paths. It uses only logical
//! names and knows nothing about the Bus, NodeIds, or resource availability.
//! Engine (Phase 4) reads AgentConfig and resolves resources at runtime.

pub fn add(left: u64, right: u64) -> u64 {
    left + right
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_works() {
        assert_eq!(add(2, 2), 4);
    }
}
