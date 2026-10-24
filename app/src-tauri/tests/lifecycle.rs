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
    let info = CoreInfo { base_url: "b".into(), ws_url: "w".into(), token: "sekrit".into(), owned: true };
    let st = CoreStatus::Ready { info: info.clone() };
    let si = mises_app_lib::server_info::ServerInfo { port: 1, token: "sekrit".into(), pid: 2 };
    for d in [format!("{info:?}"), format!("{st:?}"), format!("{si:?}"), format!("{info:#?}")] {
        assert!(!d.contains("sekrit"), "{d}");
    }
}
