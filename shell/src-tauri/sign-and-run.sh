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

# NOT a fail-open case, and it used to be treated as one. The old branch warned
# "running it unchanged" and then called `launch`, which `exec`s the very file it
# had just established cannot be executed — so the run died anyway, one line later,
# with the shell's own message ("Permission denied", or "exec format error")
# replacing the explanation that had just been printed. Failing open means running
# UNSIGNED, which is exactly what the identity-missing branch below does; there is
# no honest way to run a file that is not executable. So say so and stop. 126 is the
# conventional status for "found, but not executable", which is what cargo reports.
if [ ! -x "$BINARY" ]; then
    echo "sign-and-run: '$BINARY' is not an executable file, so there is nothing to" >&2
    echo "              sign or run. (cargo passes the freshly-built binary as the" >&2
    echo "              runner's first argument — an empty or missing path here means" >&2
    echo "              the build did not produce one.)" >&2
    exit 126
fi

# ONLY THE APP BINARY. Cargo uses this runner for `cargo test` too, and signing
# every test binary buys nothing (no test touches the keychain ACL), costs time on
# every run, and turns any codesign hiccup into a confusing test failure.
case "$(basename "$BINARY")" in
    addison) ;;
    *) exec "$BINARY" "$@" ;;
esac

CERT_SHA1=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep "\"$IDENTITY\"" | head -1 | awk '{print $2}')

if [ -n "$CERT_SHA1" ]; then
    # --force replaces the ad-hoc signature cargo's linker left behind.
    #
    # -r IS THE WHOLE FIX, and signing without it does NOT work. Signing with a
    # stable identity is necessary and not sufficient: asked to invent a
    # designated requirement for a SELF-SIGNED leaf, codesign falls back to
    # `cdhash H"..."` — a hash of the binary's CONTENTS. macOS stores that
    # requirement as the "Always Allow" ACL entry, so the permission is keyed to
    # those exact bytes and the next rebuild does not match it. That is the
    # original bug wearing a certificate: measured on this repo, a signed build
    # still carried `designated => cdhash H"1380cf87..."`.
    #
    # Naming the requirement explicitly keys the ACL to IDENTITY instead —
    # `identifier addison and certificate leaf = H"<cert>"` — which is stable
    # across every rebuild, because neither the identifier nor the certificate
    # changes when the code does. One "Always Allow" then holds.
    #
    # The certificate hash is read from the keychain rather than written down:
    # it differs per machine, and a hard-coded one would silently reintroduce
    # the cdhash fallback on everybody else's clone.
    REQUIREMENT="designated => identifier \"addison\" and certificate leaf H\"$CERT_SHA1\""
    if ! codesign --force --sign "$IDENTITY" -r="$REQUIREMENT" "$BINARY"; then
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
