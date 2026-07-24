#!/usr/bin/env bash
# Give the dev build a STABLE code-signing identity, so the macOS keychain stops
# asking for your password after every rebuild.
#
# WHY THIS EXISTS
# ---------------
# macOS binds an "Always Allow" keychain decision to the application's code-signing
# identity. A `cargo build` produces an AD-HOC signature whose identifier embeds a
# per-build hash (`addison-72d0…`), so every rebuild looks like a brand-new app,
# the saved decision no longer matches, and you are prompted again. Clicking
# "Always Allow" is working correctly — it is being invalidated on the next build.
#
# Signing with a self-signed certificate instead gives a designated requirement
# based on the CERTIFICATE rather than the build hash, so the decision survives
# rebuilds. This is free and local: the $99 Apple Developer Program is for
# DISTRIBUTION (letting other people run the app without Gatekeeper blocking it),
# not for this.
#
# ONE-TIME SETUP (yours to do — it creates a certificate, which is a security
# setting, so it is not something this script should do on your behalf):
#
#   1. Open Keychain Access.
#   2. Menu: Keychain Access > Certificate Assistant > Create a Certificate…
#   3. Name:            Addison Dev
#      Identity Type:   Self Signed Root
#      Certificate Type: Code Signing
#   4. Create, then Done.
#
# THEN, after any `cargo build` / `npm run tauri dev` rebuild:
#
#   ./scripts/sign-dev-binary.sh
#
# The first launch after signing prompts once more (the identity genuinely
# changed); choose "Always Allow" and it should stick from then on.

set -euo pipefail

IDENTITY="${ADDISON_SIGN_IDENTITY:-Addison Dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="$REPO_ROOT/shell/src-tauri/target/debug/addison"

if ! security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
  echo "No code-signing identity named '$IDENTITY' was found."
  echo
  echo "Create one first — Keychain Access > Certificate Assistant >"
  echo "Create a Certificate…, named '$IDENTITY', Identity Type 'Self Signed Root',"
  echo "Certificate Type 'Code Signing'. Then run this again."
  echo
  echo "(Set ADDISON_SIGN_IDENTITY to use a different name.)"
  exit 1
fi

if [ ! -f "$BINARY" ]; then
  echo "No dev binary at $BINARY — build it first (npm run tauri dev, or cargo build)."
  exit 1
fi

echo "Signing $BINARY as '$IDENTITY'…"
codesign --force --sign "$IDENTITY" "$BINARY"

echo
echo "Done. The identity is now:"
codesign -dvvv "$BINARY" 2>&1 | grep -E "^Identifier|^Authority|^Signature" | sed 's/^/  /'
echo
echo "Launch the app and choose 'Always Allow' once more — the identity changed, so"
echo "that decision is being made against the certificate this time, and it will"
echo "survive the next rebuild."
