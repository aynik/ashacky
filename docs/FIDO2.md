# Touch ID-backed virtual FIDO2 key

This is an **optional prototype**, disabled in generated configurations. The reference Firefox localhost trial passed registration, allow-list sign-in and discoverable sign-in through the native Secure Enclave signer, and the owner confirmed Mac password fallback. The reference service is enabled for boot. Cancellation, persistence after restart and lock behavior remain untested; see [the validation record](VALIDATION.md#optional-fido2-trial-2026-09-10). It does not change PAM, GNOME Keyring or existing account credentials.

## Boundary

The browser sees `Ashacky FIDO2`, a standard FIDO HID device created by the Linux kernel's UHID interface. A root Python transport forwards CTAPHID/CBOR messages through `/run/ashacky-control/fido2.sock` and the existing authenticated `org.ashacky.control` SPICE port. It holds no credential keys. No additional QEMU device, USB passthrough, network listener, browser patch or runtime SSH connection is needed.

`host/auth` is linked into the existing Ashacky.app identity. It parses CTAP requests, binds credentials to the relying-party ID, constructs authenticator data and signs using Apple's Secure Enclave P-256 implementation. The app handles these requests asynchronously so a guest CANCEL can invalidate an outstanding native authentication prompt. Key operations start only while the VM app is foreground and the host console session is active. Sleep, console-session resignation, host locking and control-channel loss cancel pending authentication.

The access-control policy is `privateKeyUsage` plus `userPresence`, with `WhenUnlockedThisDeviceOnly` accessibility. macOS can authorize use through Touch ID **or the Mac account password**; its native prompt owns password entry. Neither that password nor biometric data is sent to Debian or a website. This is different from a CTAP PIN, a Linux password, or the website's own password/recovery option. See Apple's [user-presence policy](https://developer.apple.com/documentation/security/secaccesscontrolcreateflags/userpresence) and [LocalAuthentication keychain guidance](https://developer.apple.com/documentation/localauthentication/accessing-keychain-items-with-face-id-or-touch-id).

The host stores RP/user metadata, public keys and CryptoKit's device-bound encrypted key representations in `PRIVATE_DIRECTORY/fido2/SHA256(INSTALLATION_TOKEN)/credentials.json`, mode 0600 within mode-0700 directories. Raw private keys are not exported. The file must remain outside Git and installation payloads. These are local credentials, not synchronized iCloud passkeys: moving the file to another Mac does not make its keys usable. Preserve the state and installation token during updates; preserve independent account recovery methods while this prototype is being tested.

## Implemented scope

- CTAP2.0 `getInfo`, `makeCredential`, `getAssertion` and `getNextAssertion`; ES256 signatures, packed self-attestation and a zero AAGUID. This carries no vendor-attestation or certification claim.
- Discoverable and allow-list credentials, with at most 256 records. A new resident credential replaces the same RP/user pair. `credProtect` defaults to level 2 and honors a stricter level-3 request. Native user verification is always required for a usable signature; `alwaysUv` is advertised.
- A declared 1024-byte message capacity. This is CTAP's baseline and bounds fragmented UHID bursts. Larger requests fail before allocation. Signature counter zero indicates that counters are unsupported.
- Bounded channels, message parsing and requests; UUID correlation, same-channel cancellation, late-response rejection and expiring next-assertion batches. Idle service waits use descriptors; keepalives and deadlines run only during pending work.

ClientPIN, credential-management/reset commands, legacy U2F, PRF/hmac-secret, large blobs, attestation certification and access to existing iCloud passkeys are not implemented or advertised. Sites requiring these features may reject this key. GNOME Keyring password decryption is a separate integration; FIDO2 does not unlock it by itself. Browser/Flatpak/Snap device access and other distributions require their own acceptance.

Firefox probes supplied allow/exclude-list IDs before its interactive request. A silent probe can confirm a known level-2 credential ID for the correct RP, returning no user metadata, UP/UV both zero, and a deliberately invalid ECDSA `(1, 1)` signature. It never uses the protected key or authorizes a login. Silent resident enumeration is denied, and level-3 credentials remain undisclosed without verification. This uses the pre-flight accommodation in [CTAP section 7.2.3](https://fidoalliance.org/specs/fido-v2.2-ps-20250714/fido-client-to-authenticator-protocol-v2.2-ps-20250714.html#always-uv). Level-3 allow-list login may require client support for verified pre-flight; it is not covered by the normal Firefox trial.

The transport follows [CTAP](https://fidoalliance.org/specs/fido-v2.2-ps-20250714/fido-client-to-authenticator-protocol-v2.2-ps-20250714.html) and the [Linux UHID API](https://docs.kernel.org/hid/uhid.html). The implementation advertises the smaller CTAP2.0 feature set above.

## GNOME Keyring remains separate

A password prompt from GNOME Keyring is expected when the Linux session starts without supplying its unlock password. Passkey authentication returns a signature and verification flags, not the password needed to decrypt an existing keyring. GNOME documents the same limitation for fingerprint and smart-card login in its [daemon integration guide](https://wiki.gnome.org/Projects/GnomeKeyring/RunningDaemon). This feature leaves the encrypted keyring and its password unchanged. Keyring unlock integration is outside this feature’s scope.

## Build and checks

The normal frontend build includes `host/auth` using Command Line Tools and system Security, CryptoKit and LocalAuthentication frameworks. No full Xcode or new third-party runtime library is required. The guest transport uses Python's standard library; its optional unit needs systemd, udev and a kernel with UHID/hidraw support.

`./ashacky check` exercises CTAPHID boundaries with in-memory fixtures. `tools/check-runtime.py` checks private host storage with unusable fixture key data, without requesting Touch ID. For independent CTAP/signature validation, install Yubico's Python client in a development environment (`python3-fido2` on Debian), then compile the test-only peer on macOS:

```sh
mkdir -p build/fido2-check
swiftc -swift-version 5 host/auth/CBOR.swift host/auth/FIDO2.swift \
  tests/fido2-peer.swift -o build/fido2-check/peer
python3 tools/check-fido2.py -- build/fido2-check/peer
```

The peer uses temporary software keys in memory and is never linked into the app, installed or used for real authentication. The checker can also run on Linux with an administrative SSH command after `--` pointing at the compiled peer on the development Mac. This is a source test, not a runtime transport.

The independent checker uses Yubico's `Ctap2` API. Python-fido2 2.2.1's higher-level `Fido2Client` tries to select a PIN protocol before using built-in verification and fails for this PIN-less authenticator; it is not a supported authentication client in this trial. [Mozilla's client](https://github.com/mozilla/authenticator-rs/blob/9eab362ec16f4143a892e06788c3d7c5b7b0a04f/src/ctap2/mod.rs#L306) has an explicit CTAP2.0 built-in-verification path that skips PIN negotiation. Source inspection and fixture checks do not replace browser acceptance.

On Linux, `sudo python3 tools/check-fido2-uhid.py` creates and removes a separate root-only UHID fixture. It verifies discovery, INIT, GetInfo and a fragmented PING with Yubico's client. That fixture has a different unique ID, never signs, and does not receive the desktop access rule. It does not open physical HID devices or restart installed services.

Adding `--check-udev` also simulates the candidate access rule on that disposable device, substituting its fixture ID in a temporary extra rules directory. This verifies the parent-property match and `uaccess` tag without installing a rule or reloading the live udev daemon. It requires a `udevadm test` version with `--extra-rules-dir`; omit this extra check on older distributions and verify the installed ACL during attended acceptance.

After activation, run `python3 tools/check-fido2-browser.py --state build/fido2-browser/public-credential.txt` as the guest desktop user and open the localhost URL it prints in the browser under test. Its buttons explicitly request registration, allow-list sign-in or discoverable sign-in; Cancel aborts a pending browser request. The Yubico server implementation verifies the challenge, origin/RP, user-presence/verification flags and assertion signature. Only a public test credential is saved locally so the same command can check persistence after a restart. The fixture never requests a Linux password and is not installed as a runtime service.

Check one operation at a time. The fixture logs page delivery, operation start and server verification; the host logs CTAP command/status without RP, user, credential IDs or request bytes. These distinguish opening the page from reaching the authenticator. Open the URL manually or use a detached desktop launcher; a timed command that kills `xdg-open` can also kill a newly launched browser. If the desktop stalls, capture host frontend/QEMU thread samples and guest control/transport journals before recovery when possible. Do not infer a Touch ID failure solely from a frozen picture. The interrupted first trial and unverified cases are recorded in [VALIDATION.md](VALIDATION.md#optional-fido2-trial-2026-09-10).

## Attended activation

1. Prepare and validate a matched candidate frontend and guest payload using BUILD.md. Record the prior app, control config, broker, optional service and udev files in the private installation receipt. Do not rebuild over the running signed app.
2. Stop the affected VM/session after the owner's saved-work checkpoint. Install the signed candidate, preserve its existing identity/permissions, and set `fido2Enabled` to `true` in the private host control JSON, preserving all other fields and the token. Restart the session.
3. Apply the root-owned guest layout entries for `ashacky-control`, `ashacky-fido2`, `ashacky-fido2.service` and `70-ashacky-fido2.rules`. Reload systemd/udev and restart the control broker. This briefly reconnects device control; do not do it during authentication or power actions.
4. Verify `/dev/uhid` exists. If UHID is modular, load `uhid` and record a root-owned `/etc/modules-load.d/ashacky-fido2.conf` containing `uhid` for subsequent boots. Do not grant desktop users access to `/dev/uhid` or the private control socket.
5. Start `ashacky-fido2.service` explicitly. It first checks host GetInfo, then publishes the virtual device. A disabled host exits with status 78 without restarting; temporary transport failures use bounded systemd restart backoff. Normal idle operation does not poll. The staging action list keeps this unit separate from normally enabled system services.
6. Verify the resulting hidraw node matches both `Ashacky FIDO2` and `org.ashacky.fido2`. The udev rule grants access only through logind's active-seat `uaccess` ACL. Test browser discovery as the ordinary desktop user, not root.
7. Use a disposable localhost WebAuthn test account. Verify registration, assertion signature, discoverable sign-in, cancellation without success, and the native **Mac password fallback**. Then restart/relogin and verify credential persistence. Test host lock and cancellation recovery without enrolling a real website account.
8. After attended acceptance, enable the guest service for boot, update the receipt and record precisely which browser/native cases passed in STATUS.md. A fresh installation and other browsers/distros remain separate checks.

For rollback, stop and disable only `ashacky-fido2.service`, restore its recorded guest files and the prior signed app/control configuration through the normal session update procedure. Preserve the private credential store; disabling the feature must not delete passkeys. No PAM, website account or keyring changes are needed to stop using the virtual device.
