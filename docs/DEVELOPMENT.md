# One checkout, one current build

For optional virtual FIDO2 updates, see [the matched host/guest activation and rollback procedure](FIDO2.md#attended-activation). Preserve its private host credential store and installation token; neither belongs in Git or generated guest payloads.

Ashacky keeps source in one checkout and generated dependencies, objects and coherent app bundles under its ignored `build/` directory. Git provides source history. There is no installed version store or active-version selector.

Use symlinks only where an OS integration point needs a path elsewhere. Root helpers and their dependencies must be installed into administrator-controlled locations. Guest kernel, PAM and VA libraries must be installed for the guest's ABI; a macOS symlink cannot install them into another OS. Generate these installed outputs from the checkout, record them and never maintain separate edited source copies.

Signed bundles are coherent current build outputs. Follow Apple's [code-signing resource rules](https://developer.apple.com/library/archive/technotes/tn2206/) when deciding what can be linked. Power-helper peer executable checks must follow intentional canonical paths; don't broaden authentication just to make a symlink work.

## Working on the source

Run `./ashacky check` before committing. It checks text/syntax, common private artifact patterns and focused protocol/configuration tests. It is a guard against common mistakes, not a complete secret scanner or source audit. Review the staged diff as well. Don't add VM disks, firmware variables, account configuration, SSH keys, radio profiles, device identities, browser data, logs, captures or hardware-test dumps.

`upstream/import-provenance.json` records selected historical inputs to the first source commit. Some files were subsequently parameterized, moved or replaced by patches; this ledger is not the current file layout.

Git submodule entries record exact upstream commits; `.gitmodules` records their URLs. `dependencies.json` defines patch order and source recipes, and `upstream/archives.lock.json` records the release assets that need checksums. Do not duplicate Git commit pins in a second lockfile. Ashacky patches live in `patches/`; existing UTM patches are read directly from `third_party/utm/patches/`.

The complete macOS build/staging commands and non-VM checks are in BUILD.md. PROVISIONING.md explains private plan generation and the agent-applied account/guest recipe. Staging produces review files and manifests, not evidence of an installation.

The normal macOS build also uses `upstream/utm-assets.lock.json` for the UTM release image and selected binary-component hashes. Review a release change together with its corresponding source pins and shader-interface hashes. Verify the public archive before updating component hashes, then repeat extraction, frontend compilation and the Metal shader check. Keep `build/upstream/utm` pristine; copy components to the generated runtime before modifying architectures, loader paths or signatures. A changed pin requires explicitly removing and regenerating this asset directory; do not weaken verification or substitute an unversioned installed UTM app.

After pulling a reviewed Ashacky update, run `git submodule sync --recursive` followed by `git submodule update --init --recursive --depth 1`. Do not use `--remote` for routine updates: it follows upstream branches rather than Ashacky's tested commits.

To intentionally update a dependency, fetch and check out the chosen commit in its submodule, then stage that submodule path in Ashacky. The source verifier uses the Git index pin, so staging makes the proposed pin available for testing before committing. Review upstream changes, refresh any affected patches, rebuild and validate before committing the new pin. Keep submodules clean, including nested submodules; put intended modifications in Ashacky's patches. Generated patched sources in `build/sources/` are disposable build inputs, never a second maintained copy. If a pin or patch changes, preparation refuses the stale generated directory; remove only that component's generated directory and rebuild after stopping affected runtime processes.

The source consolidation retains compatibility names such as `linuxhost`, `probe` and `workbench` socket names in interfaces and directories. They do not imply that discarded prototypes should run. Retire a compatibility name only with an explicit migration for every consumer.

## Updates and recovery

Before changing live checkout scripts or mapped binaries, stop affected applications/services and the VM cleanly. Check local changes, update the checkout, rebuild current outputs, refresh installed files atomically and perform guest/config migrations. Start and validate afterward. A plain live `git pull` can otherwise change scripts under running services.

Record installed paths/ownership/digests and original OS settings in private receipts. Back up state before irreversible data migrations. Source recovery uses Git plus rebuilds; a Git revert does not undo guest disk or configuration migrations. No collection of parallel installed runtime versions is required.

For instruction/build reviews, use isolated output directories and inspect the working installation read-only. A review alone does not authorize installing validation outputs, changing live services/login settings or restarting the VM. Temporary build-validation copies are not installed runtimes. Check that build destinations are not used by the running installation before writing them.

Fresh-account installation requires an explicit setup request. Missing acceptance results are limitations to record, not authorization to deploy. A second macOS account alone does not isolate system helpers or physical devices. When that test is requested, establish distinct helper identities and device/network isolation first. The reference migration and retirement are recorded in VALIDATION.md; another installation must make its own acceptance checks and preserve its VM and data.

### Updating battery telemetry

Install the updated `linuxhost-agent` and battery `feed.py` at their existing manifest paths, preserving root ownership and recording installed hashes. Restart their two guest units. They remain compatible with an older host through the status RPC fallback. Build and assemble the updated frontend, then replace the app during a clean session stop. Restart QEMU with the updated supervisor arguments to add `org.ashacky.status`; rebuilding the app alone cannot add a port to an already running VM. This update does not change the battery module ABI or credentials and adds no macOS permission requirement.

Verify `/run/linuxhost/status.json` reports `statusTransport: virtio-serial`. Disconnect/reconnect power and observe UPower, then restart the guest agent to check reconnection. The heartbeat normally refreshes the cache every ten seconds while awake; it is a health check, not the charger-change delivery interval. Record the observed latency; build checks alone do not establish live charger behavior.

Read-only guest diagnostics:

```sh
python3 -m json.tool /run/linuxhost/status.json
ls -l /dev/virtio-ports/org.ashacky.status
systemctl status linuxhost-agent.service linuxhost-battery.service --no-pager
journalctl -u linuxhost-agent.service -u linuxhost-battery.service -b --no-pager
upower -e
```

`rpc-poll` means compatibility mode: check the port exists and that the updated Ashacky frontend is running; changing QEMU arguments requires a VM restart. If the port exists but compatibility mode persists, inspect the agent journal and the private host display log for status-port connection errors. A stale cache points to the host/channel/agent path. A fresh cache with incorrect Linux battery state points to the feeder or module; inspect UPower and `/sys/class/power_supply/`. The driver's `state` file is write-only and is not a diagnostic read interface. Do not inject fabricated power states into a live desktop.

The agent abandons an event stream after 25 seconds without data and attempts the RPC fallback. The feeder accepts cache snapshots up to 20 seconds old; the kernel watchdog marks telemetry unavailable after roughly 30–35 seconds without a valid write. Suspend can pause these timers; verify recovery on wake separately. Loss of telemetry must not be presented as a new charger event.

### Updating launcher exit observation

Use the [frontend-only validation recipe](BUILD.md#frontend-only-validation-while-the-vm-runs). `check-runtime.py` exercises the candidate launcher against a temporary, windowless test app with no device services, VM connection, entitlements or permission requests. It checks fast clean exits, exits without a clean receipt, forced crashes, SIGTERM/SIGINT, idle survival, stopping before application launch finishes, refusal to quit, repeated stop signals and launch failure. A mismatched private runtime directory is rejected before opening an app. The test registration, processes and temporary files are cleaned up afterward. Run it from an active macOS GUI account; Command Line Tools suffice.

For a focused rerun against an already built candidate:

```sh
python3 tools/check-launcher.py --launcher build/frontend-review/Ashacky.app/Contents/MacOS/AshackyLauncher
```

Keep the real app, launcher and supervisor running until the candidate passes. Switch the signed bundle at the installation's existing canonical path during a clean VM stop, as described in BUILD.md; do not modify a running bundle or launch the Ashacky executable directly. There is no guest payload, new job, QEMU setting or permission change for this refinement. After restart, check one live launcher, the expected frontend launch record and working desktop/device integration. Retire the temporary previous app only after acceptance and record the installed executable hashes. The three-second shutdown grace remains; no timer runs merely to check that the app is still alive.

### Updating audio event delivery

Install `audio_pipewire.py`, `audio_events.py` and `audio_volume.py` together from the guest manifest, then restart the guest user's `linuxhost-audio-pipewire.service`. The client requires the distro's PyGObject/GIO bindings (Debian `python3-gi`, already needed by the Bluetooth facade) and PipeWire's `pw-dump`, `pw-loopback`, `pw-cli` and `wpctl`. Both companions must be in the same installed directory as the audio script. For volume support, also update `device_service.py` and restart `linuxhost-probe-management.service` to install its bounded output-volume request validator. Record new and replaced files in the private receipt.

Build and validate the updated frontend before replacing the running app during a clean session stop. Audio reuses `org.ashacky.status`; no additional QEMU port, kernel module or permission is introduced. A host without `audioRevision` uses compatibility polling for the device list. A host with device events but without `outputVolume` retains software gain; updating the guest alone does not enable linked volume.

In the guest, check `audioRevision` in `/run/linuxhost/status.json` and `journalctl --user -u linuxhost-audio-pipewire.service -b`. Healthy native operation logs `Audio host updates: events`; `compatibility polling` means the revision is missing or stale. Confirm one long-lived `pw-dump -m -N` child, with metadata queries only on changes. Check connection/removal of an audio device, default output and input changes in both directions, mute/zero-volume preservation, and user-service restart. Verify actual sound after selection; a correct device list alone does not establish audio routing. Document any fallback or hardware checks that remain untested.

For linked volume, first run `python3 -m unittest discover -s tests -p 'test_audio*.py'`. The integration test starts a private PipeWire daemon and generated PCM streams; it checks the cubic slider mapping, unity software gain and mute without opening physical audio devices or the desktop server. It requires the PipeWire command-line tools (including `pw-cat` and `pw-link`) and skips when they are absent. No full Xcode or new audio dependency is needed on macOS.

After the frontend cutover, use the host keyboard to change output volume and mute, then change GNOME's slider and mute. Check both readbacks and audible behavior, zero volume, rapid alternating changes, service restart and switching outputs. Confirm the public output's `softVolumes` stays at unity for a centered balance while `channelVolumes` reflects the host scalar cubed. Microphone mute and zero gain must remain unchanged. Verify an unsupported/fixed-volume output retains local software gain when such hardware is available. Do not treat a simulated test as physical-device acceptance. Global output levels are limited to 100%; per-application software gain remains separate. See [architecture](ARCHITECTURE.md#audio-management-events) for conflict handling and temporary attenuation during control failure.

### Updating an installation with standalone session helpers

The frontend contains host control and macOS screen-lock handling. Updating only the app leaves an old power helper authorizing the removed `LinuxHostControl` executable, so coordinated power actions will fail. During a scheduled clean stop:

1. Preserve the installed private configuration, disk/NVRAM, VM UUID, MACs, disk serial, SSH key and control token. Update the existing private `helper-build.json` so `powerClients` contains the canonical `Ashacky.app/Contents/MacOS/Ashacky` path. Do not replace these files with identities from a freshly generated plan.
2. Update the checkout and pinned submodules. Rebuild the frontend and host helpers, assemble the app and privileged closure, then run the runtime checks in BUILD.md. Refresh the root-owned helpers from that closure and restart the affected root jobs. Root helpers must not execute from the checkout.
3. Keep only the generated session LaunchAgent and four root jobs for this installation. Remove any recorded standalone control, SessionSync, forwarding or H.264 jobs; their functions belong to the app or supervisor now. Preserve the root `service.sh` wrapper that initializes runtime-directory permissions. Ensure the session environment contains `LINUXHOST_CONTROL_CONFIG`; the command itself uses the absolute configured Python. SSH settings are optional administration only.
4. Start the VM and verify the authenticated control status identifies `local.ashacky.host`. Use the non-destructive `hostctl powercheck` to verify the rebuilt privileged helper accepts the app. Confirm the four device grants, picture/input/audio, USB workers and lock/session behavior before retiring old application permissions. Record the new installed hashes and acceptance results.

The Metal frontend explicitly enables SPICE `gl-scanout`: an EGL-disabled client otherwise does not advertise the IOSurface path. `check-runtime.py` verifies the connection factory without opening a display connection. If QEMU is running but the window is black after an update, check the display log and this capability before changing the guest graphics stack.

### Updating Bluetooth event delivery

Install `bluetooth_bluez.py` and its companion `bluetooth_events.py` together at their guest-manifest paths, preserving root ownership and recording the files in the private receipt. Keep `bluetooth_management.py` alongside them. Restart `linuxhost-probe-bluetooth.service`; it remains compatible with the older frontend through its two-second refresh. The distro's PyGObject/GIO dependency is unchanged (Debian `python3-gi`); use the distro Python selected by the system unit. Test API changes on a private D-Bus before touching the system service; never introduce a second `org.bluez` owner on the live system bus.

Build, assemble and validate a temporary frontend using [the frontend-only recipe](BUILD.md#frontend-only-validation-while-the-vm-runs), then switch it at a clean, attended VM stop. Preserve its bundle/signing identity and native Bluetooth permission. No new app, LaunchAgent, port, QEMU topology or kernel module is required once `org.ashacky.status` is installed.

Check `bluetoothRevision` in `/run/linuxhost/status.json` and `journalctl -u linuxhost-probe-bluetooth.service -b`. Healthy operation logs `Bluetooth host updates: events (60-second reconciliation)`. Read the latest mode: a brief `compatibility polling` message at startup is expected before the first fresh revision arrives; persistent fallback means a missing/stale revision or unavailable host listener registration. The ten-second heartbeat keeps the radio watchdog alive; verify GNOME/rfkill stays available for at least a minute with no device action. The 60-second snapshot is a recovery check, not the normal connection-update latency.

With an owner-selected device, check disconnect/reconnect from the device and from GNOME, state changes in both Settings and the top-bar menu, discovery start/stop, and facade restart. Verify that host input/audio still works afterward. Radio changes made on macOS should appear in Linux; the facade does not add host radio power control. Test pairing with a spare device when available and keep any untested cases explicit. Do not disconnect the user's only pointer. Forgetting a device outside Ashacky can take until the next reconciliation to update its pairing state. A cached discovery entry may remain visible as unpaired until a later scan replaces the discovery cache.

The command-completion wait still checks operation status every half second only during a bounded pair/connect/disconnect attempt. Do not mistake those checks or an open discovery session for the removed idle poll. An active scan still holds the serialized management RPC until the bounded inquiry finishes; measure ordinary connection latency with discovery closed, and record the separate delay during discovery. Check the private host log for permission/registration errors before changing permissions; do not reset TCC or re-enable stock BlueZ to work around a stale cache.


For read-only diagnosis, inspect the status cache, the latest journal mode and these system units:

```sh
python3 -m json.tool /run/linuxhost/status.json
systemctl status linuxhost-agent.service linuxhost-probe-management.service linuxhost-probe-bluetooth.service --no-pager
journalctl -u linuxhost-probe-bluetooth.service -b --no-pager
```

If the cache is stale or still reports `rpc-poll`, follow the battery channel diagnostics above. If it is fresh and has `bluetoothRevision` but the facade fails, check the companion files and `python3-gi`, root ownership and directory traversal permissions, and the management service/private control broker before changing macOS permissions. The kernel's Bluetooth rfkill `hard` flag marks backend unavailability; it can expire roughly 15–16 seconds after its last valid feed. The event consumer rejects a revision after 25 seconds without fresh status. These are different health checks: no host heartbeat must not be presented as a deliberate radio-off event. Fresh status plus an unavailable radio points to the facade/module feed; read the Bluetooth entry under `/sys/class/rfkill/` or use the distro's `rfkill` utility.

For recovery, restore only the recorded Bluetooth guest files as a matching set and restart the facade; an older guest also works with the updated frontend. Reverting the Git checkout alone does not restore installed `/opt` files. Any app recovery requires another clean session stop before restoring/rebuilding the canonical bundle. Keep the existing VM, identities, configuration, BlueZ ownership and macOS grants intact.

### Updating Wi-Fi request delivery

Build the candidate module against the intended guest kernel using the guest build recipe, and run `./ashacky check`. The focused tests use private sockets and real Linux pipe readiness; they do not load a module or contact the host. Preserve the installed bridge, DKMS source/build artifacts and receipt before an authorized live update. Install the payload's `wifi_bridge.py` and `linuxhost-wifi` source at their existing manifest paths, root-owned, and record their hashes. No frontend rebuild, status-channel change or macOS permission is needed. The updated bridge can first be restarted against the old module to verify its bounded compatibility path.

For an existing `linuxhost-wifi/0.1.0` registration, rebuild with `dkms build -m linuxhost-wifi -v 0.1.0 -k TARGET_KERNEL --force` and install with the corresponding `dkms install ... --force`; run `depmod -a TARGET_KERNEL`. Do not add a duplicate registration or install the audit `.ko` directly. Rebuild for each intended kernel. The loaded module remains old until a reload or guest reboot.

A live reload briefly removes the virtual Wi-Fi interface. First verify the generated VM UUID and lower-NIC MAC as `probe-devices.py` does, and verify management access uses the independent shared NIC. Check the host's actual network route too: guest management access does not prove the host has Ethernet. Wait for outstanding connection work to finish, then stop only `linuxhost-probe-wifi.service` and `linuxhost-probe-wifi-power.service`. Use `rmmod linuxhost_wifi` without force so `cfg80211` stays loaded, then `modprobe linuxhost_wifi lowerdev=VALIDATED_INTERFACE` and restart those two units. Do not toggle host Wi-Fi or restart all virtual-device services as part of this reload. Alternatively, activate the installed module at the next planned guest boot.

Avoid `modprobe -r linuxhost_wifi` here: it can also unload unused dependencies. In the reference update it removed `cfg80211`, leaving the existing Wi-Fi supplicant unable to reacquire its interface. Restarting `wpa_supplicant.service` and cycling only the virtual interface's NetworkManager managed state restored it. Preserve the independent management NIC and existing profiles; do not reset networking or credentials to recover this condition.

Read-only guest diagnostics:

```sh
uname -r
dkms status
systemctl status linuxhost-probe-wifi.service linuxhost-probe-wifi-power.service --no-pager
journalctl -u linuxhost-probe-wifi.service -b --no-pager
nmcli -g GENERAL.STATE,GENERAL.REASON device show lhwifi0
```

The latest startup should say `Wi-Fi request delivery: kernel notifications`. Persistent `compatibility polling` means the loaded module lacks the advertised readiness bit; check the running kernel, installed DKMS version and whether the module was actually reloaded. Host permissions do not determine this mode. `POLLERR`, `POLLHUP` or an invalid descriptor ends the bridge for systemd recovery. A successful mode check alone does not prove host scanning or association: verify fresh scan results, the existing connection and restart with a pending request. Never print connection ioctl payloads, network profiles or credentials in diagnostics.

The five-second signal refresh and independent 45/90-second request timeouts remain. Radio synchronization uses the event path below, with one-second compatibility refresh for older/stale frontends. Requests also wait while the bridge performs a synchronous host operation. Check idle blocking with discovery closed; repeated scans requested by NetworkManager are real work, not an idle-loop regression.

Recovery restores the recorded bridge and DKMS source, rebuilds/installs that source for the intended kernel and reloads it with the same procedure. Update the private receipt to match. Restoring Git alone leaves installed code and the loaded module unchanged; keep the existing VM, host app and permissions intact.

### Updating Wi-Fi radio synchronization

Install the payload's `wifi_power.py` and `wifi_events.py` together, preserving root ownership, 0644 file modes and 0755 parent directories. Record replaced/new files in the private receipt and restart only `linuxhost-probe-wifi-power.service`. It needs PyGObject/GIO, already required for Bluetooth and audio. The kernel rfkill ABI and five-second signal refresh are unchanged; no module reload is needed. An older frontend retains one-second host-status compatibility refresh, while Linux radio changes already use kernel events.

Build/assemble/check the frontend in a temporary directory using BUILD.md. Do not add `com.apple.wifi.events` to the ad-hoc signature; the reference probe was rejected with that restricted entitlement. Replace the canonical app during a clean session stop, then restart through the existing session job and LaunchServices. No new QEMU port, macOS permission app, profile or credential is introduced.

Check `wifiRevision` in `/run/linuxhost/status.json` and the latest `journalctl -u linuxhost-probe-wifi-power.service -b` mode: `Wi-Fi radio host updates: events`. The scan bridge separately reports `Wi-Fi request delivery: kernel notifications`; that message alone does not verify host radio events. Missing or older-than-25-second status, absent capability or failed registration retains the radio compatibility refresh. CoreWLAN interruption removes the capability until another callback arrives; permanent invalidation requires an app restart. Check the host log and current signature before changing permissions.

The status channel can become ready before the private control broker's Wi-Fi socket. A brief `FileNotFoundError` at boot followed by `Wi-Fi radio synchronization recovered` is expected in that ordering; the client retries without restarting the service. Persistent failures require checking `ashacky-control.service` and its authenticated port. A manual VM reboot alone does not activate a staged frontend: verify the canonical app was replaced during its clean stop and that the new revision capability actually appears afterward.

With the owner's test connection ready, verify macOS actually routes over Ethernet before toggling Wi-Fi. The guest's shared management NIC alone does not establish that. Check both directions: GNOME's switch must change host power, and a host power change must update the guest without sending an echo command back. Restore Wi-Fi and the existing connection in a bounded recovery path even when a test fails. Verify service restart, unchanged-heartbeat idle behavior, and delayed-reply/rapid-toggle handling. Fixtures cover these races without a real radio; physical acceptance is separate. Do not log SSIDs, BSSIDs or credentials.

A host RPC may wait behind an active scan, so events cannot remove that existing serialization delay. The worker keeps the event loop responsive during the bounded RPC. Failed reads retry after one second; uncertain mutations are not automatically repeated. Startup or a host session change first reconciles actual host state. A broken/removed rfkill channel exits for systemd recovery rather than pretending that the user turned Wi-Fi off.

Measure radio-state delivery separately from NetworkManager association. The [AP-selection update](#updating-wi-fi-ap-selection-and-link-reports) addresses automatic connections failing when macOS chooses a different compatible BSSID. An explicit BSSID constraint still applies. Inspect sanitized connection results before blaming notification delivery; do not dump network profiles or remove the pin check to conceal a failure. A transient permission error also needs current API readback before requesting new grants.

For guest recovery, stop the radio service, restore the recorded previous files/receipt and restart it. The old guest works with the updated frontend. App recovery requires another clean session stop before restoring/rebuilding the canonical bundle. Preserve the existing VM, networking, credentials and grants.

### Updating Wi-Fi AP selection and link reports

Install `wifi_bridge.py`, `wifi_scan.py` and `wifi_link.py` together from the payload, with their existing `wifi_events.py` dependency. Preserve manifest root ownership, 0644 files and 0755 parent directories. The new companion is required even before the new module is loaded. Record originals and installed hashes in the private receipt. Rebuild/install the matching `linuxhost-wifi` DKMS source and activate it using the [module reload procedure](#updating-wi-fi-request-delivery) above, or at the next guest boot. This update needs no host rebuild or new permission; prompt link updates reuse the existing `wifiRevision` status channel.

The bridge should log both `Wi-Fi request delivery: kernel notifications` and `Wi-Fi association: host BSS selection and link reports`. `LH_GET_CAPABILITIES` returns bits 0 and 1 (value 3); restarting Python alone cannot add the new bit to an old loaded module. An older module still handles existing requests but does not advertise host AP selection or receive roam reports. Follow the source fixture checks with the optional [isolated kernel check](BUILD.md#optional-wi-fi-kernel-link-check) before a live module update.

For physical acceptance, use the existing unpinned profile and a verified independent management/host Ethernet route. Check GNOME off/on reconnection, agreement between the actual host AP and Linux's associated AP, and packet flow bound to the virtual Wi-Fi interface. Compare identities in memory and record only the comparison result. A correct icon or management-NIC ping does not establish the Wi-Fi packet path. Record association timing separately from radio-power delivery and include transient failures, even if an eventual retry succeeds. Preserve explicit BSSID pins; if macOS ignores one, the connection must fail visibly.

When suitable multi-AP hardware is available, verify a real host roam updates Linux without a disconnect, and that host network changes/loss are reported correctly. Synthetic cfg80211 tests do not replace this check. Missing identity uses a one-second confirmation deadline; missing Location authorization must not cause a false disconnect. The existing five-second signal refresh reconciles missed events or stale capabilities. Event delivery cannot bypass synchronous host scan/association time. The supported security contract remains open Wi-Fi or WPA2-PSK/CCMP, without enterprise authentication, SAE or required PMF.

Recovery restores the matching bridge/scan/link/dependency files, DKMS source and private receipt, then rebuilds and reloads the previous module using the same procedure. Remove a newly introduced companion only if the recovery record says it was absent before. Keep the host app, profiles and permission grants intact.

### Updating trackpad input waits

Run `./ashacky check` and stage the guest payload. Preserve the installed `/opt/linuxhost-probe/trackpad/trackpad.py` and its receipt before replacing the script, root-owned 0644 under 0755 parent directories. Record its installed hash. The Python `evdev` dependency, uinput device properties, systemd unit, SPICE port and host app are unchanged; no compilation or VM restart is needed.

Do not start a second consumer on `org.linuxhost.input`: competing reads would steal touch frames. Use private sockets and a recording-only uinput substitute for candidate transport tests. A passive observer may read the existing evdev device without grabbing it, recording only aggregate counts. Do not create an experimental desktop input device or send readiness/applied acknowledgements to the real host during isolated tests.

For an attended cutover, arrange a bounded restoration of the recorded script/receipt before restarting only `linuxhost-trackpad.service`. Check its PID, installed hash and exactly one matching virtual touchpad. Test pointer movement, clicks/drag, two-finger scrolling and pinch, plus releasing all contacts and leaving/re-entering the VM window where practical. Cancel restoration only after the candidate is healthy and the owner confirms input works. If input fails, restore the recorded script/receipt and restart that unit; stopping the service also lets the host's applied-frame gate expire into ordinary SPICE pointer fallback. Do not change that gate to conceal a guest failure.

Measure idle wakeups separately from actual touch traffic. One-second readiness and the unchanged host 250 ms touch-state heartbeat still cause legitimate activity. The removed behavior is the fixed 100 ms guest check. Held contacts/buttons must release at their own one-second deadline even if partial data arrives or replies are blocked. Channel/create failures retain the existing one-second reconnect delay. Sleep/wake and host-side heartbeat changes need separate acceptance; a source revert alone does not restore the installed script.

### Updating camera demand events

Run the source checks and [camera helper build](BUILD.md#guest-components), then stage the payload. Preserve the installed `/opt/linuxhost-probe/camera_demand.py`, `/opt/linuxhost-probe/camera-readers` and receipt before replacing them. The script is root-owned 0644; the compiled helper is root-owned 0755, with readable parent directories. Record both installed hashes. For a demand-only update after both sides already use the same camera transport, no new host app, permission, QEMU device, kernel module build or VM restart is needed. Changing the transport requires the [paired camera migration](#updating-camera-transport) below.

Check demand before restarting only `linuxhost-probe-camera.service`:

```sh
sudo /opt/linuxhost-probe/camera-readers /dev/video10 1
systemctl status linuxhost-probe-camera.service --no-pager
journalctl -u linuxhost-probe-camera.service -b --no-pager
```

The finite helper reads the initial usage event without reading frames. Defer replacement if `camera_reader_active` is true. A subscription failure means the installed v4l2loopback lacks the required private event; check its version/source instead of starting continuous capture. After restart, expect `camera_device_ready: true` with `host_capture_started: false`, one FFmpeg writer and one usage-helper child. The Python service and helper should block while idle; ordinary FFmpeg worker wakeups are separate from these waits.

For live acceptance, open a camera reader briefly, then close it. Use a discard sink for automated checks; never save frames or captures in the repo. Confirm positive `camera_stream_frames` followed by `reader_still_active: false`, no active reader afterward and a healthy service. The source fixtures check that the last frame is replaced by black and that idle stop, monitor exit and writer exit wake/reap the service. An intentional monitor-exit recovery check belongs only in a quiet test window: it should produce one systemd restart, reap both old children and return to idle until another reader opens. Record that deliberate restart separately from unexpected failures.

Preserve the existing capture/cancellation timeouts, host stream lease and child-cleanup deadlines. Idle waits must not prevent shutdown or leave capture running when demand tracking fails. For recovery, stop the camera unit, restore the recorded script/helper and receipt, then restart that unit. A source revert alone does not restore the installed binary. Keep the current host app, camera grant and v4l2loopback configuration intact.

### Updating the private control channel

This is a coordinated guest/frontend/QEMU topology update. Prepare it while the current session runs, preserving the installed token, disk/NVRAM, VM/NIC identity, signing identity and independent administrative access. Do not regenerate private configuration from a fresh installation plan. Build and validate a temporary frontend with the [frontend-only recipe](BUILD.md#frontend-only-validation-while-the-vm-runs). Changing `patches/cocoaspice.patch` requires regenerating only its inactive `build/sources/cocoaspice` input; never edit the pinned submodule or rebuild the active app.

Stage the guest manifest and inspect the control broker/agent, their units, the root-only udev rule, Bluetooth and Wi-Fi clients, and both GNOME extension modules plus metadata. Their paths/modes come from `guest/layout.json`. Preserve original installed files and receipt history. Back up and retire the obsolete `/usr/local/libexec/linuxhost-session-sync` only at paired cutover. The old client sockets and frontend remain necessary until cutover; do not switch these clients live before the new channel is available.

Immediately before the scheduled clean VM stop, install the matching guest payload without restarting its clients, and set only `hostSocket` in the existing private `/etc/linuxhost.json` to `/run/ashacky-control/host.sock`. Enable `ashacky-control.service` for the next boot and reload udev rules; the new port must resolve to a root:root 0600 node. Use the updated supervisor, which adds `org.ashacky.control` and the separate camera memory BAR; it starts no SSH forwards. Replace the app at its existing canonical path. Remove obsolete `ASHACKY_GUEST_SSH` and `guestSSH` fields from this installation's session environment/control config; remove unused `LINUXHOST_SESSION_CONFIG` and `ASHACKY_PYTHON` from the runtime job while preserving independent administrative keys/configuration. No root helper, new permission identity or macOS privacy reset is needed.

The new GNOME process must load the updated extension modules; toggling an already imported extension is not sufficient evidence of a reload. Check the selected GNOME user's `disable-lock-screen` is `false` and the `screensaver` shortcut list includes `'<Super>l'`; preserve other bindings and record corrections. The extension redirects GNOME's normal screen-shield lock method to `hostctl lock`. The host requires the installation token and active console account, then calls its native lock API. It never sends a guest unlock command or opens Debian's lock screen. The previous bidirectional lock watcher and its timers are removed. macOS must protect the session on sleep; keep its password/Touch ID policy intact.

After boot, verify `Private control channel authenticated` in the app and broker journals, all four private sockets, and `hostctl powercheck`. Confirm Wi-Fi discovery/connection, Bluetooth lists, audio endpoints and linked volume, and PAM Touch ID with password fallback. Test both GNOME's Lock menu and Command+L: macOS alone should lock, and authenticating there should return directly to Debian. Test host sleep/wake separately. A failed host lock request must notify the user and allow an explicit retry; it must not show a misleading locked state. A broker restart must fail pending calls, reconnect without changing authentication or duplicating an operation, and recover device reads. Inspect process arguments: there should be no runtime SSH forwards or session watcher.

Keep the prior app and installation receipt until acceptance passes. Rollback requires the previous app, supervisor/client/extension files and private socket configuration together; restoring only one side is insufficient. A rollback to bidirectional locking also needs its saved session helper. The channel transports small control messages. The camera memory migration is described below; codec-input transport isolation remains separate. Do not route camera frames through this port as a substitute.

### Updating camera transport

Prepare a matching frontend, supervisor and guest payload without changing the active app. The frontend includes `CameraBuffer.swift` and the common C `CameraMemory.c`; the Linux build compiles that same C source into `camera-memory.so`. Run the native slot-ownership and Swift/Python pixel checks through `check-runtime.py`, the Linux camera lifecycle/bounds tests and isolated guest staging. Generated test pixels do not exercise the physical camera or macOS permission.

Preserve the active app, supervisor source, camera service files, broker, video-memory setup and installation receipt. Stage `camera_shared.py`, `camera-memory.so`, `camera_demand.py`, `ashacky-control` and `linuxhost-video-memory` at their manifest paths. Retire the old `camera_bridge.py` during the paired update. The new broker creates a fourth root-only socket. Do not restart it or the camera service against the old frontend. A clean VM restart loads the matching app and QEMU's second shared-memory device; there is no new QEMU build, kernel module, app identity or privacy grant.

After boot, confirm one 64 MiB video BAR and one 8 MiB camera BAR. Both camera resource files must remain root-owned mode 0600; only the existing video resource is granted to the desktop. Verify the video-memory link still resolves to the 64 MiB BAR. Read a bounded set of camera frames into a discard sink, check dimensions/rate, close it and verify the host capture stopped, the shared slots cleared and the guest returned to black. Check an ordinary camera app, repeated demand, a stream longer than 30 seconds and recovery after losing the broker. Check that no runtime process invokes `probe-ssh`, `vm-ssh.py` or an SSH forward. Owner/agent administrative SSH may still be running separately.

Remove the obsolete camera forwarding child and unused SSH environment fields from the user job at the clean stop. Preserve owner administration and any keys still used for it. Rollback requires the previous app, supervisor/topology and guest files together, including the old video-memory selector and SSH camera forward/configuration; restoring only the camera Python file is insufficient. Keep paired recovery material until attended acceptance, then remove temporary runtime copies and retain the installation receipt.

### Updating Sidecar controls

Build and validate the frontend in a temporary output directory using BUILD.md.
The helper is `Contents/MacOS/AshackySidecar` inside the same app, not an installed
CLI, permission app or separate login service. `check-runtime.py` exercises the
helper supervisor with disposable native fixtures; it does not connect an iPad.
If the SPICE acknowledgement check reports an unqueued message, rebuild the
patched SPICE dependency in an unused build directory before assembling again.
Do not bypass that check or assume the old build prefix contains a prior live fix.

Run `python3 -m unittest discover -s tests -p 'test_sidecar.py'` in the guest.
Stage `guest/libexec/linuxhost-agent`, `guest/bin/hostctl`, and the extension's
`extension.js`, `displayMenu.js` and `metadata.json` at their manifest targets,
preserving root ownership and modes. Preserve the unchanged `hostLock.js` and
private configuration. The agent's bounded client concurrency is required so a
slow display request cannot hold up a lock request. Record originals and applied
hashes in the installation's private migration receipt.

Install the staged guest files without restarting the running guest services,
then stop the VM cleanly and switch the validated app before the next boot. Use
the existing supervisor and canonical app path. The supervisor owns QMP's single
client connection; a second client cannot issue shutdown commands there. For a
VM-only maintenance stop, first verify that no host power/logout action is pending,
then request guest `systemctl poweroff` and wait for the app and supervisor to exit.
`hostctl poweroff` also requests host shutdown and is unsuitable for this step.
The helper must inherit the Mac graphical bootstrap namespace; running
its discovery test from an ordinary SSH shell is not sufficient connection
validation. No new QEMU port, guest kernel module, GNOME Network Displays build or
runtime SSH service is required.

After restart, open Quick Settings → Displays. Confirm the iPad appears, connect
it, verify the second Linux output, disconnect and reconnect. Check that Display
Settings opens, menu disable/enable leaves session actions working, and a missing
or sleeping iPad produces a recoverable result. Verify Command+L while a display
operation is pending before claiming native lock/sleep cancellation acceptance.
For the three-output update, replace `host/session/session.py` during the same
clean stop as the app. QEMU must boot with `max_outputs=3`; restarting only the
frontend cannot add a scanout to a running VM. No guest file, module, identity or
permission change is needed. Preserve the matching previous app and supervisor
for rollback. The C layout fixture compares the supervisor's GPU capacity with
the common frontend/Sidecar policy, and the runtime check verifies the bundled
helper's capacity without touching hardware.

When committing prepared sources before activation, retain the installed
supervisor until the paired app switch. The host checkout may show that retained
file as a deliberate local modification; do not discard it merely to clean Git
status. Preserve original and candidate hashes in the private migration receipt.
A source commit does not authorize restarting or activating the candidate.

Test the built-in screen, wired monitor and Sidecar simultaneously. Disconnect
and reconnect each external screen while the other stays active, checking image,
pointer/keyboard, virtual-output identity and the capture-release shortcut. Test
both connection orders. The default host-driven guest layout remains horizontal;
do not claim persistence of a custom GNOME arrangement across hotplug. Lid closure
fallback has planner coverage but needs a separate hardware test. See SIDECAR.md
for unknown-outcome handling after a deadline.
