//! ARF Agent — declarative configuration and passive state machine.
//!
//! The Agent is a config + state machine skeleton. It does not know about
//! the Bus, MCP, or other Agents. The Engine feeds it messages and it
//! produces action decisions.

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
