//! ARF State — task lifecycle and message history.
//!
//! Manages `messages` (full message stream) and `tasks` (structured tasks with
//! lifecycle states and bidirectional `blocked_by` / `blocking` locks).

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
