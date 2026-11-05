//! The macOS application menu. macOS webviews route ⌘Z/⌘X/⌘C/⌘V/⌘A only when the app has an Edit
//! menu wired to the system edit actions — without it, copy and paste don't work in the webview.
//! This is the root cause the Plan 5 request names ("select, copy and paste text in the box").
//!
//! The Edit submenu's contents live in `EDIT_ITEMS` as plain data so the order can be unit-tested
//! without a running app (building a real `Menu` needs an event loop), and so the builder and the
//! test can't drift apart.

use tauri::menu::{IsMenuItem, Menu, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager, Runtime};

/// One entry of the Edit submenu, mapped to a system `PredefinedMenuItem` by the builder.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EditItem {
    Undo,
    Redo,
    Separator,
    Cut,
    Copy,
    Paste,
    SelectAll,
}

/// The Edit submenu, in order.
pub const EDIT_ITEMS: &[EditItem] = &[
    EditItem::Undo,
    EditItem::Redo,
    EditItem::Separator,
    EditItem::Cut,
    EditItem::Copy,
    EditItem::Paste,
    EditItem::SelectAll,
];

fn edit_item<R: Runtime, M: Manager<R>>(m: &M, which: EditItem) -> tauri::Result<Box<dyn IsMenuItem<R>>> {
    Ok(match which {
        EditItem::Undo => Box::new(PredefinedMenuItem::undo(m, None)?),
        EditItem::Redo => Box::new(PredefinedMenuItem::redo(m, None)?),
        EditItem::Separator => Box::new(PredefinedMenuItem::separator(m)?),
        EditItem::Cut => Box::new(PredefinedMenuItem::cut(m, None)?),
        EditItem::Copy => Box::new(PredefinedMenuItem::copy(m, None)?),
        EditItem::Paste => Box::new(PredefinedMenuItem::paste(m, None)?),
        EditItem::SelectAll => Box::new(PredefinedMenuItem::select_all(m, None)?),
    })
}

/// Build the app menu: the standard macOS app menu (About / Hide / Quit …) plus an Edit submenu
/// carrying the clipboard actions from `EDIT_ITEMS`.
pub fn build_menu<R: Runtime>(handle: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let owned: Vec<Box<dyn IsMenuItem<R>>> =
        EDIT_ITEMS.iter().map(|w| edit_item(handle, *w)).collect::<tauri::Result<_>>()?;
    let refs: Vec<&dyn IsMenuItem<R>> = owned.iter().map(|b| b.as_ref()).collect();
    let edit = Submenu::with_items(handle, "Edit", true, &refs)?;

    let app_menu = Submenu::with_items(
        handle,
        "AgentCAD",
        true,
        &[
            &PredefinedMenuItem::about(handle, Some("About AgentCAD"), None)?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::services(handle, None)?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::hide(handle, None)?,
            &PredefinedMenuItem::hide_others(handle, None)?,
            &PredefinedMenuItem::show_all(handle, None)?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::quit(handle, None)?,
        ],
    )?;

    Menu::with_items(handle, &[&app_menu, &edit])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edit_menu_has_the_clipboard_items() {
        for want in [
            EditItem::Undo,
            EditItem::Redo,
            EditItem::Cut,
            EditItem::Copy,
            EditItem::Paste,
            EditItem::SelectAll,
        ] {
            assert!(EDIT_ITEMS.contains(&want), "Edit menu is missing {want:?}");
        }
    }

    #[test]
    fn edit_items_are_in_a_sensible_order() {
        let pos = |x: EditItem| EDIT_ITEMS.iter().position(|&i| i == x).expect("item present");
        assert!(pos(EditItem::Undo) < pos(EditItem::Redo));
        assert!(pos(EditItem::Cut) < pos(EditItem::Copy));
        assert!(pos(EditItem::Copy) < pos(EditItem::Paste));
        assert!(pos(EditItem::Paste) < pos(EditItem::SelectAll));
    }
}
