#!/usr/bin/env bash
# SUPERSEDED — this is the manual predecessor of `shell/src-tauri/sign-and-run.sh`.
# ==============================================================================
# Do NOT reach for this script to fix keychain prompts. `sign-and-run.sh` is the
# live mechanism: a cargo *runner* (wired in `shell/src-tauri/.cargo/config.toml`)
# that signs every dev build automatically and — the part that matters — passes an
# EXPLICIT designated requirement. This script does not, and without it `codesign`
# invents one for a self-signed leaf by falling back to `cdhash H"…"`, a hash of the
# binary's CONTENTS. So every rebuild presents a new requirement and the "Always
# Allow" you granted can never match again. That was measured on this repo;
# `docs/KNOWN-GAPS.md` keeps the finding and `docs/CONVENTIONS.md` owns the
# environment rule.
#
# What is still worth reading here is the ONE-TIME certificate setup below,
# including the TRUST step people get stuck on. `sign-and-run.sh` needs the same
# certificate and does not create it either. Signing by hand is otherwise only
# useful for a one-off binary built outside the cargo runner.
#
# WHY THIS EXISTED
# ----------------
# macOS binds an "Always Allow" keychain decision to the application's code-signing
# identity. A `cargo build` produces an AD-HOC signature whose identifier embeds a
# per-build hash (`addison-72d0…`), so every rebuild looks like a brand-new app,
# the saved decision no longer matches, and you are prompted again. Clicking
# "Always Allow" is working correctly — it is being invalidated on the next build.
#
# Signing with a self-signed certificate was expected to give a designated
# requirement based on the CERTIFICATE rather than the build hash, so that the
# decision survived rebuilds. **It does not, on its own** — see the superseded
# banner above; the requirement has to be NAMED explicitly, which is what
# `sign-and-run.sh` added and this script never did. The certificate is still
# necessary, just not sufficient. It is free and local either way: the $99 Apple
# Developer Program is for DISTRIBUTION (letting other people run the app without
# Gatekeeper blocking it), not for this.
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
# THEN you are done: the cargo runner signs every dev build for you. This
# superseded script used to be re-run by hand after each `cargo build` /
# `npm run tauri dev`; it no longer needs to be, and running it does not make
# "Always Allow" stick.

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
echo "NOTE: this signature carries NO explicit designated requirement, so codesign"
echo "falls back to a cdhash of this exact binary and an 'Always Allow' granted now"
echo "will stop matching after the next rebuild. Use shell/src-tauri/sign-and-run.sh"
echo "(the cargo runner, already wired) for a decision that survives rebuilds."
