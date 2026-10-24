fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["core_info", "core_restart"]),
        ),
    )
    .expect("tauri build failed");
}
