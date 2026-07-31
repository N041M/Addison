#!/bin/sh
# Cargo RUNNER for macOS dev builds — sign, then exec. See .cargo/config.toml.
#
# WHY THIS EXISTS. macOS keys a keychain item's "Always Allow" decision to the
# requesting app's code signature. A Tauri dev binary is ad-hoc signed, and an
# ad-hoc signature carries a per-build identifier (`addison-<hash>`), so **every
# rebuild presents itself to macOS as a different application** and the ACL written
# last time no longer matches. That is why the password dialogs come back after a
# rebuild and never after a plain relaunch, and it is why "Always Allow" appears
# not to work: it worked, for a binary that no longer exists.
#
# Signing with one stable identity gives the ACL something durable to match, so a
# single "Always Allow" holds across every later rebuild.
#
# WHY A CARGO RUNNER, of all the hooks. `npm run tauri dev` builds and launches in
# one step, so there is no gap to sign in: Tauri's `beforeDevCommand` runs before
# the Rust build, and `bundle.macOS.signingIdentity` applies to `tauri build` only.
# Cargo's `runner` is invoked with the freshly-built binary and is responsible for
# starting it — which is exactly the seam. The developer's workflow does not change
# at all; `npm run tauri dev` keeps working.
#
# FAILING OPEN IS DELIBERATE. This is a developer-convenience wrapper, not a
# security control: nothing in Addison's safety model depends on the dev binary
# being signed. A machine without the identity (a fresh clone, CI, a colleague)
# must still build and run, so a missing identity WARNS and runs unsigned rather
# than failing the build. The cost of that path is the keychain prompts coming
# back — annoying, and visible, which is the right shape for a fallback.
#
# Create the identity once, in Keychain Access:
#   Certificate Assistant -> Create a Certificate…
#   Name: Addison Dev   Identity Type: Self Signed Root   Type: Code Signing
# Nothing else is needed; this script finds it by name.

IDENTITY="Addison Dev"
BINARY="$1"
shift

# Sign without launching. The normal path ends in `exec` into a GUI app, so this is
# the only way to test the script itself — and an untested signing step is how this
# whole thread started.
sign_only="${ADDISON_SIGN_ONLY:-}"

launch() {
    [ -n "$sign_only" ] && exit 0
    exec "$BINARY" "$@"
}

if [ ! -x "$BINARY" ]; then
    echo "sign-and-run: '$BINARY' is not an executable; running it unchanged." >&2
    launch "$@"
fi

# ONLY THE APP BINARY. Cargo uses this runner for `cargo test` too, and signing
# every test binary buys nothing (no test touches the keychain ACL), costs time on
# every run, and turns any codesign hiccup into a confusing test failure.
case "$(basename "$BINARY")" in
    addison) ;;
    *) exec "$BINARY" "$@" ;;
esac

if security find-identity -v -p codesigning 2>/dev/null | grep -q "\"$IDENTITY\""; then
    # --force replaces the ad-hoc signature cargo's linker left behind.
    if ! codesign --force --sign "$IDENTITY" "$BINARY"; then
        echo "sign-and-run: codesign failed; running unsigned (keychain prompts will" >&2
        echo "              come back on every rebuild)." >&2
    fi
else
    echo "sign-and-run: no '$IDENTITY' code-signing identity found, so this build is" >&2
    echo "              ad-hoc signed and macOS will re-ask for keychain access after" >&2
    echo "              every rebuild. Create one in Keychain Access (Certificate" >&2
    echo "              Assistant -> Create a Certificate, Self Signed Root, Code" >&2
    echo "              Signing) named exactly: $IDENTITY" >&2
fi

launch "$@"
