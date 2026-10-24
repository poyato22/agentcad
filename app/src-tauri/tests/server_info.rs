use mises_app_lib::server_info::*;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::time::{Duration, SystemTime};

fn write(dir: &std::path::Path, body: &str, mode: u32) -> std::path::PathBuf {
    let p = dir.join("server.json");
    fs::write(&p, body).unwrap();
    fs::set_permissions(&p, fs::Permissions::from_mode(mode)).unwrap();
    p
}

#[test]
fn reads_valid_server_json() {
    let d = tempfile::tempdir().unwrap();
    let p = write(d.path(), r#"{"port":7400,"token":"t","pid":42}"#, 0o600);
    let i = read_server_info(&p, None).unwrap();
    assert_eq!(i, ServerInfo { port: 7400, token: "t".into(), pid: 42 });
}

#[test]
fn missing_file_is_missing() {
    let d = tempfile::tempdir().unwrap();
    assert_eq!(read_server_info(&d.path().join("server.json"), None), Err(InfoError::Missing));
}

#[test]
fn rejects_world_readable_server_json() {
    let d = tempfile::tempdir().unwrap();
    for mode in [0o644, 0o640, 0o604] {
        let p = write(d.path(), r#"{"port":1,"token":"t","pid":1}"#, mode);
        assert_eq!(read_server_info(&p, None), Err(InfoError::BadPerms), "mode {mode:o}");
    }
}

#[test]
fn rejects_server_json_older_than_spawn() {
    let d = tempfile::tempdir().unwrap();
    let p = write(d.path(), r#"{"port":1,"token":"t","pid":1}"#, 0o600);
    let later = SystemTime::now() + Duration::from_secs(60);
    assert_eq!(read_server_info(&p, Some(later)), Err(InfoError::TooOld));
    let earlier = SystemTime::now() - Duration::from_secs(60);
    assert!(read_server_info(&p, Some(earlier)).is_ok());
}

#[test]
fn bad_json_is_error_not_panic() {
    let d = tempfile::tempdir().unwrap();
    for body in ["", "{", r#"{"port":"x"}"#, r#"{"port":99999,"token":"t","pid":1}"#] {
        let p = write(d.path(), body, 0o600);
        assert_eq!(read_server_info(&p, None), Err(InfoError::BadJson), "{body}");
    }
}
