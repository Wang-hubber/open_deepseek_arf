/// Unified error type for MCP node creation and connection.
#[derive(Debug, Clone)]
pub enum McpError {
    /// Resource discovery failed (scan error, no valid tools/skills).
    Discovery { reason: String },
    /// Remote MCP server unreachable (RemoteMcpNode only).
    RemoteUnreachable { url: String, reason: String },
    /// MCP handshake rejected (RemoteMcpNode only).
    RemoteRejected {
        url: String,
        code: i32,
        message: String,
    },
    /// Bus connection failed.
    BusConnect { reason: String },
}

impl std::fmt::Display for McpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Discovery { reason } => write!(f, "discovery error: {reason}"),
            Self::RemoteUnreachable { url, reason } => {
                write!(f, "remote MCP server unreachable ({url}): {reason}")
            }
            Self::RemoteRejected {
                url,
                code,
                message,
            } => {
                write!(
                    f,
                    "remote MCP server rejected handshake ({url}): [{code}] {message}"
                )
            }
            Self::BusConnect { reason } => write!(f, "bus connection failed: {reason}"),
        }
    }
}

impl std::error::Error for McpError {}
