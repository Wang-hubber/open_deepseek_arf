//! PyO3 bindings for ARF V1.x core.
//!
//! Exposes Bus, State, Engine, and Agent types to Python.

use pyo3::prelude::*;

/// A Python module implemented in Rust.
#[pymodule]
fn _arf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "1.0.0-alpha.0")?;
    Ok(())
}
