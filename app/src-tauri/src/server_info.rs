use serde::Deserialize;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

#[derive(Deserialize, Debug, Clone, PartialEq)]
pub struct ServerInfo {
    pub port: u16,
    pub token: String,
    pub pid: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InfoError {
    Missing,
    TooOld,
    BadJson,
    BadPerms,
}

/// `$MISES_HOME` or the platform default.
pub fn mises_home() -> PathBuf {
    std::env::var_os("MISES_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(crate::platform::default_data_home)
}

/// Reads server.json. Rejects group/world-readable files (it holds the token) and, when
/// `newer_than` is given, files last written before that instant (stale from an older core).
pub fn read_server_info(path: &Path, newer_than: Option<SystemTime>) -> Result<ServerInfo, InfoError> {
    let meta = std::fs::metadata(path).map_err(|_| InfoError::Missing)?;
    if meta.permissions().mode() & 0o077 != 0 {
        return Err(InfoError::BadPerms);
    }
    if let Some(t) = newer_than {
        if meta.modified().map_err(|_| InfoError::TooOld)? < t {
            return Err(InfoError::TooOld);
        }
    }
    let text = std::fs::read_to_string(path).map_err(|_| InfoError::Missing)?;
    serde_json::from_str(&text).map_err(|_| InfoError::BadJson)
}

/// Authenticated `GET /health` on loopback → 200 `{"ok":true}`.
pub fn health_ok(info: &ServerInfo, timeout: Duration) -> bool {
    let url = format!("http://127.0.0.1:{}/health", info.port);
    let Ok(resp) = ureq::get(&url)
        .timeout(timeout)
        .set("Authorization", &format!("Bearer {}", info.token))
        .call()
    else {
        return false;
    };
    resp.status() == 200
        && resp
            .into_string()
            .ok()
            .and_then(|b| serde_json::from_str::<serde_json::Value>(&b).ok())
            .is_some_and(|v| v["ok"] == true)
}
