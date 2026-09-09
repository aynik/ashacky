# Agent installation workflow

Read [README.md](../README.md), [STATUS.md](STATUS.md), [BUILD.md](BUILD.md) and [DEVELOPMENT.md](DEVELOPMENT.md) first. Use [PROVISIONING.md](PROVISIONING.md) for the concrete offline plan and Debian/account recipe. The build and staging tools do not apply settings or install services; the installing agent applies reviewed outputs and records its actions. Fresh-account acceptance remains unverified. The reference account now runs the repository-built runtime; this does not establish fresh-account acceptance.

## Scope and discovery

Work in the dedicated, currently logged-in macOS account chosen by the owner. Discover its real UID/GID, canonical home/checkout path, macOS build, architecture, active-console status, toolchain and available sudo. Inspect existing Ashacky/LinuxHost services before generating names; a different user must not replace the reference account's system helpers. Do not infer the guest user's identity from the host username.

Keep one checkout and one `build/`. Create private VM/configuration/credential/log storage outside tracked files, mode 0700. Keep root policies and installed root helpers in root-controlled locations. Record every installed path, digest, owner, symlink target, launch label and changed setting in a private installation receipt. The manifests describe intended outputs; maintain an actual installation receipt using the workflow in PROVISIONING.md. Automated receipt application/rollback is not implemented; do not leave unrecorded system edits behind.

Use existing task authorization for the requested dedicated-session setup. Ask only for missing distro/account choices or required human interaction. macOS privacy/authentication dialogs must be completed through the OS; do not modify its TCC database. Touch ID enrollment remains in macOS.

## Build and provision

1. Use Command Line Tools for normal setup; do not require full Xcode or a Metal compiler. Fetch the pinned UTM release assets with `python3 tools/prepare-utm-assets.py`. Initialize the recorded dependencies with `git submodule update --init --recursive --depth 1`, then build verified dependencies and components following BUILD.md. Keep submodule checkouts pristine and use the preparation recipes for patches. Build and assemble the runtime and run its relocation checks before any future runtime replacement. The UTM disk image supplies verified upstream libraries and shaders only; do not install its app or copy a personal VM.
2. Download an official ARM64 image/installer for the selected distribution and verify its published checksum/signature. Record URL, version and digest privately. Create fresh VM UUID, NIC MACs, disk serial, firmware variables and guest-management keys. Have the owner choose a password; never commit it or a password hash.
3. Bootstrap with a supported cloud-init NoCloud seed, installer hook, or guest-tools disk. SPICE channels do not install software. QGA is usable only after installation. Do not silently enable broad root login or disable host-key checking; generate and pin the dedicated guest management key and exact VM identity.
4. Use the `session.py` device topology: management virtio NIC first, Wi-Fi transport NIC second, HDA at PCI 4, input port `org.linuxhost.input`, four explicit USB root ports, and the shared-memory device. Keep stock guest kernel/Mesa/browser. Configure guest DHCP on the management NIC and the dedicated Wi-Fi lower interface; do not hardcode a physical Wi-Fi interface name or display model.
5. Install guest components using `guest/layout.json` as the file map. Adapt package names, module signing/DKMS, initramfs, PAM, libva path/diversion and desktop version for the selected distro. Only Debian/GNOME is a tested reference; other combinations need their own acceptance results.

## Guest integration details

- `/etc/linuxhost.json`: protected root-owned configuration with `userID`, a fresh control `token`, and the forwarded `hostSocket`. Current SSH transport uses `/run/linuxhost-host.sock`; do not copy the reference file.
- `/etc/ashacky/devices.json`: fresh `vmUUID`, `wifiInterface`, `wifiMAC` and camera device number (the current bridge expects video10). The loader verifies the DMI UUID and NIC identity before attaching its virtual Wi-Fi module.
- Keep the two transport NICs unmanaged in NetworkManager and configure management DHCP through the distro's system networking. Let NetworkManager manage the virtual Wi-Fi interface. Generate names from the actual guest NIC identities and ordering.
- The Bluetooth D-Bus facade owns `org.bluez`. Record and disable/mask stock bluetooth.service only for this dedicated guest; don't run two owners concurrently. Keep its D-Bus policies/packages available. Install rfkill support.
- Audio nodes expose host default-device selection over SPICE, not independent simultaneous physical routes. Enable the user PipeWire integration after the audio stack.
- Install the three custom modules with DKMS, and use the distro's v4l2loopback module. Do not copy a prebuilt `.ko` between kernel versions. Enable only the services in the layout after their prerequisites are ready.
- PAM uses `pam_linuxhost.so user=<actual-guest-user>` for supported services, followed by the distro's existing password stack. Preserve password fallback and test with a second administrative path available. Do not replace the whole distro PAM file.
- GNOME Shell integration currently targets GNOME 50. Enable its extension for the selected guest user; test other major versions rather than merely changing metadata.
- Install the general VA driver under the distro's libva driver directory. Track any `virtio_gpu_drv_video.so` diversion/symlink and preserve package-manager ownership semantics. Install the matching remote FFmpeg library closure, broker, environment file and user socket. In `/etc/ashacky/video.env`, set `LH_VIDEO_H264_HOST` to the private host address and H.264 port, and `LH_VIDEO_VP9_HOST` to the private host IPv4 address without a port. With the shared mapping present, the VP9 broker uses port 5569. Keep video listeners private and isolate/authenticate them before multi-account testing.
- Install only the general VA-API driver and decoder service. Do not add browser launchers, preload helpers, preference fragments or desktop overrides. Sandboxed-browser hardware decoding may require application-specific integration and is not a packaged capability. The discarded custom browser and queued driver remain excluded.

## Host services and startup

Generate one user LaunchAgent for the session supervisor, which owns the SSH forwards and decoder children. The CocoaSpice display, device services, host control and lock synchronization share the Ashacky executable; do not generate separate jobs or executables for these services. Use absolute discovered paths; LaunchAgents do not inherit an interactive shell's PATH. Provide `LINUXHOST_SESSION_CONFIG`, `LINUXHOST_CONTROL_CONFIG`, `ASHACKY_GUEST_SSH` and `ASHACKY_PYTHON` explicitly. The SSH wrapper requires the configured interpreter instead of choosing a system Python. Respect Unix socket path length limits.

Bundle the patched `vtremoted` H.264 server built in BUILD.md; the session supervisor starts it as a child, so it needs no separate LaunchAgent. Its `--listen` address/port must match the guest broker configuration. Do not assume the supervisor's VP9 helper serves H.264, and do not use upstream's generic service-install script or its default listener. Bundle the server's LZ4/Zstandard runtime dependencies. The server build is separate from `./ashacky build host`; the runtime builder, assembler and offline plan generator cover these outputs.

Host transport config has exactly `uuid`, `privateDirectory`, `managementSubnet` and `fallbackAddress`, with protected permissions. VM config selects `userID`, `uuid`, `diskSerial`, `disk`, `vars`, `shared`, `runtime`, `control`, the two `macs` and `networks`, `usbSocket`, `videoBindAddress`, and optional `appContents`, CPU/memory/name values. Generate matching control config and power helper constants. Never point these inputs at an unrelated existing VM.

Provision root-controlled socket_vmnet networking for the private management network and actual Wi-Fi bridge interface. The runtime builder compiles socket_vmnet and CoreWLAN interface discovery; the offline plan generates its root wrapper and launch jobs, with placement described in PROVISIONING.md. Provision distinct root USB and power helper paths/labels for the target account. Helpers must reject inactive-console callers; a separate login alone does not isolate physical hardware or a shared subnet.

After ordinary boot, device permission and recovery checks pass, enable automatic launch for the account. Record the previous loginwindow preference and launchd overrides before setting `com.apple.loginwindow TALBlockSavingAndLaunch=true` and disabling only that user's `com.apple.Finder` and `com.apple.Dock.agent`. The reference tested these with SIP disabled on macOS 15.7.3; inspect OS compatibility before applying them elsewhere. Do not delete system apps, replace loginwindow, change other users, or start-and-kill Finder/Dock.

## Acceptance and migration

Fresh-account testing remains unverified. The owner has authorized a controlled migration of the working reference installation, preserving its VM and rollback state. Do not infer permission to run a second setup agent or a concurrent test VM from that migration.

Use a fresh account and a fresh VM for the complete installation test. Establish helper/network isolation before both installations can run. Validate:

- Login directly to Linux; escape shortcut, focus, pointer and permission dialogs.
- Wi-Fi scan/connect, Bluetooth pair/reconnect, audio output/input switching, camera demand capture.
- Multitouch, clipboard, Retina, external display hotplug, accelerated OpenGL/Vulkan.
- USB storage remove/reinsert, read and an owner-approved write test; no desktop freeze.
- Battery updates, Touch ID/password fallback, sleep/wake, both lock directions, guest logout and coordinated power actions.
- Reboot/login, guest kernel update with rebuilt modules, component update, interrupted setup recovery and uninstall that preserves the VM.
- VA-API decode in ordinary clients such as mpv; assess sandboxed browsers separately and report measured 4K60 limits without claiming unsupported codecs.

For an explicitly authorized migration of an existing installation, record originals and rollback before changing files. Preserve its disk, firmware variables, VM/NIC identities, management key and control token. Validate guest changes against the running host first; stop the VM cleanly before switching its supervisor and privileged services. Apply host cutover at logout/login, with a watchdog capable of restoring the old launch configuration if startup fails. Never start a second hypervisor on the same disk. Retire the old runtime only after attended device and session acceptance. A migrated existing VM does not establish fresh-install reproducibility.
