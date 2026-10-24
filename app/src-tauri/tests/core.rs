use mises_app_lib::core::*;
use mises_app_lib::server_info::*;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

static ENV: Mutex<()> = Mutex::new(());

fn stub() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/stub_core.sh")
}

fn cfg(home: &Path, mode: &str, extra: &[(&str, String)], scale: f64) -> SupervisorConfig {
    let mut env = vec![
        ("MISES_HOME".to_string(), home.display().to_string()),
        ("STUB_MODE".to_string(), mode.to_string()),
    ];
    env.extend(extra.iter().map(|(k, v)| (k.to_string(), v.clone())));
    SupervisorConfig {
        command: CoreCommand { program: stub(), args: vec![], cwd: home.to_path_buf(), env },
        home: home.to_path_buf(),
        time_scale: scale,
    }
}

type Log = Arc<Mutex<Vec<CoreStatus>>>;

fn start(c: SupervisorConfig) -> (Supervisor, Log) {
    let log: Log = Arc::default();
    let l = log.clone();
    (Supervisor::start_with(c, Arc::new(move |s| l.lock().unwrap().push(s.clone()))), log)
}

fn wait_for(log: &Log, f: impl Fn(&CoreStatus) -> bool, secs: u64) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if log.lock().unwrap().iter().any(&f) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}

fn alive(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) == 0 }
}

fn is_ready(s: &CoreStatus) -> bool {
    matches!(s, CoreStatus::Ready { .. })
}

fn last_ready_info(log: &Log) -> CoreInfo {
    let g = log.lock().unwrap();
    let Some(CoreStatus::Ready { info }) = g.iter().rev().find(|s| is_ready(s)) else { panic!("never ready") };
    info.clone()
}

#[test]
fn uv_lookup_prefers_env_then_homebrew() {
    let a = tempfile::tempdir().unwrap();
    let b = tempfile::tempdir().unwrap();
    let env_uv = a.path().join("uv-env");
    let brew = b.path().join("uv");
    std::fs::write(&env_uv, "").unwrap();
    std::fs::write(&brew, "").unwrap();
    let cands = vec![b.path().join("missing"), brew.clone()];
    assert_eq!(find_uv_with(Some(env_uv.clone()), &cands, None), Some(env_uv));
    assert_eq!(find_uv_with(Some(a.path().join("nope")), &cands, None), Some(brew.clone()));
    assert_eq!(find_uv_with(None, &cands, None), Some(brew));
    assert_eq!(find_uv_with(None, &[], Some(std::ffi::OsString::new())), None);
    // PATH is only the last resort
    let p = std::env::join_paths([b.path()]).unwrap();
    assert_eq!(find_uv_with(None, &[], Some(p)), Some(b.path().join("uv")));
}

#[test]
fn core_cmd_override_used() {
    let _g = ENV.lock().unwrap();
    std::env::set_var("MISES_CORE_CMD", "/bin/echo hello  world");
    let c = core_command(Path::new("/x/core")).unwrap();
    std::env::remove_var("MISES_CORE_CMD");
    assert_eq!(c.program, PathBuf::from("/bin/echo"));
    assert_eq!(c.args, vec!["hello", "world"]);
}

#[test]
fn default_command_runs_uv_serve_on_port_zero() {
    let _g = ENV.lock().unwrap();
    std::env::remove_var("MISES_CORE_CMD");
    let d = tempfile::tempdir().unwrap();
    let uv = d.path().join("uv");
    std::fs::write(&uv, "").unwrap();
    std::env::set_var("MISES_UV", &uv);
    let c = core_command(Path::new("/x/core")).unwrap();
    std::env::remove_var("MISES_UV");
    assert_eq!(c.program, uv);
    assert_eq!(c.args, ["run", "--project", "/x/core", "mises", "serve", "--port", "0"]);
    assert_eq!(c.cwd, PathBuf::from("/x/core"));
}

#[test]
fn attach_when_healthy_core_running() {
    let home = tempfile::tempdir().unwrap();
    let (first, l1) = start(cfg(home.path(), "serve", &[], 1.0));
    assert!(wait_for(&l1, is_ready, 15));
    let (second, l2) = start(cfg(home.path(), "crash", &[], 1.0)); // would crash if it spawned
    assert!(wait_for(&l2, is_ready, 15));
    let info = last_ready_info(&l2);
    assert!(!info.owned);
    assert!(info.base_url.starts_with("http://127.0.0.1:"));
    assert!(info.ws_url.starts_with("ws://127.0.0.1:") && info.ws_url.ends_with("/events"));
    second.stop();
    assert!(read_server_info(&home.path().join("server.json"), None).is_ok(), "attached core untouched");
    assert!(last_ready_info(&l1).owned);
    first.stop();
}

#[test]
fn spawn_waits_for_fresh_server_json() {
    let home = tempfile::tempdir().unwrap();
    let stale = home.path().join("server.json");
    std::fs::write(&stale, r#"{"port":1,"token":"old","pid":1}"#).unwrap();
    std::fs::set_permissions(&stale, std::fs::Permissions::from_mode(0o600)).unwrap();
    let (sup, log) = start(cfg(home.path(), "serve", &[("STUB_DELAY", "1".into())], 1.0));
    assert!(wait_for(&log, is_ready, 20));
    let info = last_ready_info(&log);
    assert!(info.owned);
    assert_eq!(info.token, "stub-token");
    sup.stop();
}

#[test]
fn crash_restarts_with_backoff_then_gives_up() {
    let home = tempfile::tempdir().unwrap();
    let t = Instant::now();
    let (sup, log) = start(cfg(home.path(), "crash", &[], 0.1));
    std::thread::sleep(Duration::from_millis(2500));
    assert!(t.elapsed() >= Duration::from_millis(700), "1+2+4 s at 0.1 scale");
    let seq: Vec<String> = log
        .lock()
        .unwrap()
        .iter()
        .map(|s| match s {
            CoreStatus::Starting => "S".into(),
            CoreStatus::Crashed { restarts, stderr_tail, .. } => {
                assert!(stderr_tail.contains("boom"), "{stderr_tail}");
                format!("C{restarts}")
            }
            _ => "?".into(),
        })
        .collect();
    assert_eq!(seq.join(","), "S,C1,S,C2,S,C3,S,C3");
    sup.stop();
}

#[test]
fn stop_kills_process_group() {
    let home = tempfile::tempdir().unwrap();
    let pf = home.path().join("pids");
    let (sup, log) = start(cfg(home.path(), "fork", &[("STUB_PIDFILE", pf.display().to_string())], 1.0));
    assert!(wait_for(&log, is_ready, 15));
    let pids: Vec<i32> = std::fs::read_to_string(&pf).unwrap().lines().map(|l| l.parse().unwrap()).collect();
    assert_eq!(pids.len(), 2);
    assert!(pids.iter().all(|p| alive(*p)));
    sup.stop();
    std::thread::sleep(Duration::from_millis(200));
    assert!(pids.iter().all(|p| !alive(*p)), "group survived stop()");
    assert!(matches!(log.lock().unwrap().last(), Some(CoreStatus::Stopped)));
}

#[test]
fn serializes_camel_case_for_the_webview() {
    let s = CoreStatus::Crashed { code: Some(1), stderr_tail: "x".into(), restarts: 2 };
    assert_eq!(
        serde_json::to_value(&s).unwrap(),
        serde_json::json!({"state":"crashed","code":1,"stderrTail":"x","restarts":2})
    );
    let r = CoreStatus::Ready {
        info: CoreInfo { base_url: "b".into(), ws_url: "w".into(), token: "t".into(), owned: true },
    };
    assert_eq!(
        serde_json::to_value(&r).unwrap(),
        serde_json::json!({"state":"ready","info":{"baseUrl":"b","wsUrl":"w","token":"t","owned":true}})
    );
}
