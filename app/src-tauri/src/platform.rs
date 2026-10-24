//! Everything OS-specific lives here so a Windows port only touches this file.
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

/// Default data home: `~/Library/Application Support/Mises` (matches core `platform.data_home`).
pub fn default_data_home() -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    home.join("Library/Application Support/Mises")
}

/// Fixed places `uv` may live. Finder-launched apps have PATH=/usr/bin:/bin.
pub fn uv_candidates() -> Vec<PathBuf> {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    vec![
        PathBuf::from("/opt/homebrew/bin/uv"),
        PathBuf::from("/usr/local/bin/uv"),
        home.join(".local/bin/uv"),
        home.join(".cargo/bin/uv"),
    ]
}

/// Run the child as leader of its own process group so the whole tree can be signalled.
pub fn own_process_group(cmd: &mut Command) {
    cmd.process_group(0);
}

/// Signal a process group. Returns false if the group no longer exists.
pub fn signal_group(pgid: u32, kill: bool) -> bool {
    let sig = if kill { libc::SIGKILL } else { libc::SIGTERM };
    // SAFETY: plain syscall on an integer pgid we created via process_group(0).
    unsafe { libc::killpg(pgid as i32, sig) == 0 }
}
