//! Process-level dedup cache for inbound reply correlation_ids.
//!
//! Spec §4.1. Prevents self-resend of an outbound message (where sender == receiver)
//! from triggering duplicate processing. **Process-level only** — cross-restart
//! dedup is application responsibility (see spec §4.4).

use lru::LruCache;
use std::num::NonZeroUsize;
use std::sync::{Arc, Mutex};
use uuid::Uuid;

/// LRU cache of correlation_ids that have been seen on the inbound reply path.
/// Capacity-bounded; oldest evicted.
#[derive(Clone)]
pub struct InboundDedupCache {
    inner: Arc<Mutex<LruCache<Uuid, ()>>>,
}

impl InboundDedupCache {
    pub fn new(capacity: usize) -> Self {
        let cap = NonZeroUsize::new(capacity.max(1)).unwrap();
        Self { inner: Arc::new(Mutex::new(LruCache::new(cap))) }
    }

    /// Returns `true` if `cid` was already in the cache (i.e., duplicate).
    /// Side effect: records `cid` on first sight.
    pub fn check_and_record(&self, cid: &Uuid) -> bool {
        let mut cache = self.inner.lock().expect("InboundDedupCache poisoned");
        if cache.contains(cid) {
            true
        } else {
            cache.put(*cid, ());
            false
        }
    }

    /// Number of entries currently cached (for tests / observability).
    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.inner.lock().unwrap().len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // [方法] 首次 check_and_record 返回 false，第二次返回 true
    #[test]
    fn dedup_first_miss_second_hit() {
        let cache = InboundDedupCache::new(16);
        let cid = Uuid::new_v4();
        assert!(!cache.check_and_record(&cid), "first call: not a dup");
        assert!(cache.check_and_record(&cid), "second call: dup");
    }

    // [边界] 不同 cid 互不影响
    #[test]
    fn dedup_distinct_cids_independent() {
        let cache = InboundDedupCache::new(16);
        let a = Uuid::new_v4();
        let b = Uuid::new_v4();
        assert!(!cache.check_and_record(&a));
        assert!(!cache.check_and_record(&b));
    }

    // [边界] 容量超限后老 cid 被淘汰，重新出现算 miss
    #[test]
    fn dedup_lru_evicts_at_capacity() {
        let cache = InboundDedupCache::new(2);
        let a = Uuid::new_v4();
        let b = Uuid::new_v4();
        let c = Uuid::new_v4();
        cache.check_and_record(&a);
        cache.check_and_record(&b);
        cache.check_and_record(&c);  // evicts a
        assert_eq!(cache.len(), 2);
        assert!(!cache.check_and_record(&a), "a was evicted → miss");
        assert!(cache.check_and_record(&a), "second time → hit");
    }

    // [构造] 容量 0 不会 panic（被钳到 1）
    #[test]
    fn dedup_capacity_zero_clamped() {
        let cache = InboundDedupCache::new(0);
        let cid = Uuid::new_v4();
        assert!(!cache.check_and_record(&cid));
        assert!(cache.check_and_record(&cid));
    }

    // [类型] Clone 后两个 cache 共享状态（Arc 共享）
    #[test]
    fn dedup_clone_shares_state() {
        let c1 = InboundDedupCache::new(8);
        let c2 = c1.clone();
        let cid = Uuid::new_v4();
        assert!(!c1.check_and_record(&cid));
        assert!(c2.check_and_record(&cid), "clone sees the recorded cid");
    }
}