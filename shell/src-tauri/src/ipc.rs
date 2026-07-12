// JSON-RPC relay: frontend (webview) <-> Agent Core (Python) — engineering-spec §7.
//
// The shell is a relay, not a decision-maker: it forwards frames the Agent Core
// has already validated against the permission gate. The webview never talks to
// the Agent Core or the network directly (§1.3).

// TODO(step 7): #[tauri::command] send_to_core(frame) -> pipes to the core's
// stdin; a reader task pumps the core's stdout notifications back to the webview
// via app.emit(). Method names mirror agent_core/protocol.py::Method.
