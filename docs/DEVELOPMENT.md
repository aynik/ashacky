# One checkout, one current build

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

### Updating audio event delivery

Install `audio_pipewire.py` and its companion `audio_events.py` together from the guest manifest, then restart the guest user's `linuxhost-audio-pipewire.service`. The client requires the distro's PyGObject/GIO bindings (Debian `python3-gi`, already needed by the Bluetooth facade) and PipeWire's `pw-dump`, `pw-loopback` and `wpctl`. The companion must be in the same installed directory as the audio script. Record new and replaced files in the private receipt.

Build and validate the updated frontend before replacing the running app during a clean session stop. Audio reuses `org.ashacky.status`; no additional QEMU port, kernel module or permission is introduced. Until that frontend is active, the new guest client intentionally uses compatibility polling for the host device list.

In the guest, check `audioRevision` in `/run/linuxhost/status.json` and `journalctl --user -u linuxhost-audio-pipewire.service -b`. Healthy native operation logs `Audio host updates: events`; `compatibility polling` means the revision is missing or stale. Confirm one long-lived `pw-dump -m -N` child, with metadata queries only on changes. Check connection/removal of an audio device, default output and input changes in both directions, mute/zero-volume preservation, and user-service restart. Verify actual sound after selection; a correct device list alone does not establish audio routing. Document any fallback or hardware checks that remain untested.

### Updating an installation with standalone session helpers

The frontend now contains host control and lock synchronization. Updating only the app leaves an old power helper authorizing the removed `LinuxHostControl` executable, so coordinated power actions will fail. During a scheduled clean stop:

1. Preserve the installed private configuration, disk/NVRAM, VM UUID, MACs, disk serial, SSH key and control token. Update the existing private `helper-build.json` so `powerClients` contains the canonical `Ashacky.app/Contents/MacOS/Ashacky` path. Do not replace these files with identities from a freshly generated plan.
2. Update the checkout and pinned submodules. Rebuild the frontend and host helpers, assemble the app and privileged closure, then run the runtime checks in BUILD.md. Refresh the root-owned helpers from that closure and restart the affected root jobs. Root helpers must not execute from the checkout.
3. Keep only the generated session LaunchAgent and four root jobs for this installation. Remove any recorded standalone control, SessionSync, forwarding or H.264 jobs; their functions belong to the app or supervisor now. Preserve the root `service.sh` wrapper that initializes runtime-directory permissions. Ensure the session environment contains `LINUXHOST_SESSION_CONFIG`, `LINUXHOST_CONTROL_CONFIG`, `ASHACKY_GUEST_SSH` and the absolute configured `ASHACKY_PYTHON`.
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

If the cache is stale or still reports `rpc-poll`, follow the battery channel diagnostics above. If it is fresh and has `bluetoothRevision` but the facade fails, check the companion files and `python3-gi`, root ownership and directory traversal permissions, and the management service/SSH forward before changing macOS permissions. The kernel's Bluetooth rfkill `hard` flag marks backend unavailability; it can expire roughly 15–16 seconds after its last valid feed. The event consumer rejects a revision after 25 seconds without fresh status. These are different health checks: no host heartbeat must not be presented as a deliberate radio-off event. Fresh status plus an unavailable radio points to the facade/module feed; read the Bluetooth entry under `/sys/class/rfkill/` or use the distro's `rfkill` utility.

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

The one-second radio synchronization, five-second signal refresh and independent 45/90-second request timeouts remain. Requests also wait while the bridge performs a synchronous host operation. Check idle blocking with discovery closed; repeated scans requested by NetworkManager are real work, not an idle-loop regression.

Recovery restores the recorded bridge and DKMS source, rebuilds/installs that source for the intended kernel and reloads it with the same procedure. Update the private receipt to match. Restoring Git alone leaves installed code and the loaded module unchanged; keep the existing VM, host app and permissions intact.
