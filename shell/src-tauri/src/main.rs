// Addison Tauri shell entrypoint — engineering-spec §1.3.
//
// Highest-trust process: real OS permissions (file picker, keychain,
// notifications, updater). It NEVER executes model-provided instructions
// directly — it only relays IPC calls the Agent Core has already validated
// against the permission gate (§4.3).
//
// The webview is exposed EXACTLY two commands:
//   - `send_to_core`     : relay a JSON-RPC frame to the core (rejects shell.*/
//                          keychain.* — those are Core->Shell only, §1.3/§8);
//   - `store_provider_key`: write-only save of a BYOK key into the OS keychain
//                          (there is deliberately no read-back command, §5).
// Core->Shell `shell.*`/`keychain.*` requests arrive over the core's stdout and
// are handled inside agent_process.rs — never reachable from the window.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod agent_process;
mod filesystem;
mod ipc;
mod keychain;
mod updater;

fn main() {
    tauri::Builder::default()
        // Session-scoped filesystem bookkeeping (created files, scoped handles).
        // CoreStdin is managed inside agent_process::spawn once the child launches.
        .manage(filesystem::FileState::default())
        .setup(|app| {
            // Spawn + supervise the Python Agent Core (§1.1). This also registers
            // the CoreStdin managed state that `send_to_core` writes to.
            agent_process::spawn(app.handle())?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ipc::send_to_core,
            keychain::store_provider_key,
        ])
        // TODO(Phase 3): tauri_plugin_updater — signed manifest endpoint + pubkey
        // (design-doc §7.7). Left unwired here on purpose (updater.rs).
        .run(tauri::generate_context!())
        .expect("error while running Addison");
}
