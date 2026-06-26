//! ARF Engine — the ReAct runtime loop.
//!
//! Listens to Bus messages filtered by session_id, calls the model via
//! ModelAdapter, receives action decisions, and emits them back to the Bus.
//! Manages Park/Resume lifecycle and State persistence.

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
