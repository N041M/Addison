// Spawns and supervises the Python Agent Core child process — engineering-spec §1.1, §1.3.
//
// The core ships as a bundled child process (PyInstaller or similar), NOT a
// system dependency the user installs (§1.2, design-doc §7.8). Communication is
// JSON-RPC 2.0 over stdio (§7) — no network stack for local IPC.

// TODO(step 7):
//  - locate the bundled agent_core executable next to the app binary
//  - spawn it with piped stdin/stdout
//  - forward JSON-RPC frames both ways (see ipc.rs)
//  - restart on unexpected exit, surfaced to the user as a plain-language notice
