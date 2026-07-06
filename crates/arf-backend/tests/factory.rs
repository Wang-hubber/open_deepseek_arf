//! Tests for `BackendFactory` + `_resolve_backend` (Phase 11 / 11.5).

use std::sync::{Arc, Mutex};

use arf_backend::{
    resolve_backend, BackendError, BackendFactory, BackendPath, BackendProtocol, BackendSpec,
    IntoBackend, StateBackend,
};

// Note: env var tests are isolated via a Mutex to prevent parallel
// test runs from clobbering each other.
static ENV_LOCK: Mutex<()> = Mutex::new(());

// Rust 2024 marks std::env::set_var/remove_var as unsafe. Wrap them
// in our own unsafe helpers so tests can use a cleaner call site.
unsafe fn env_set(key: &str, value: &str) {
    unsafe { std::env::set_var(key, value) }
}

unsafe fn env_remove(key: &str) {
    unsafe { std::env::remove_var(key) }
}

// ---------------------------------------------------------------------------
// [构造] BackendSpec parsing
// ---------------------------------------------------------------------------

#[test]
fn spec_parse_state() {
    assert_eq!(BackendSpec::parse("state"), BackendSpec::State);
}

#[test]
fn spec_parse_filesystem_with_path() {
    assert_eq!(
        BackendSpec::parse("filesystem:/tmp/foo"),
        BackendSpec::Filesystem("/tmp/foo".into())
    );
}

#[test]
fn spec_parse_filesystem_without_path() {
    assert_eq!(
        BackendSpec::parse("filesystem"),
        BackendSpec::Filesystem("".into())
    );
}

#[test]
fn spec_parse_unknown() {
    assert_eq!(
        BackendSpec::parse("redis"),
        BackendSpec::Unknown("redis".into())
    );
}

#[test]
fn spec_parse_unknown_with_colon() {
    assert_eq!(
        BackendSpec::parse("weird:cfg"),
        BackendSpec::Unknown("weird".into())
    );
}

// ---------------------------------------------------------------------------
// [构造] BackendFactory::from_spec
// ---------------------------------------------------------------------------

#[tokio::test]
async fn from_spec_state() {
    let backend = BackendFactory::from_spec(BackendSpec::State).unwrap();
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "data").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "data");
}

#[tokio::test]
async fn from_spec_filesystem() {
    let dir = tempfile::tempdir().unwrap();
    let spec = BackendSpec::Filesystem(dir.path().to_string_lossy().into());
    let backend = BackendFactory::from_spec(spec).unwrap();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "fs-data").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "fs-data");
}

#[tokio::test]
async fn from_spec_unknown_returns_err() {
    let spec = BackendSpec::Unknown("redis".into());
    let result = BackendFactory::from_spec(spec);
    match result {
        Ok(_) => panic!("expected error"),
        Err(BackendError::Other(msg)) => assert!(msg.contains("redis")),
        Err(other) => panic!("expected Other, got {other:?}"),
    }
}

#[tokio::test]
async fn from_spec_filesystem_with_invalid_root() {
    let spec = BackendSpec::Filesystem("/this/does/not/exist".into());
    let result = BackendFactory::from_spec(spec);
    match result {
        Ok(_) => panic!("expected error"),
        Err(BackendError::Io(_)) => {}
        Err(other) => panic!("expected Io, got {other:?}"),
    }
}

#[tokio::test]
async fn default_backend_is_state() {
    let backend = BackendFactory::default_backend();
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "default").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "default");
}

// ---------------------------------------------------------------------------
// [env] from_env with ARF_BACKEND
// ---------------------------------------------------------------------------

#[tokio::test]
async fn from_env_unset_returns_state() {
    let _guard = ENV_LOCK.lock().unwrap();
    unsafe { env_remove("ARF_BACKEND") };
    let backend = BackendFactory::from_env().unwrap();
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "from-env").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "from-env");
}

#[tokio::test]
async fn from_env_explicit_state() {
    let _guard = ENV_LOCK.lock().unwrap();
    unsafe { env_set("ARF_BACKEND", "state") };
    let backend = BackendFactory::from_env().unwrap();
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "explicit-state").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "explicit-state");
    unsafe { env_remove("ARF_BACKEND") };
}

#[tokio::test]
async fn from_env_filesystem() {
    let dir = tempfile::tempdir().unwrap();
    let _guard = ENV_LOCK.lock().unwrap();
    unsafe {
        env_set(
            "ARF_BACKEND",
            &format!("filesystem:{}", dir.path().to_string_lossy()),
        )
    };
    let backend = BackendFactory::from_env().unwrap();
    let path = BackendPath::new("/foo.txt").unwrap();
    backend.write(&path, "env-fs").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "env-fs");
    unsafe { env_remove("ARF_BACKEND") };
}

#[tokio::test]
async fn from_env_unknown_returns_err() {
    let _guard = ENV_LOCK.lock().unwrap();
    unsafe { env_set("ARF_BACKEND", "redis") };
    let result = BackendFactory::from_env();
    match result {
        Ok(_) => panic!("expected error"),
        Err(BackendError::Other(_)) => {}
        Err(other) => panic!("expected Other, got {other:?}"),
    }
    unsafe { env_remove("ARF_BACKEND") };
}

// ---------------------------------------------------------------------------
// [resolve] resolve_backend with various inputs
// ---------------------------------------------------------------------------

#[tokio::test]
async fn resolve_backend_with_arc() {
    let backend: Arc<dyn BackendProtocol> = Arc::new(StateBackend::new());
    let resolved = resolve_backend(backend.clone());
    assert!(Arc::ptr_eq(&backend, &resolved));
}

#[tokio::test]
async fn resolve_backend_with_concrete_state() {
    let backend = StateBackend::new();
    let resolved = resolve_backend(backend);
    let path = BackendPath::new("/foo").unwrap();
    resolved.write(&path, "from-concrete").await.unwrap();
    let r = resolved.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "from-concrete");
}

#[tokio::test]
async fn resolve_backend_with_factory_fn() {
    let factory: arf_backend::BackendFactoryFn =
        Box::new(|| Arc::new(StateBackend::new()) as Arc<dyn BackendProtocol>);
    let backend = resolve_backend(factory);
    let path = BackendPath::new("/foo").unwrap();
    backend.write(&path, "from-factory").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "from-factory");
}

#[tokio::test]
async fn resolve_backend_with_box() {
    let backend: Box<dyn BackendProtocol> = Box::new(StateBackend::new());
    let resolved = resolve_backend(backend);
    let path = BackendPath::new("/foo").unwrap();
    resolved.write(&path, "from-box").await.unwrap();
    let r = resolved.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "from-box");
}

// ---------------------------------------------------------------------------
// [trait] IntoBackend blanket impls
// ---------------------------------------------------------------------------

#[tokio::test]
async fn into_backend_trait_for_arc() {
    let backend: Arc<dyn BackendProtocol> = Arc::new(StateBackend::new());
    let resolved: Arc<dyn BackendProtocol> = backend.into_backend();
    let path = BackendPath::new("/foo").unwrap();
    resolved.write(&path, "via-trait").await.unwrap();
    let r = resolved.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "via-trait");
}

// ---------------------------------------------------------------------------
// [兼容] App-style usage
// ---------------------------------------------------------------------------

#[tokio::test]
async fn app_style_usage() {
    let _guard = ENV_LOCK.lock().unwrap();
    unsafe { env_set("ARF_BACKEND", "state") };
    let backend: Arc<dyn BackendProtocol> = BackendFactory::from_env().unwrap();
    let path = BackendPath::new("/app-file").unwrap();
    backend.write(&path, "app-style").await.unwrap();
    let r = backend.read(&path, 0, 0).await.unwrap();
    assert_eq!(r.file_data.unwrap().content, "app-style");
    unsafe { env_remove("ARF_BACKEND") };
}