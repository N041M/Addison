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
#   2. Menu bar at the TOP OF THE SCREEN (not in the window):
#        Keychain Access > Certificate Assistant > Create a Certificate…
#   3. Name:            Addison Dev      (must match exactly — this script looks
#                                         for it by name)
#      Identity Type:   Self Signed Root
#      Certificate Type: Code Signing
#   4. Create. It warns the certificate is self-signed and not from a recognised
#      authority — that is expected. Continue, then Done.
#   5. TRUST IT, which is a separate step and the one that is easy to miss. A
#      freshly created self-signed root is NOT trusted for code signing, so
#      `security find-identity -v -p codesigning` still reports 0 valid
#      identities and this script still refuses:
#        - find "Addison Dev" under My Certificates and double-click it
#        - expand the "Trust" section
#        - set "Code Signing" to "Always Trust" (leave the rest alone)
#        - close the window; macOS asks for your password to save the setting
#      That prompt is a one-off: it authorises a trust-setting change, not app
#      access to your keychain.
#
# Confirm with:  security find-identity -v -p codesigning
# You want "1 valid identities found" naming Addison Dev.
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
  # Distinguish "not created" from "created but not trusted". They look identical
  # in the valid-identities list and have completely different fixes, and the
  # second one is where people actually get stuck.
  if security find-identity -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
    echo "'$IDENTITY' exists but is NOT TRUSTED for code signing, so it cannot sign yet."
    echo
    echo "In Keychain Access: find '$IDENTITY' under My Certificates, double-click it,"
    echo "expand 'Trust', set 'Code Signing' to 'Always Trust', and close the window."
    echo "macOS will ask for your password to save that setting — that prompt is a"
    echo "one-off and is authorising the trust change, not app access to your keychain."
  else
    echo "No code-signing identity named '$IDENTITY' was found."
    echo
    echo "Create one — Keychain Access (menu bar, top of the screen) >"
    echo "Certificate Assistant > Create a Certificate…, named '$IDENTITY',"
    echo "Identity Type 'Self Signed Root', Certificate Type 'Code Signing'."
    echo "Then TRUST it for Code Signing; see this script's header for that step."
  fi
  echo
  echo "Confirm with: security find-identity -v -p codesigning"
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
