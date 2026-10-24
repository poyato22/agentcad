//! Parent-death, signal and race behaviour of the supervisor, using the stub core.
use mises_app_lib::core::*;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn stub() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/stub_core.sh")
}

fn config(home: &Path, mode: &str) -> SupervisorConfig {
    let env = [
        ("MISES_HOME", home.display().to_string()),
        ("STUB_MODE", mode.to_string()),
        ("STUB_PIDLOG", home.join("pidlog").display().to_string()),
        ("STUB_PIDFILE", home.join("pids").display().to_string()),
    ];
    SupervisorConfig {
        command: CoreCommand {
            program: stub(),
            args: vec![],
            cwd: home.to_path_buf(),
            env: env.into_iter().map(|(k, v)| (k.to_string(), v)).collect(),
        },
        home: home.to_path_buf(),
        time_scale: 1.0,
    }
}

fn alive(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) == 0 }
}

fn pids_in(path: &Path) -> Vec<i32> {
    std::fs::read_to_string(path).unwrap_or_default().lines().filter_map(|l| l.trim().parse().ok()).collect()
}

fn until(secs: u64, f: impl Fn() -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    f()
}

fn ready_count(log: &Mutex<Vec<CoreStatus>>) -> usize {
    log.lock().unwrap().iter().filter(|s| matches!(s, CoreStatus::Ready { .. })).count()
}

fn new_sup(home: &Path) -> (Arc<Supervisor>, Arc<Mutex<Vec<CoreStatus>>>) {
    let log: Arc<Mutex<Vec<CoreStatus>>> = Arc::default();
    let l = log.clone();
    (Arc::new(Supervisor::start_with(config(home, "fork"), Arc::new(move |s| l.lock().unwrap().push(s.clone())))), log)
}

/// Not a real test: the body of the child process used by the parent-death tests below.
#[test]
fn supervise_child_entry() {
    let Some(home) = std::env::var_os("SUP_CHILD_HOME").map(PathBuf::from) else { return };
    let (sup, log) = new_sup(&home);
    install_signal_stop(sup.clone());
    assert!(until(15, || ready_count(&log) > 0));
    std::fs::write(home.join("child-ready"), "").unwrap();
    std::thread::sleep(Duration::from_secs(60));
}

fn run_child_and_kill(sig: i32) {
    let home = tempfile::tempdir().unwrap();
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "supervise_child_entry", "--nocapture", "--test-threads=1"])
        .env("SUP_CHILD_HOME", home.path())
        .spawn()
        .unwrap();
    assert!(until(20, || home.path().join("child-ready").exists()), "child never became ready");
    let pids = pids_in(&home.path().join("pids"));
    assert_eq!(pids.len(), 2);
    assert!(pids.iter().all(|p| alive(*p)));
    unsafe { libc::kill(child.id() as i32, sig) };
    let _ = child.wait();
    assert!(until(5, || pids.iter().all(|p| !alive(*p))), "core or its group outlived the app (signal {sig})");
}

/// Not a real test: the child body for the uv-path parent-death test below (the REAL core,
/// started with `uv run`, exactly as core_command builds it when the venv entry point is absent).
#[test]
fn supervise_real_uv_child_entry() {
    let Some(home) = std::env::var_os("SUP_UV_HOME").map(PathBuf::from) else { return };
    let uv = PathBuf::from(std::env::var_os("SUP_UV").unwrap());
    let mut command = uv_core_command(&uv, &core_dir());
    command.env.push(("MISES_HOME".into(), home.display().to_string()));
    let log: Arc<Mutex<Vec<CoreStatus>>> = Arc::default();
    let l = log.clone();
    let _sup = Supervisor::start_with(SupervisorConfig { command, home: home.clone(), time_scale: 1.0 },
        Arc::new(move |s| l.lock().unwrap().push(s.clone())));
    assert!(until(90, || ready_count(&log) > 0), "real core never became ready: {:?}", log.lock().unwrap());
    std::fs::write(home.join("child-ready"), "").unwrap();
    std::thread::sleep(Duration::from_secs(120));
}

/// M8: does a SIGKILLed app's stdin EOF reach the Python core when it runs under `uv run`
/// (uv stays as the parent)? It does: uv only holds the pipe's read end, and EOF comes once
/// every write end (the app's) is closed. Skipped when uv isn't installed.
#[test]
fn sigkill_of_the_app_on_the_uv_path_still_ends_the_core_by_stdin_eof() {
    let Some(uv) = find_uv_with(std::env::var_os("MISES_UV").map(PathBuf::from), &mises_app_lib::platform::uv_candidates(),
        std::env::var_os("PATH")) else {
        eprintln!("skipped: uv not found");
        return;
    };
    let home = tempfile::tempdir().unwrap();
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "supervise_real_uv_child_entry", "--nocapture", "--test-threads=1"])
        .env("SUP_UV_HOME", home.path())
        .env("SUP_UV", &uv)
        .spawn()
        .unwrap();
    let ready = until(120, || home.path().join("child-ready").exists());
    let info: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(home.path().join("server.json")).unwrap_or_else(|_| "{}".into())).unwrap();
    let core_pid = info["pid"].as_i64().unwrap_or(0) as i32;
    let alive_before = core_pid > 0 && alive(core_pid);
    // the core really runs under uv: its parent is the uv process, not the app
    let parent = Command::new("ps").args(["-o", "ppid=,comm=", "-p", &core_pid.to_string()]).output().ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default();
    let ppid: i32 = parent.split_whitespace().next().and_then(|x| x.parse().ok()).unwrap_or(0);
    let pcomm = Command::new("ps").args(["-o", "comm=", "-p", &ppid.to_string()]).output().ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default();
    unsafe { libc::kill(child.id() as i32, libc::SIGKILL) };
    let _ = child.wait();
    assert!(ready, "child never became ready");
    assert!(alive_before, "core {core_pid} not running before the kill");
    assert!(pcomm.ends_with("uv"), "core parent is {pcomm:?}, not uv");
    let gone = until(20, || !alive(core_pid));
    if !gone {
        unsafe { libc::kill(core_pid, libc::SIGKILL) }; // never leave an orphan behind
    }
    assert!(gone, "the core under `uv run` outlived a SIGKILLed app (no stdin EOF)");
}

#[test]
fn sigkill_of_the_app_takes_the_core_and_its_group_down() {
    run_child_and_kill(libc::SIGKILL);
}

#[test]
fn sigterm_of_the_app_stops_the_core() {
    run_child_and_kill(libc::SIGTERM);
}

#[test]
fn dropping_the_supervisor_stops_an_owned_core() {
    let home = tempfile::tempdir().unwrap();
    let (sup, log) = new_sup(home.path());
    assert!(until(15, || ready_count(&log) > 0));
    let pids = pids_in(&home.path().join("pids"));
    drop(sup);
    assert!(until(5, || pids.iter().all(|p| !alive(*p))));
}

#[test]
fn two_concurrent_restarts_leave_exactly_one_core() {
    let home = tempfile::tempdir().unwrap();
    let (sup, log) = new_sup(home.path());
    assert!(until(15, || ready_count(&log) > 0));
    let (a, b) = (sup.clone(), sup.clone());
    let ts = [std::thread::spawn(move || a.restart()), std::thread::spawn(move || b.restart())];
    ts.into_iter().for_each(|t| t.join().unwrap());
    assert!(until(20, || ready_count(&log) >= 2));
    std::thread::sleep(Duration::from_millis(500));
    let live = pids_in(&home.path().join("pidlog")).into_iter().filter(|p| alive(*p)).count();
    assert_eq!(live, 1, "expected exactly one live core");
    sup.stop();
    assert!(until(5, || pids_in(&home.path().join("pidlog")).iter().all(|p| !alive(*p))));
}

#[test]
fn restart_racing_with_shutdown_leaves_no_core() {
    for _ in 0..3 {
        let home = tempfile::tempdir().unwrap();
        let (sup, log) = new_sup(home.path());
        assert!(until(15, || ready_count(&log) > 0));
        let (a, b) = (sup.clone(), sup.clone());
        let r = std::thread::spawn(move || a.restart());
        let s = std::thread::spawn(move || b.stop());
        r.join().unwrap();
        s.join().unwrap();
        assert!(until(5, || pids_in(&home.path().join("pidlog")).iter().all(|p| !alive(*p))), "core survived shutdown");
        sup.restart(); // after shutdown a restart is a no-op
        std::thread::sleep(Duration::from_millis(300));
        assert!(pids_in(&home.path().join("pidlog")).iter().all(|p| !alive(*p)));
    }
}

#[test]
fn debug_output_never_contains_the_token() {
    let info = CoreInfo { base_url: "b".into(), ws_url: "w".into(), token: "sekrit".into(), owned: true, pid: 7 };
    let st = CoreStatus::Ready { info: info.clone() };
    let si = mises_app_lib::server_info::ServerInfo { port: 1, token: "sekrit".into(), pid: 2 };
    for d in [format!("{info:?}"), format!("{st:?}"), format!("{si:?}"), format!("{info:#?}")] {
        assert!(!d.contains("sekrit"), "{d}");
    }
}
