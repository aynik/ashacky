# Build and consolidation validation

The initial build-only audit on 2026-09-08 left the reference VM, installed services, login settings and production executable unchanged. Subsequent reviews and authorized reference updates are recorded separately below.

| Check | Result |
| --- | --- |
| Repository text/privacy patterns; Python/JSON/shell syntax | Passed |
| Seven focused trackpad/configuration checks | Passed: contact lifecycle and malformed input, mandatory transport config, config permissions, VM/NIC identity, four explicit USB root ports, non-root helper identity |
| Guest camera reader and PAM module compilation | Passed |
| General VA-API driver, Firefox preload library and decoder broker compilation | Passed with the existing pinned remote FFmpeg dependency |
| Wi-Fi, Bluetooth rfkill and battery kernel modules | Compiled against Debian `7.1.12+deb14-arm64` headers in the repository build tree; not installed |
| Host control, SessionSync and shared video codec compilation | Passed in an isolated temporary macOS build directory |
| Wi-Fi, Bluetooth and camera permission-bundle compilation/signing | Passed; bundles were not launched or installed |
| Root power and USB helper compilation | Passed using temporary installation constants; helpers were not launched or installed |
| SessionSync transition self-test | Passed without locking/unlocking the real session |
| Pinned QEMU archive download/checksum and patch application | Passed |
| QEMU source reconstruction | All six locally changed source files match the working host source byte-for-byte, including the shared-memory device |
| Pinned CocoaSpice checkout and patch application | Passed; all five modified source files match the working host source byte-for-byte |
| Firmware provenance | Decompressed QEMU archive firmware matches the working immutable firmware code image; hash recorded in `upstream/firmware.json` |
| Guest file layout | Every listed source/build input exists after the component build |

## Submodule migration

The subsequent source-layout migration on the same date was checked separately:

- Fetched all 17 top-level pins from their public upstream repositories and initialized SPICE's nested dependencies recursively.
- Prepared every dependency recipe successfully, including all patches and nested SPICE exports, while retaining clean upstream checkouts.
- Compared the reconstructed VA driver, H.264 sources and unchanged bitstream header with the previous maintained files: byte-identical. The generated keymap also matches the previous header byte-for-byte.
- Rebuilt guest components, including the VA driver and decoder broker, with the existing remote FFmpeg prefix. Nothing was installed.
- Added source-preparation checks for clean/pinned checkouts, a fresh local fixture clone with recursive initialization and nested exports, missing submodules, cache invalidation and archive traversal rejection. That migration brought the repository check to 14 focused tests.

## Instructions and build audit

A subsequent audit on 2026-09-08 compared the documented commands with the scripts and executed the component recipes. It corrected two macOS test portability issues: importing Linux-only evdev and comparing a canonical temporary path with its `/var` symlink spelling. The instructions now specify the required macOS Python/PATH setup, Debian development packages, and the separate H.264 host server and listener configuration.

| Check | Result |
| --- | --- |
| Full documented FFmpeg shell block, including private-prefix install and guest build | Passed from the pinned patched source; optional library autodetection disabled to avoid hidden system graphics dependencies |
| Guest broker linkage | Resolves FFmpeg from the checkout's new build prefix; no dependency on the previous development FFmpeg prefix |
| Guest kernel build targets and installation file map | Passed for the reference kernel; every mapped input exists |
| `./ashacky build host --installation ...` | Passed in a temporary source export on macOS, including all permission bundles, both privileged helpers and the SessionSync self-test; nothing launched or installed |
| Patched `vtremoted` Swift release recipe | Passed in the same temporary build; documented executable path and linked libraries checked |
| Linux repository checks | 14 tests passed, plus submodule validation, source syntax and privacy checks |
| macOS unit checks | 12 tests passed; two Linux evdev tests explicitly skipped. Includes source preparation with native Git and patch in temporary fixture repositories |
| Previous source-shader-only frontend build | Stopped at the required Metal compiler check; superseded by the release-asset path below |
| Relative documentation links | All resolve |

## UTM assets and Command Line Tools build

On 2026-09-09 the normal frontend build was changed to use verified release assets, removing full Xcode/Metal from the setup requirements:

- Downloaded the public UTM 5.0.5 disk image (302,621,893 bytes), verified its GitHub release SHA-256, and mounted it read-only without installing or launching the app.
- Extracted only six graphics frameworks and the CocoaSpice renderer resource bundle. Component tree hashes preserve verification of file contents, executable bits and relative symlinks; reuse rechecks the extracted trees.
- The shader matches the prototype's original UTM shader byte-for-byte. Prepared source/header hashes guard its interface against incompatible shader changes.
- Built the patched CocoaSpice frontend in a temporary native Git checkout on macOS with Command Line Tools selected and no Metal compiler. CocoaSpice and keycodemapdb were initialized from their public pinned submodules; SPICE/GStreamer development libraries came from the existing reference prefixes.
- Loaded the generated app's shader through Metal and successfully created its rendering pipeline on Apple M1. All six extracted graphics frameworks also loaded successfully. These checks open no VM or display window.
- Tested extraction, clean cache reuse and the native frontend build on macOS. Added focused tests for content tampering, symlink escape, cached archive corruption, shader-source mismatch and unmounting on verification failure.

## Complete isolated runtime build

The next audit on 2026-09-09 completed the missing runtime recipes in a temporary native checkout on the reference M1 host. No additional agent, package-manager installation/upgrade, VM boot, service installation or login change was used. The old development SPICE/GStreamer prefixes were excluded from the build environment.

| Check | Result |
| --- | --- |
| Complete documented `build-runtime.py all` recipe | Passed using Command Line Tools, existing Homebrew prerequisites and an isolated Python environment with pinned build tools; full Xcode and the Metal compiler were absent |
| Private SPICE/GStreamer dependency builds | Passed from the pinned, patched sources; release-version metadata added to exported SPICE sources so they do not derive their version from Ashacky's Git history |
| Standalone QEMU, qemu-img and render server | Built from prepared sources, including the renderer's private implementation dependencies; QEMU reports version 10.0.12 |
| CocoaSpice frontend and host helpers | Built against the new private prefix; permission apps were signed but not launched |
| Complete app assembly | 53 Mach-O files with all non-system dependencies inside the bundle; nested signing and strict/deep signature verification passed |
| Effective minimum macOS version | 15.0 for this build, computed from the deployment targets of bundled binaries; external Homebrew dependencies can raise this minimum |
| Relocation | Passed after copying the app to a temporary path containing spaces, removing Homebrew from the child PATH and clearing DYLD overrides |
| QEMU feature queries | HVF, SPICE GPU properties, Venus/Neptune, shared memory, USB redirection and 9p present; no VM started |
| Graphics and codec loading | All six UTM graphics frameworks and bundled LZ4/Zstandard libraries loaded; CocoaSpice's Metal shader pipeline created successfully on Apple M1 |
| Audio component check | Required GStreamer plugins registered and a silent in-memory audio pipeline passed; no physical audio input/output opened |
| Privileged helper payload | Four executables and three dependency libraries staged, relocated and signed using illustrative account constants; no helper installed or invoked |
| Offline installation plan | Matching private host/guest identities and manifests generated; native `plutil` validation and network-script shell syntax passed |
| Host interface discovery | CoreWLAN helper returned the Wi-Fi interface without changing the radio |
| Debian payload | Guest components rebuilt and a browser-free payload with 102 manifest entries staged; broker FFmpeg dependencies resolve inside its staged private library directory |
| Repository checks | 24 tests passed on Linux; 24 ran on macOS with two Linux-only evdev tests skipped; source syntax, privacy and pinned-submodule checks passed |
| Documentation | Relative links resolve and all 22 shell blocks passed Bash syntax checks |

Firefox-specific launcher, preload library and preferences were subsequently excluded from the package and the guest payload was regenerated. The earlier preload compilation result above is historical, not a shipped component. General VA-API integration remains; sandboxed-browser hardware decoding is not guaranteed without application-specific work outside this package. The working reference installation was left unchanged.

## Controlled reference migration and unified application

After explicit authorization on 2026-09-09, a permanent checkout was built on the host and the matching Debian payload was installed into the existing guest, preserving its VM identity, networking, credentials and password fallback. Private receipts back up changed files. Core guest services are active; the rebuilt broker and VA driver passed 90-frame H.264 and VP9 `vaapi-copy` checks against the original host. Existing matching DKMS installations were retained. Browser compatibility glue remains a local customization outside the package.

The three Workbench apps were then replaced in source by services linked directly into the CocoaSpice executable. The new Ashacky.app compiled, assembled and passed strict signatures, all 54 Mach-O dependency closures (including the background launcher), relocation, QEMU feature queries, silent audio and the Metal shader pipeline check. At that stage its setup window and same-user permission-status socket ran under the single `local.ashacky.host` identity. The installation plan no longer emits separate permission-app jobs. The unified services scanned Wi-Fi, enumerated paired Bluetooth/audio devices and received a camera frame without saving it. A login-ancestry test revealed that direct Python launches lose TCC attribution. The bundled launcher now uses the documented NSWorkspace launch API; it passed clean-stop and forced-crash propagation checks without leaving an app behind. Final-build permissions remained allowed after restarting through the launchd/Python/LaunchServices chain. The permission window was then closed; normal runtime opens only the Linux display. A VM boot through the unified app remains pending attended acceptance.

## Login failure review

The first attended host cutover failed before QEMU launched. Under launchd’s 0077 umask, creating the network subdirectory left its common parent at root-only mode 0700. The account already had the correct group membership. The watchdog restored the legacy runtime; its diagnostics now retain the failure across rollback.

The generated root wrapper explicitly creates the common parent as root and the integration group, mode 0750. An isolated macOS fixture tested all four possible first-service orders with a missing parent and umask 0077, then verified socket access as the dedicated user. The USB helper now takes a singleton lock before replacing its listener; a live helper test rejected a duplicate and a non-QEMU client without touching USB hardware, then stopped cleanly.

The permission window was removed. Native permission requests and their persisted grants passed a restart through launchd, Python and LaunchServices; the windowless setup and launcher exited together afterward. The frontend rebuilt and passed the 54-binary closure, signature, relocation, GPU shader and silent audio checks. The source check passes 26 tests on Linux; on macOS, two Linux-only checks are skipped. Child-service tests cover isolation, stop/restart and restart throttling. The plan now emits one user LaunchAgent and four root LaunchDaemons, each associated with Ashacky. Ordinary app opening requests the installed session job; its VM behavior still awaits the next attended login.

Three unused host codec/probe jobs and a detached old USB listener were retired with private recovery records. The superseded guest decoder socket was also retired. Restarting the retained broker exposed a directory mode error in the private migration script: `/etc/ashacky` had been created as 0700 instead of its manifest’s 0755. The mode and receipt were corrected, preserving the private device file’s 0600 mode. H.264 and VP9 `vaapi-copy` smoke tests then passed; the H.264 check uses the shipped four-thread limit. UTM’s unused privacy grants were reset by bundle ID and their removal verified. The retained legacy services and their remaining grants stay available until the next host cutover passes acceptance.

## Working reference launch and retirement of legacy services

On 2026-09-09 the owner confirmed that the repo-built app opens a working Linux desktop after the Metal/SPICE scanout correction. The client now enables `gl-scanout` explicitly because Metal consumes IOSurface frames without EGL. A relocated-bundle check exercises the same connection factory without opening a socket. Live logs show GL scanout enabled and frames presented; GNOME and the six core integration services are active with no failed system units.

Host control and SessionSync were subsequently linked into the main Ashacky executable. The app owns their timers, listeners and guest command lifetime; QEMU resumes only after its authenticated control endpoint is ready. The generated root power helper now accepts the main app's exact executable path. The standalone helper executables are removed during assembly. The rebuilt app passed strict signatures, all 52 Mach-O dependency closures, relocation, the Metal shader pipeline and silent audio checks. Native checks cover reverse-lock transitions, command termination, rejected late launches, token validation and rejection of unclean shutdown confirmation. No test performs an actual host power action or biometric authentication.

A live restart verified that the control endpoint reports `local.ashacky.host`, the restricted power check succeeds, and Location/Bluetooth/Camera/Microphone permissions remain allowed. There are no separate SessionSync or LinuxHostControl processes. The USB helper now reads root-controlled console ownership using `stat`, avoiding a cached SystemConfiguration connection in forked workers, and closes the inherited singleton-lock descriptor in each worker. A subsequent clean stop left no session SSH processes or USB workers behind. After removing obsolete user script/runtime directories, the active self-contained qcow2 disk and firmware were renamed into the private Ashacky directory on the same filesystem, preserving their inodes and VM identity. The next launch passed with exactly four new USB workers, all device grants intact and the guest desktop/services active. Historical disk images and user files were not deleted.

With the owner's authorization, the retired LinuxHost apps, installed helper closure, jobs and automatic rollback watcher were removed or disabled. Cleanup targeted only this account's obsolete permissions: Workbench app grants, the old Python Bluetooth/Camera/Microphone records, the old WiFiWorkbench location record, and seven Local Network rules for retired Workbench/control/sync copies. Native API readback confirms that no targeted Local Network rule remains. All other account and application records were preserved. The existing Python Local Network entry is retained because the repository still uses Python for VM supervision and transport. Private maintenance receipts record exact paths and changes; no blanket privacy or background-task reset was used.

## Review of the working host changes

A subsequent review imported the working host changes into the source checkout and repeated the non-disruptive runtime checks. The existing app passes the 52 Mach-O dependency closures, strict signatures, relocation, Metal/SPICE scanout, session-transition/command-lifetime tests and silent audio checks. Live readback confirms the Ashacky control identity, allowed device grants and the restricted power helper's non-destructive check. The review did not rebuild or restart the live runtime.

The new PNG icon exposed a source-checker error: binary assets were being decoded as text. The checker now validates the specific 1024×1024 RGBA icon and rejects ancillary EXIF/text metadata, damaged chunks and trailing data. EXIF was removed without changing compressed pixel data. The source suite passes 28 tests on Linux; macOS skips two Linux-only checks. An isolated macOS build generated and decoded all ten ICNS sizes. The guides now consistently use the supervisor-owned H.264 process and explain updating the power-helper executable allowlist without regenerating VM credentials or identity. Fresh-account acceptance remains unverified.

## Battery event-path refinement

The reference battery path had two independent five-second polls: the guest agent's host status request and the battery feeder's cache read. The replacement uses native IOKit power notifications, a versioned SPICE/virtio-serial status channel and inotify on atomic guest cache replacement. A ten-second heartbeat maintains the existing watchdog; an older host continues to use the five-second RPC fallback.

The updated frontend built in a temporary output directory while the desktop stayed running. Assembly, strict signatures, all 52 Mach-O dependency closures, relocation, Metal/SPICE, session/authentication boundaries and silent audio checks passed. The source suite passes 32 tests on Linux; macOS skips three Linux-only checks. Tests cover fragmented/bounded telemetry messages, protocol versions, immediate inotify delivery for atomic AC/battery snapshots, directory replacement and stale/invalid telemetry. Native checks confirm status framing and exclusion of the control token.

The updated guest agent and feeder were installed with private recovery records. After an authorized clean VM stop, the tested frontend replaced the existing app at its canonical path and QEMU restarted with the status channel. Live readback confirms `statusTransport: virtio-serial`, both guest units active, successful channel reconnection after restarting the guest agent, continued heartbeat updates and the restricted power helper's non-destructive check. The owner then confirmed that the GNOME battery icon changes immediately when connecting/disconnecting power. This is an attended responsiveness check, not a numerical latency benchmark. The temporary previous app and review build were removed after acceptance; private installation hashes and test results remain recorded.

## Audio event-path refinement

The native CoreAudio observer and updated frontend compile. The assembled app passes strict signatures, all 52 Mach-O closures, relocation, Metal/SPICE, session-control boundaries and silent audio checks. The source suite passes 40 tests on Linux. Guest checks cover fragmented Unicode JSON streams, bounded framing, node/client filtering, metadata deletion, stale/legacy host revisions, mute/zero-volume preservation, inactive-session retries and child/monitor cleanup. A passive ten-second check against the real PipeWire server performed one initial reconciliation and no repeated graph reads; it changed no routes.

The new guest client and companion module were installed with private recovery records, followed by an authorized clean VM stop and replacement of the frontend at its canonical path. Live checks confirm `audioRevision` in the virtio-serial status cache, `Audio host updates: events`, successful user-service restart, one persistent PipeWire monitor and the non-destructive power-helper check. There were no automatic audio-service restarts or compatibility-mode transitions during acceptance.

Linux-to-host output selection changed to each of two endpoints in approximately 90 ms, and the owner heard the test tone through the external output. After the frontend update, the owner confirmed prompt output removal/reappearance and working sound following disconnect/reconnect. The guest trace showed the output list changing about 50 ms after its revision token arrived and default selection settling about 100 ms after the token; this does not measure Bluetooth connection time. Input-device hotplug has not yet been attended-tested with this change. The owner observed separate macOS/Linux volume levels and explicitly deferred volume synchronization. Temporary previous-app and review outputs were removed after acceptance; private installation hashes and results remain recorded.

## Bluetooth event-path refinement

The updated frontend compiles with the existing macOS SDK. Four focused guest checks cover malformed/legacy/stale revision capabilities, atomic cache notifications without repeated heartbeat queries, directory replacement, watcher cleanup, fallback/recovery, and radio heartbeat delivery. A private D-Bus test exercises real ObjectManager enumeration and property signals, a revision arriving during an in-flight snapshot, discovery start/stop and suppression of requests while the host is inactive. These automated checks use no live system bus or physical Bluetooth devices. Cached radio-state renewal is allowed only for its validated, active revision, preserving the existing kernel watchdog.

The assembled frontend passes strict signatures, all 52 Mach-O dependency closures, relocation, Metal/SPICE, session-control boundaries and silent audio checks. The source suite passes 44 tests on Linux; macOS skips eight Linux-only integration checks. The updated guest facade and companion are installed with private backups and receipt hashes. A service restart against the previous frontend enters compatibility mode and preserves device enumeration and the available, powered radio. After an authorized clean VM stop, the tested frontend replaced the app at its canonical path. Live readback confirms `bluetoothRevision` on the virtio-serial channel, native event mode and a successful facade restart. The virtual radio remains available past its watchdog deadline and for several minutes of idle runtime. The existing audio service and persistent PipeWire monitor remain healthy, and the restricted power-helper check passes. The owner confirmed fast Bluetooth disconnect/reconnect updates. The guest trace recorded `Connected: false` about 50 ms after the disconnect revision and `Connected: true` about 61 ms after the reconnect revision, with discovery inactive. These timings start at guest receipt of the revision, not at physical link establishment. Fresh pairing, physical radio-power changes and discovery with new hardware were not retested. Discovery start/stop was checked on the private D-Bus only. After acceptance, the temporary previous app and frontend review objects were removed; private receipts retain installed hashes and results. No new kernel ABI, Bluetooth payload transport or radio-power mutation has been introduced.

The pre-commit guide review added the Linux check dependencies (`python3-gi` and the private D-Bus daemon), explicit `patchelf` staging prerequisites and a frontend-only temporary build recipe. The recipe's PATH/pkg-config setup and CLI options were checked through a non-interactive host SSH shell; the candidate's actual compilation/assembly/relocation results are above. A fresh isolated Debian payload, using the previously unpacked `patchelf` for the audit, contains all three Bluetooth scripts with matching source hashes, root ownership in its manifest, 0644 file modes and 0755 parent-directory modes. Its Bluetooth system-service action is present. The temporary payload was removed without installing it. All 26 documented shell blocks pass Bash syntax checks, and all 24 relative documentation links, including section anchors, resolve.

## Wi-Fi kernel request notifications

The guest-only update adds an advertised request-readiness capability to the cfg80211 module and blocking `poll()` to the existing root bridge. The module compiled against the current Debian ARM64 headers and rebuilt/installed through the existing DKMS registration. The updated bridge first ran against the old loaded module in compatibility mode. Activation preserved the existing profiles, management NIC, host app and permissions; private recovery files and installed hashes were recorded.

The first live reload used `modprobe -r`, which also unloaded `cfg80211`. The existing supplicant then failed to reacquire its interface. Restarting the supplicant and cycling only the virtual interface's NetworkManager managed state restored the connection. A subsequent reload using `rmmod linuxhost_wifi`, preserving `cfg80211`, reconnected automatically without those recovery steps. DEVELOPMENT.md documents that narrower procedure. No kernel or networking configuration workaround was added.

A live diagnostic on the actual misc device verified an idle wait, a request arriving during that wait, no repeated readiness after reading it, recovery of pending work through a new descriptor, preservation of readiness after a failed userspace copy, and cancellation. Restarting the production bridge with a real pending scan published fresh host results. Normal scans and the restored connection continued afterward. A quiet ten-second sample recorded three voluntary context switches, with the bridge sleeping in kernel `poll()` at both endpoints; this is an idle sample, not a throughput benchmark. Full host disconnection and radio-power toggles were not repeated. The separate one-second radio and five-second signal refreshes remain.

The owner subsequently confirmed that nearby networks appear in GNOME and the connection works. All 49 source tests pass on Linux, including five focused request tests. Four new Linux-only readiness/socket checks skip on macOS; they use pipes and private socket fixtures and never contact the real radio. The isolated Debian staging check includes the matching bridge and module source hashes, root-owned manifest entries, 0644 file/0755 parent modes and the existing DKMS/service actions. The temporary payload was removed without installing it. The guide review checked all 27 documented shell blocks with Bash syntax validation and resolved all 25 relative links/anchors.

## Wi-Fi radio event refinement

The candidate frontend adds an opaque Wi-Fi revision to the existing status channel. A passive CoreWLAN probe registered event callbacks with the existing ad-hoc signing style. With Ethernet verified as the macOS default route, an off/on test delivered power, link, SSID and BSSID notifications and restored Wi-Fi. Adding Apple's documented `com.apple.wifi.events` entitlement to that isolated probe instead caused AMFI to reject it as a restricted entitlement; Ashacky's entitlement file was left unchanged. No signal-quality callback was observed in that test, so the five-second signal refresh remains.

The frontend compiled and its assembled candidate passed strict signatures, all 52 Mach-O dependency closures, relocation, the shader pipeline and silent audio checks. All 52 source tests pass on Linux. Three added radio tests cover capability freshness, startup/echo handling and an integration fixture with real GIO file events, rfkill-shaped pipe events, asynchronous Unix RPC, delayed replies, newer choices, inactive sessions and uncertain operation failure. The fixture touches no physical radio. The status-message check also verifies inclusion/omission of the Wi-Fi capability without exposing the control token.

The two guest radio files were installed with private recovery records and matching source/receipt hashes. Against the previous frontend, compatibility mode, service startup and Linux-to-host off/on control passed; the timed readbacks were about 0.58 and 0.38 seconds, and the existing connection returned. These timings include the diagnostic's readback interval. Isolated Debian staging includes both radio files with matching hashes and manifest permissions; its temporary output was removed.

The assembled frontend subsequently replaced the canonical app during an authorized clean VM stop, preserving the disk and firmware inodes. The guest booted with `wifiRevision`, and the radio service restarted in native event mode. Initial missing-socket errors recovered once the SSH forward appeared; no automatic service restart was needed. The restricted power-helper check passed without performing a host power action.

With Ethernet again verified as the host's default route, external `networksetup` power changes delivered new revisions and updated the guest radio. Power-off reached Linux about 0.30 seconds after the host command returned; power-on had already arrived by the first readback. Linux-to-host off/on readbacks took about 1.13 and 0.16 seconds, including diagnostic queries. The existing network reconnected about 15 seconds after power-on. A separate quiet 26-second sample saw three status writes with one unchanged revision: the RPC worker remained in its futex wait with zero context switches, while the main loop handled the status events. These are bounded functional samples, not latency guarantees.

The owner confirmed that GNOME disconnects quickly and reconnects more slowly. The attended connection attempt spent about 22 seconds in NetworkManager's configuration state, with one temporary permission rejection and three different-BSSID rejections before success. The SSID comparison had already passed for those BSSID failures, and the active NetworkManager profile did not explicitly pin a BSSID. The strict comparison predates this radio refinement, but the interaction with host autojoin/association timing remains to be diagnosed; it must not be removed by silently ignoring an explicit connection constraint. Radio event delivery is accepted with this connection-path limitation recorded. Guide review passed all 27 shell blocks and 26 relative links/anchors; the staged source tree passed the secret scan. The temporary previous app and probe/review outputs were removed after acceptance; private receipts retain the installed hashes, results and remaining limitation.

## Wi-Fi AP selection and link reports

An isolated host association probe requested a different compatible AP on the same network. CoreWLAN returned success but stayed on its preferred AP across repeated readbacks; explicit disassociation first did not change that outcome. The active Linux profile had no BSSID pin. Inspection of the [wpa_supplicant 2.10 source](https://w1.fi/releases/wpa_supplicant-2.10.tar.gz) confirmed that the driver's roam capability enables AP hints for automatic connections while preserving explicit pins. The update advertises that capability and implements host link reports rather than dropping all BSSID validation.

Five added source tests cover supported security and downgrade rejection, automatic versus pinned AP selection, generation-bound roam/loss reports, missing identity versus unavailable permission, post-scan identity races, and real inotify replacement/overflow/watch recovery. The source suite passed on Linux. The candidate module compiled against the running reference kernel. The separate documented kernel fixture used a renamed module, dummy NIC and network namespace to verify the advertised capability, an AP hint without a pin, initial association, roam/duplicate reports, malformed/security/generation rejection, explicit pin enforcement and link loss. It removed its temporary device/module afterward; no kernel warning was observed. This exercises the kernel ABI, not a physical roam.

The three guest scripts and DKMS source were installed with private originals and matching source/receipt hashes. A module-only reload preserved cfg80211 and the independent management NIC, then reconnected automatically with both services healthy and link capability value 3. The host app and VM were not restarted. With Ethernet verified as the host route, three rapid one-second off/on cycles reached a connected state in about 2.90, 3.70 and 2.36 seconds after power-on. All finished secured and unpinned with the same actual AP reported by host and guest, and none logged a different-BSSID rejection. The middle cycle logged three transient macOS operation failures; the diagnostic's stricter zero-retries assertion failed even though reconnection succeeded. The test restored Wi-Fi before exiting. A subsequent packet check explicitly bound to the virtual Wi-Fi interface passed, and installed source/receipt hashes still matched.

These bounded samples show the association mismatch is resolved for the tested automatic profile. They do not prove retry-free reconnection, an upper latency bound or physical AP roaming. The five-second signal/link reconciliation remains; removing it requires separate evidence that host quality/link notifications cover the necessary transitions.

Isolated Debian staging included the matching bridge, scan, link and event companions plus DKMS source, with verified hashes, root ownership, readable parent directories and the existing service/module actions. The temporary payload was removed without installing it. Guide review passed 28 shell blocks and resolved 30 relative links/anchors.

## Remaining validation limits

These checks establish functional build/staging reproduction with the recorded host SDK and existing Homebrew prerequisites, not byte-identical output or clean-machine installation. The isolated audit did not install or boot its outputs. The later controlled migration installed guest services and the PAM module path with password fallback; the reference host cutover now works, while full macOS logout/login after final cleanup and fresh-account acceptance remain pending. The cloud-init seed attachment, fresh-account boot, hardware/session behavior after parameterization, other distro adapters, and receipt-driven migration/uninstall still require attended acceptance. See [INSTALL.md](INSTALL.md).

The host codec compiles in Swift 5 mode with concurrency warnings that require attention before Swift 6 mode. The patched H.264 server emits a Swift pointer-conversion warning at its VideoToolbox hardware-property query, and CocoaSpice emits existing Objective-C warnings. The optional source shader rebuild remains unvalidated because the Metal compiler is absent; the normal release-asset build does not need it.
