use std::sync::atomic::AtomicU64;

pub(crate) static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

mod config_tests;
mod discovery_tests;
mod executor_tests;
mod script_tests;
mod skill_tests;
mod tool_tests;
mod types_tests;
