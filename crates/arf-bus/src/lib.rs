//! ARF Bus — J-RPC broadcast message bus.
//!
//! All nodes communicate through this bus. It maintains an online node graph,
//! handles node lifecycle (online/offline/heartbeat), and routes messages
//! (broadcast when `to` is empty, directed otherwise).

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
