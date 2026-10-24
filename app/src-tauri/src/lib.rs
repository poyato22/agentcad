pub mod core;
pub mod platform;
pub mod server_info;

use core::{core_info, core_restart, SharedSupervisor, Supervisor};
use std::sync::Arc;
use tauri::{Manager, RunEvent};

pub fn run() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![core_info, core_restart])
        .setup(|app| {
            let sup: SharedSupervisor = Arc::new(Supervisor::start(app.handle().clone()));
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
