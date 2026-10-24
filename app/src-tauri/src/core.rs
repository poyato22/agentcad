//! Core sidecar supervisor: attach to a running `mises serve` or spawn and babysit one.
use crate::platform;
use crate::server_info::{health_ok, mises_home, read_server_info, ServerInfo};
use serde::Serialize;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime};

const START_TIMEOUT: Duration = Duration::from_secs(20);
const KILL_GRACE: Duration = Duration::from_secs(5);
const BACKOFF_SECS: [u64; 3] = [1, 2, 4];
const HEALTHY_RESET: Duration = Duration::from_secs(60);
const TAIL_BYTES: usize = 4096;

#[derive(Serialize, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CoreInfo {
    pub base_url: String,
    pub ws_url: String,
    pub token: String,
    pub owned: bool,
    /// The core (Python) process from server.json: runs whose owner_pid matches are live in it.
    pub pid: u32,
}

impl std::fmt::Debug for CoreInfo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CoreInfo")
            .field("base_url", &self.base_url)
            .field("ws_url", &self.ws_url)
            .field("token", &"<redacted>")
            .field("owned", &self.owned)
            .field("pid", &self.pid)
            .finish()
    }
}

#[derive(Serialize, Clone, Debug, PartialEq)]
#[serde(tag = "state", rename_all = "camelCase", rename_all_fields = "camelCase")]
pub enum CoreStatus {
    Starting,
    Ready { info: CoreInfo },
    Crashed { code: Option<i32>, stderr_tail: String, restarts: u32 },
    Stopped,
}

#[derive(Clone, Debug)]
pub struct CoreCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: Vec<(String, String)>,
}

/// First existing: explicit env path, fixed install locations, then PATH.
pub fn find_uv_with(env_uv: Option<PathBuf>, candidates: &[PathBuf], path: Option<OsString>) -> Option<PathBuf> {
    let from_path = path.into_iter().flat_map(|p| std::env::split_paths(&p).map(|d| d.join("uv")).collect::<Vec<_>>());
    env_uv.into_iter().chain(candidates.iter().cloned()).chain(from_path).find(|p| p.is_file())
}

pub fn core_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("MISES_CORE_DIR").filter(|v| !v.is_empty()) {
        return d.into();
    }
    match option_env!("MISES_CORE_DIR") {
        Some(d) => d.into(),
        None => Path::new(env!("CARGO_MANIFEST_DIR")).join("../../core"),
    }
}

pub fn core_command(core_dir: &Path) -> Result<CoreCommand, String> {
    let cwd = core_dir.to_path_buf();
    let env = vec![];
    // The override is a test hook: ignored in release builds so a stray env var can't run arbitrary code.
    let ov = if cfg!(debug_assertions) { std::env::var("MISES_CORE_CMD").ok() } else { None };
    if let Some(ov) = ov.filter(|v| !v.trim().is_empty()) {
        let mut parts = ov.split_whitespace().map(String::from);
        let program = PathBuf::from(parts.next().unwrap_or_default());
        return Ok(CoreCommand { program, args: parts.collect(), cwd, env });
    }
    let uv = find_uv_with(std::env::var_os("MISES_UV").map(PathBuf::from), &platform::uv_candidates(), std::env::var_os("PATH"));
    choose_core_command(core_dir, uv)
}

/// `uv run` when uv is found (it keeps the venv in step with uv.lock), else the synced venv's own
/// entry point. Either way a SIGKILLed app ends the core: `uv run` stays as the core's parent but
/// holds only the read end of our stdin pipe, so the core still sees EOF once the app's write end
/// closes (tests/lifecycle.rs: sigkill_of_the_app_on_the_uv_path_still_ends_the_core_by_stdin_eof).
pub fn choose_core_command(core_dir: &Path, uv: Option<PathBuf>) -> Result<CoreCommand, String> {
    if let Some(uv) = uv {
        return Ok(uv_core_command(&uv, core_dir));
    }
    let venv_mises = core_dir.join(".venv/bin/mises");
    if venv_mises.is_file() {
        let args = ["serve", "--port", "0", "--exit-on-stdin-eof"].map(String::from).to_vec();
        return Ok(CoreCommand { program: venv_mises, args, cwd: core_dir.to_path_buf(), env: vec![] });
    }
    Err("could not find `uv` (looked in $MISES_UV, /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, ~/.cargo/bin, PATH) \
         and the core has no synced venv; install uv with `brew install uv`".into())
}

/// `uv run --project <core> mises serve --port 0 --exit-on-stdin-eof`.
pub fn uv_core_command(uv: &Path, core_dir: &Path) -> CoreCommand {
    let args = ["run", "--project", &core_dir.display().to_string(), "mises", "serve", "--port", "0", "--exit-on-stdin-eof"];
    CoreCommand { program: uv.to_path_buf(), args: args.map(String::from).to_vec(), cwd: core_dir.to_path_buf(), env: vec![] }
}

#[derive(Clone)]
pub struct SupervisorConfig {
    pub command: CoreCommand,
    pub home: PathBuf,
    /// Multiplies every wait (backoff, timeouts); 1.0 in production.
    pub time_scale: f64,
}

pub type Emit = Arc<dyn Fn(&CoreStatus) + Send + Sync>;
pub type SharedSupervisor = Arc<Supervisor>;

pub struct Supervisor {
    cfg: Option<SupervisorConfig>,
    emit: Emit,
    status: Arc<Mutex<CoreStatus>>,
    run: Mutex<Option<(Arc<AtomicBool>, JoinHandle<()>)>>,
    /// Held across stop()/restart() so concurrent lifecycle calls can't interleave.
    lifecycle: Mutex<()>,
    /// Set once by stop(); no core may be launched afterwards.
    shutting_down: AtomicBool,
}

struct Ctx {
    cfg: SupervisorConfig,
    emit: Emit,
    status: Arc<Mutex<CoreStatus>>,
    stop: Arc<AtomicBool>,
}

impl Ctx {
    fn set(&self, s: CoreStatus) {
        *self.status.lock().unwrap() = s.clone();
        (self.emit)(&s);
    }
    fn wait(&self, d: Duration) -> bool {
        let end = Instant::now() + d.mul_f64(self.cfg.time_scale);
        while Instant::now() < end && !self.stopped() {
            std::thread::sleep(Duration::from_millis(20));
        }
        !self.stopped()
    }
    fn stopped(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }
    fn scaled(&self, d: Duration) -> Duration {
        d.mul_f64(self.cfg.time_scale)
    }
    fn info_path(&self) -> PathBuf {
        self.cfg.home.join("server.json")
    }
}

fn ready(info: &ServerInfo, owned: bool) -> CoreStatus {
    CoreStatus::Ready {
        info: CoreInfo {
            base_url: format!("http://127.0.0.1:{}", info.port),
            ws_url: format!("ws://127.0.0.1:{}/events", info.port),
            token: info.token.clone(),
            owned,
            pid: info.pid,
        },
    }
}

fn tail(buf: &Arc<Mutex<String>>) -> String {
    buf.lock().unwrap().clone()
}

impl Supervisor {
    pub fn start(app: tauri::AppHandle) -> Self {
        use tauri::Emitter;
        let emit: Emit = Arc::new(move |s| {
            let _ = app.emit("core-status", s);
        });
        match core_command(&core_dir()) {
            Ok(command) => Self::start_with(SupervisorConfig { command, home: mises_home(), time_scale: 1.0 }, emit),
            Err(msg) => Self::failed(msg, emit),
        }
    }

    /// A supervisor that never started (e.g. `uv` not found): permanently Crashed with the reason.
    pub fn failed(msg: String, emit: Emit) -> Self {
        let s = CoreStatus::Crashed { code: None, stderr_tail: msg, restarts: 0 };
        emit(&s);
        Self::new(None, emit, s)
    }

    pub fn start_with(cfg: SupervisorConfig, emit: Emit) -> Self {
        let sup = Self::new(Some(cfg), emit, CoreStatus::Starting);
        sup.launch();
        sup
    }

    fn new(cfg: Option<SupervisorConfig>, emit: Emit, status: CoreStatus) -> Self {
        Self {
            cfg,
            emit,
            status: Arc::new(Mutex::new(status)),
            run: Mutex::new(None),
            lifecycle: Mutex::new(()),
            shutting_down: AtomicBool::new(false),
        }
    }

    pub fn status(&self) -> CoreStatus {
        self.status.lock().unwrap().clone()
    }

    fn launch(&self) {
        let Some(cfg) = self.cfg.clone() else { return };
        if self.shutting_down.load(Ordering::SeqCst) {
            return;
        }
        let stop = Arc::new(AtomicBool::new(false));
        let ctx = Ctx { cfg, emit: self.emit.clone(), status: self.status.clone(), stop: stop.clone() };
        let handle = std::thread::spawn(move || monitor(&ctx));
        *self.run.lock().unwrap() = Some((stop, handle));
    }

    fn stop_running(&self) {
        let running = self.run.lock().unwrap().take();
        if let Some((stop, handle)) = running {
            stop.store(true, Ordering::SeqCst);
            let _ = handle.join();
        }
        if self.cfg.is_some() {
            *self.status.lock().unwrap() = CoreStatus::Stopped;
            (self.emit)(&CoreStatus::Stopped);
        }
    }

    /// Final shutdown: stops an owned core (SIGTERM to its process group, SIGKILL after 5 s) and
    /// forbids further launches. An attached core is left alone. Idempotent.
    pub fn stop(&self) {
        let _life = self.lifecycle.lock().unwrap();
        self.shutting_down.store(true, Ordering::SeqCst);
        self.stop_running();
    }

    pub fn restart(&self) {
        let _life = self.lifecycle.lock().unwrap();
        if self.shutting_down.load(Ordering::SeqCst) {
            return;
        }
        self.stop_running();
        *self.status.lock().unwrap() = CoreStatus::Starting;
        (self.emit)(&CoreStatus::Starting);
        self.launch();
    }
}

impl Drop for Supervisor {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Stop the owned core on SIGTERM/SIGINT/SIGHUP, then exit. (SIGKILL is covered by the core's
/// `--exit-on-stdin-eof`: the pipe we hold on its stdin closes when we die.)
pub fn install_signal_stop(sup: SharedSupervisor) {
    use signal_hook::consts::{SIGHUP, SIGINT, SIGTERM};
    let Ok(mut signals) = signal_hook::iterator::Signals::new([SIGTERM, SIGINT, SIGHUP]) else { return };
    std::thread::spawn(move || {
        if let Some(sig) = signals.forever().next() {
            sup.stop();
            std::process::exit(128 + sig);
        }
    });
}

fn spawn(cmd: &CoreCommand, tail_buf: &Arc<Mutex<String>>) -> std::io::Result<Child> {
    let mut c = Command::new(&cmd.program);
    c.args(&cmd.args).envs(cmd.env.iter().cloned()).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped());
    if cmd.cwd.is_dir() {
        c.current_dir(&cmd.cwd);
    }
    platform::own_process_group(&mut c);
    let mut child = c.spawn()?;
    tail_buf.lock().unwrap().clear();
    if let Some(mut err) = child.stderr.take() {
        let buf = tail_buf.clone();
        std::thread::spawn(move || {
            let mut chunk = [0u8; 1024];
            while let Ok(n) = err.read(&mut chunk) {
                if n == 0 {
                    break;
                }
                let mut b = buf.lock().unwrap();
                b.push_str(&String::from_utf8_lossy(&chunk[..n]));
                if b.len() > TAIL_BYTES {
                    let mut cut = b.len() - TAIL_BYTES;
                    while !b.is_char_boundary(cut) {
                        cut += 1;
                    }
                    b.drain(..cut);
                }
            }
        });
    }
    Ok(child)
}

fn terminate(ctx: &Ctx, child: &mut Child) {
    let pgid = child.id();
    platform::signal_group(pgid, false);
    let end = Instant::now() + ctx.scaled(KILL_GRACE);
    while Instant::now() < end && matches!(child.try_wait(), Ok(None)) {
        std::thread::sleep(Duration::from_millis(20));
    }
    platform::signal_group(pgid, true); // also reaps stragglers in the group
    let _ = child.wait();
}

enum Outcome {
    Stopped,
    /// Exit code and how long the core had been healthy (zero if it never became ready).
    Exited(Option<i32>, Duration),
}

fn monitor(ctx: &Ctx) {
    let tail_buf = Arc::new(Mutex::new(String::new()));
    let mut restarts = 0u32;
    ctx.set(CoreStatus::Starting);
    if let Ok(info) = read_server_info(&ctx.info_path(), None) {
        if health_ok(&info, Duration::from_secs(2)) {
            // Possibly left by a previous app instance: attach anyway (never kill blindly), but say so.
            eprintln!("mises: attaching to existing core (pid {}); not started by this app instance", info.pid);
            ctx.set(ready(&info, false));
            return;
        }
    }
    loop {
        let (code, uptime) = match run_once(ctx, &tail_buf) {
            Outcome::Stopped => return,
            Outcome::Exited(code, up) => (code, up),
        };
        if uptime >= ctx.scaled(HEALTHY_RESET) {
            restarts = 0; // it ran fine for a while; a new crash starts a fresh budget
        }
        if ctx.stopped() {
            return;
        }
        let at_limit = restarts as usize >= BACKOFF_SECS.len();
        if !at_limit {
            restarts += 1;
        }
        ctx.set(CoreStatus::Crashed { code, stderr_tail: tail(&tail_buf), restarts });
        if at_limit || !ctx.wait(Duration::from_secs(BACKOFF_SECS[restarts as usize - 1])) {
            return;
        }
        ctx.set(CoreStatus::Starting);
    }
}

fn run_once(ctx: &Ctx, tail_buf: &Arc<Mutex<String>>) -> Outcome {
    let spawned_at = SystemTime::now();
    let mut child = match spawn(&ctx.cfg.command, tail_buf) {
        Ok(c) => c,
        Err(e) => {
            *tail_buf.lock().unwrap() = format!("failed to start {}: {e}", ctx.cfg.command.program.display());
            return Outcome::Exited(None, Duration::ZERO);
        }
    };
    let mut ready_at: Option<Instant> = None;
    let deadline = Instant::now() + ctx.scaled(START_TIMEOUT);
    let mut announced = false;
    loop {
        if ctx.stopped() {
            terminate(ctx, &mut child);
            return Outcome::Stopped;
        }
        if let Ok(Some(status)) = child.try_wait() {
            platform::signal_group(child.id(), true);
            std::thread::sleep(Duration::from_millis(50)); // let the stderr reader drain
            return Outcome::Exited(status.code(), ready_at.map_or(Duration::ZERO, |t| t.elapsed()));
        }
        if !announced {
            if let Ok(info) = read_server_info(&ctx.info_path(), Some(spawned_at)) {
                if health_ok(&info, Duration::from_millis(500)) {
                    ctx.set(ready(&info, true));
                    announced = true;
                    ready_at = Some(Instant::now());
                }
            }
            if !announced && Instant::now() > deadline {
                *tail_buf.lock().unwrap() += "\ncore did not become healthy in time";
                terminate(ctx, &mut child);
                return Outcome::Exited(None, Duration::ZERO);
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[tauri::command]
pub fn core_info(state: tauri::State<SharedSupervisor>) -> Result<CoreInfo, String> {
    match state.status() {
        CoreStatus::Ready { info } => Ok(info),
        CoreStatus::Starting => Err("starting".into()),
        CoreStatus::Crashed { stderr_tail, .. } => Err(format!("crashed: {stderr_tail}")),
        CoreStatus::Stopped => Err("stopped".into()),
    }
}

/// The supervisor's current status. The webview asks for it on load: a `core-status` event
/// emitted during setup (e.g. "`uv` not found") fires before the webview is listening.
pub fn core_status_of(sup: &Supervisor) -> CoreStatus {
    sup.status()
}

#[tauri::command]
pub fn core_status(state: tauri::State<SharedSupervisor>) -> CoreStatus {
    core_status_of(&state)
}

#[tauri::command]
pub fn core_restart(state: tauri::State<SharedSupervisor>) -> Result<(), String> {
    let sup = state.inner().clone();
    std::thread::spawn(move || sup.restart()); // stop() can take seconds; don't block the IPC thread
    Ok(())
}
