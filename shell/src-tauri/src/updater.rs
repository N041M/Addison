// Auto-updates — engineering-spec (design-doc §7.7).
//
// Tauri's built-in updater with signed release manifests: silent background
// download, prompt to restart. No manual "check GitHub for a new release" step,
// which is where most self-hosted tools lose non-technical users after week one.

// TODO(step 7 / Phase 3): configure tauri-plugin-updater with the signed
// manifest endpoint + public key. Install to the user's local app-data dir so
// no admin/sudo prompt ever appears (design-doc §7.8).
