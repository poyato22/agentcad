pub mod core;
pub mod menu;
pub mod platform;
pub mod server_info;

use core::{core_info, core_restart, core_status, SharedSupervisor, Supervisor};
use std::sync::Arc;
use tauri::{Manager, RunEvent};

pub fn run() {
    let app = tauri::Builder::default()
        // The macOS app/Edit menu: without the Edit submenu the webview can't route ⌘C/⌘V/⌘A.
        .menu(menu::build_menu)
        // External links (http/https only; capabilities/default.json scopes it): ui platform/openExternal.ts
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![core_info, core_restart, core_status])
        .setup(|app| {
            let sup: SharedSupervisor = Arc::new(Supervisor::start(app.handle().clone()));
            core::install_signal_stop(sup.clone());
            app.manage(sup);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Mises");
    app.run(|handle, event| {
        if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
            if let Some(sup) = handle.try_state::<SharedSupervisor>() {
                sup.stop(); // idempotent: kills an owned core's process group, never an attached one
            }
        }
    });
}
