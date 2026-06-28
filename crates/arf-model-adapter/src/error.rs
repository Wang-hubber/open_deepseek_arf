//! Error types for ModelAdapter providers.

/// Errors that can occur when calling a model provider's API.
#[derive(Debug)]
pub enum ProviderError {
    /// HTTP transport error (connection refused, timeout, DNS).
    Transport(String),
    /// API returned a non-retryable error (400, 401, etc.).
    Api { status: u16, message: String },
    /// API returned a retryable error (429, 5xx) and retries exhausted.
    RetryExhausted { attempts: u32, last_error: String },
    /// Response parsing failed (unexpected format change).
    Parse(String),
}

impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(msg) => write!(f, "transport error: {msg}"),
            Self::Api { status, message } => write!(f, "API error {status}: {message}"),
            Self::RetryExhausted { attempts, last_error } => {
                write!(f, "retry exhausted after {attempts} attempts: {last_error}")
            }
            Self::Parse(msg) => write!(f, "parse error: {msg}"),
        }
    }
}

impl std::error::Error for ProviderError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_error_display_transport() {
        let e = ProviderError::Transport("connection refused".into());
        assert!(format!("{e}").contains("connection refused"));
    }

    #[test]
    fn provider_error_display_api() {
        let e = ProviderError::Api {
            status: 401,
            message: "Unauthorized".into(),
        };
        assert!(format!("{e}").contains("401"));
        assert!(format!("{e}").contains("Unauthorized"));
    }

    #[test]
    fn provider_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(ProviderError::Parse("test".into()));
    }
}
