// Addison Tauri shell entrypoint — engineering-spec §1.3.
//
// Highest-trust process: real OS permissions (file picker, keychain,
// notifications, updater). It NEVER executes model-provided instructions
// directly — it only relays IPC calls the Agent Core has already validated
// against the permission gate (§4.3).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod agent_process;
mod filesystem;
mod ipc;
mod keychain;
mod updater;

fn main() {
    // TODO(step 7): spawn the Python Agent Core (agent_process), wire the
    // JSON-RPC relay between frontend <-> core (ipc), register keychain /
    // filesystem / updater commands, then run the Tauri app.
    //
    // tauri::Builder::default()
    //     .plugin(tauri_plugin_updater::Builder::new().build())
    //     .setup(|app| { agent_process::spawn(app)?; Ok(()) })
    //     .invoke_handler(tauri::generate_handler![
    //         ipc::send_to_core,
    //         keychain::store_provider_key,
    //         filesystem::pick_file,
    //     ])
    //     .run(tauri::generate_context!())
    //     .expect("error while running Addison");
    eprintln!("Addison shell scaffold — implement per engineering-spec §11 step 7.");
}
