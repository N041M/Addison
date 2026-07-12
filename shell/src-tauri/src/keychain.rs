// OS keychain access — engineering-spec §5, §8.3.
//
// API keys of any kind NEVER reach the frontend/webview and are never stored in
// SQLite. They live in the OS keychain, written by this module and read only at
// the moment of use. The device-identity private key is generated here on first
// launch and never leaves the keychain (design-doc §7.5.1).

// TODO(step 7 / step 9):
//  - store_provider_key(role, provider, key)  : frontend -> shell, write to keychain
//  - get_provider_key(role) -> key            : Agent-Core-internal, per-call fetch
//  - ensure_device_keypair() -> device_id     : create-if-absent on first launch
//  - sign_relay_request(payload) -> signature : signs Setup Assistant relay calls
