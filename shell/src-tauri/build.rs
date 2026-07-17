// Tauri build script — runs `tauri-build` codegen (capability schemas, context
// assets, platform metadata) so `tauri::generate_context!` compiles. Kept minimal;
// signing/updater wiring is Phase 3 (design-doc §7.7).

fn main() {
    tauri_build::build();
}
