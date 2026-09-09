# Prepare an installation for review

This document gives the concrete account and Debian/GNOME recipe. During the current audit, only generation, compilation, staging and read-only checks are authorized. The application steps below are for a future explicitly requested fresh installation. Do not apply them to the working reference account or guest to validate these instructions.

## Generate matching identities and service files

Copy `examples/installation.json` into a private, ignored build location and replace its examples with discovered values. The host and guest usernames/UIDs are independent. `checkout` must be the permanent canonical source path. `privateDirectory` holds the VM, keys and runtime files; use a short path such as the dedicated user's `.ashacky` directory because macOS Unix sockets have a 104-byte pathname limit.

Choose a separate integration group containing only the dedicated macOS user. Check the proposed group name and GID against `dscl . -list /Groups PrimaryGroupID` before creating it in a future installation. Do not use `staff` for the network socket group: other macOS accounts belong to it. Choose an unused private `/24` management subnet, checking existing routes, vmnet helpers and other VMs. The host is `.1` and the first guest lease fallback is `.2`; actual leases and the pinned VM identity are checked by the SSH transport. This selection does not itself isolate simultaneous installations. Host video listeners are bound to that subnet's host address, and are not authenticated protocols; simultaneous installations remain deferred pending an isolation test.

Guest NIC names come from the chosen VM's PCI topology and must be verified against their generated MACs after bootstrap. The current topology normally names them `enp0s1` and `enp0s2` on Debian. Do not substitute the host's Wi-Fi interface name. The host network helper discovers that independently with CoreWLAN.

```sh
python3 tools/plan-installation.py --config /absolute/private/installation.json --name candidate
```

The only writes are to the new `build/plans/candidate` directory. It contains fresh VM/NIC identities, a shared control token, host configs, LaunchAgent/LaunchDaemon plists, root network/power policy files and guest network/device/video configs. The directory is private; never commit or paste its secrets. Existing plan names are refused so a reviewed identity cannot be silently replaced. `manifest.json` records intended destinations, owners and modes. No key, VM, group, service or OS setting is created by this command.

Build the account-specific privileged helpers on macOS after the runtime build:

```sh
./ashacky build host --installation build/plans/candidate/helper-build.json
python3 tools/assemble-privileged.py
```

The `powerClients` path must resolve to the canonical `LinuxHostControl` executable in the final bundle. Generate a new plan if the permanent checkout location changes. Keep root power policy and user `powerEnabled` false while bringing up the VM; enable both after the ordinary session and shutdown checks are ready. `allowOtherSessions=false` remains the default.

## Fresh VM bootstrap, when installation is requested

Use an official Debian ARM64 cloud image with cloud-init, or an official installer. Record its exact release URL and verify its published digest/signature before use. Select the distro release explicitly; the reference is Debian forky/sid with GNOME 50, not a claim that Debian stable provides that desktop version. Installing the full `linux-image-arm64` kernel avoids assuming a cloud kernel contains the desktop, cfg80211 and multimedia modules.

Create the chosen guest user and have the owner set its password through the installer or a private cloud-init seed. Do not reuse the reference username/password, disk, NVRAM, host SSH keys or management credentials. A seed containing a password hash is private data and must be removed after bootstrap.

The built `qemu-img` can convert the verified cloud image to the plan's new `guest.qcow2` and resize it. Require that each output does not exist first. Decompress `build/sources/qemu/pc-bios/edk2-arm-vars.fd.bz2` to a temporary raw file, then use `qemu-img convert -f raw -O qcow2` to create the plan's `vars.qcow2`; remove the temporary raw copy. This is the pristine variable template from the verified QEMU archive. The immutable code firmware is already in the generated app. Never initialize variables by copying a used VM's file.

For cloud-init, make a private seed directory with `user-data`, `meta-data` and, if needed, `network-config`. Use the generated UUID as the instance ID; configure the management NIC by its generated MAC, install the chosen desktop user, and inject only fresh authorized public keys. On macOS, `hdiutil makehybrid -iso -joliet -default-volume-name cidata -o seed.iso seed-directory` creates the seed image. Set the VM config's optional `provisioningISO` to its absolute path. The supervisor attaches it as a read-only USB CD without changing the four USB redirection ports. Remove that config field and delete the seed after bootstrap; this boot path still needs fresh-install acceptance.

The supervisor's normal device topology and QMP startup apply to bootstrap as well. First start it manually in the target Aqua session, using the generated config and its environment variables, after the requested installation has provisioned the root networking helpers. Do not enable automatic login startup before basic boot and permissions work.

Set up management SSH on the fresh guest deliberately. The current transport uses `root@guest` to inspect DMI, forward root-owned `/run` sockets and call fixed session helpers. Generate a dedicated Ed25519 key in `privateDirectory/id_ed25519`, mode 0600. Restrict its guest authorized-key entry to the selected host management address, disable password login for root, and disable agent/X11/PTY forwarding for that key while retaining port forwarding and command execution required by these scripts. Keep ordinary owner administration separate. Verify the guest SSH host-key fingerprint through the installer/console or another authenticated provisioning channel; write a `linuxhost-vm` alias entry into `privateDirectory/known_hosts`. Do not trust an unverified `ssh-keyscan` result.

The guest SSH server needs `AllowStreamLocalForwarding yes`, `StreamLocalBindUnlink yes` and `StreamLocalBindMask 0077` for reconnectable private forwards. Check the effective SSH configuration with `sshd -T`, scoped to the intended management login when Match rules are used. Enable only the necessary root key on the fresh guest, with its host-address restriction; the existing working guest is not a bootstrap target.

## Debian guest payload

Build in the target Debian ARM64 environment using BUILD.md, including the pinned FFmpeg prefix and broker. The reference runtime additionally uses these distro packages:

```sh
sudo apt install gnome-core gdm3 network-manager wpasupplicant bluez \
  pipewire pipewire-pulse wireplumber pipewire-bin python3-gi python3-evdev \
  dbus-user-session iproute2 rfkill ffmpeg openssh-server qemu-guest-agent \
  spice-vdagent dkms v4l2loopback-dkms linux-image-arm64 linux-headers-arm64 \
  liblz4-1 libzstd1 patchelf
```

This package command is an installation step, not an audit command. Confirm package names and the selected GNOME major before applying it on a different Debian release. The extension currently targets GNOME 50. Other distro adapters remain future work.

Transfer only the generated plan needed by this VM through the authenticated management channel, then stage the payload:

```sh
python3 tools/stage-guest.py --ffmpeg-prefix build/ffmpeg-prefix \
  --plan build/plans/candidate
```

This writes only `build/guest-root`, `build/guest-manifest.json`, `build/guest-actions.json`. It copies the existing installation map, generated guest configs, PAM/VA modules, DKMS sources, private FFmpeg libraries and GNOME session integration. Browser launchers, preload helpers, preferences and desktop overrides are excluded. FFmpeg libraries use `$ORIGIN` and the broker uses `$ORIGIN/../lib`; it cannot accidentally use the old developer prefix. It never invokes DKMS, changes PAM, enables a service or modifies `/etc`. A repeated staging run requires removing the inactive generated payload first.

`guest-manifest.json` includes directory permissions as well as file modes and ownership. The staging directory itself is private; copying its enclosing 0700 mode onto installed `/opt` directories would prevent desktop-user services from reading their scripts. Apply the per-entry manifest in a future installation, using root ownership. Capture prior contents, owners, modes, symlink targets and package ownership before replacing an existing target. Do not copy all of `build/` into the guest.

After payload installation, perform the following distro integration actions, recording each one:

1. For each package in `guest-actions.json` (`linuxhost-wifi`, `linuxhost-bt-radio`, `linuxhost-battery`), run `dkms add -m NAME -v 0.1.0`, `dkms build -m NAME -v 0.1.0 -k TARGET_KERNEL`, and `dkms install -m NAME -v 0.1.0 -k TARGET_KERNEL`. Run `depmod -a TARGET_KERNEL`. Do not copy audit `.ko` files. The image uses non-secure UEFI; a distro configuration enforcing module signatures needs its own signing setup.
2. Preserve the generated NetworkManager exclusion by interface name. The virtual Wi-Fi interface inherits its transport NIC's MAC, so a MAC-based unmanaged rule would disable the desired Wi-Fi controls. Let systemd-networkd provide management DHCP using the generated `.network` file and let NetworkManager manage the virtual Wi-Fi device. Check existing networking configuration before enabling networkd to avoid duplicate DHCP clients. Resolve DNS through one chosen distro mechanism; do not replace `/etc/resolv.conf` blindly.
3. Preserve the stock BlueZ package and D-Bus policies, but record and mask its `bluetooth.service` in this dedicated guest before enabling the facade that owns `org.bluez`. The facade runs as root under that policy. Enable `linuxhost-probe-management.service` before its Bluetooth/audio clients. Camera uses v4l2loopback video10 and starts capture only on demand.
4. Merge the generated `pamAuthLine` before the password authentication include in the selected PAM services (the reference uses `sudo` and `gdm-password`). Preserve all other auth/account/session rules and password fallback. The `user=` value is the guest user, not the host user. Keep a separate administrative path while testing. Do not replace entire distro PAM files.
5. In Debian's `/etc/gdm3/daemon.conf`, merge the generated `[daemon]` automatic-login settings for the selected guest user. This makes a new host-authenticated boot open its desktop; SessionSync only unlocks an existing session and does not perform a fresh login. Keep guest lock-screen authentication enabled.
6. Install `ashacky_drv_video.so` in the detected libva driver directory. Use a recorded `dpkg-divert --local --rename --add` for an existing package-owned `virtio_gpu_drv_video.so`, then create that name as a symlink to `ashacky_drv_video.so`. Record the diversion destination and prior symlink exactly. Do not overwrite Mesa's file without a diversion. Other GPU driver names are outside this adapter.
7. Test the general VA-API path in an ordinary client such as mpv. Its optional `guest/video/mpv.conf` is a reference configuration, not installed by the payload. Sandboxed browsers may block the Unix socket or shared-memory mapping; do not advertise browser hardware decoding as working solely because the driver is installed, and do not add browser-specific workarounds to this package.
8. Run `systemctl daemon-reload`, then enable the system units listed in `guest-actions.json` only after their prerequisites and host forwards are available. Enable `qemu-guest-agent` as appropriate for the distro's static/device-activated unit, and verify the SPICE vdagent user session. In the actual guest user's session, run `systemctl --user daemon-reload`, enable `linuxhost-audio-pipewire.service` and `linuxhost-video-direct.socket`, and enable the GNOME extension with `gnome-extensions enable linuxhost@local`. Enabling the video socket does not require running the broker persistently before a client connects.

Check `ldd` on the staged broker before installation: `libavcodec` and `libavutil` must resolve inside the payload's private library directory, with other dependencies provided by the distro. Do not run the broker as a staging test: its normal job is to connect to the host decoder service.

## Host placement and launch, when requested

Use the generated host manifest and the permanent checkout. Create the private directory, `runtime`, `logs` and `shared` as the dedicated user, mode 0700. Place host private JSON files and management keys there, mode 0600. Ashacky.app and the user scripts remain generated outputs/source in that checkout. Use their absolute paths in the generated plists; LaunchAgents do not inherit the interactive shell environment. `ASHACKY_PYTHON` makes the SSH wrapper use the selected interpreter too.

Create the dedicated integration group only after checking its name/GID are unused; add only the target user. Install the entire `PrivilegedHelpers` payload to the generated `rootDirectory`, root:wheel, with directories 0755, executables 0755 and libraries root-owned and unwritable by group/others. Add both generated root-owned scripts, `service.sh` and `network.sh`. Install the root power policy at its manifest path, mode 0600 with root-owned parent directories. Copy the generated LaunchDaemons to `/Library/LaunchDaemons`, root:wheel 0644, and the LaunchAgents into the selected user's `~/Library/LaunchAgents`, owned by that user and mode 0644. Never point a root LaunchDaemon at a user-writable source script, Homebrew library or build output.

Load only these exact root labels with `launchctl bootstrap system /Library/LaunchDaemons/LABEL.plist`. Each job first uses `service.sh` to create the common runtime parent explicitly as root and the dedicated integration group, mode 0750. Verify that the account can traverse that parent and that both network sockets belong to the group. Creating only a child directory under launchd’s 0077 umask leaves an inaccessible 0700 parent. Root logs are at the generated `/var/log/ashacky-UID-NAME.log` paths. The USB/power helpers enforce the configured UID and active console; the network helper uses group-controlled access. No validation service may replace a reference account's helper label or socket.

For initial attended permission checks, open the assembled app in the selected account with `open -a /absolute/checkout/build/host/Ashacky.app --args --setup /absolute/private-directory`. There is no custom permission window: macOS requests undecided Location (for Wi-Fi discovery), Bluetooth, Camera and Microphone access one at a time. Granted, denied and globally disabled permissions do not trigger repeat prompts; previously denied access must be changed in System Settings. This mode starts the same device endpoints but no VM or capture. Stop that setup process after checking its same-user status socket and before starting the VM supervisor: normal runtime embeds these services in the CocoaSpice display process, without separate permission apps or LaunchAgents. The supervisor launches the bundled `AshackyLauncher`, which opens the app through LaunchServices; directly spawning the app executable from Python loses its permission attribution on the tested macOS release. It supplies `ASHACKY_PRIVATE_DIRECTORY` from the control configuration's parent directory. Device forwarding reconnects when that process starts. Preserve signing/bundle identity across rebuilds where possible; ad-hoc signed rebuilds can invalidate existing grants, so perform permission checks after the final build. Never modify the TCC database. During migration, old Workbench grants do not transfer to the new `local.ashacky.host` identity; grant access to Ashacky before retiring the old runtime.

Before loading the session job, run `host/session/session.py --config PRIVATE/vm.json --check` with the chosen Python to validate ownership/identity. Set the four environment variables from the generated plist when doing a manual supervisor run. A normal `open -a /absolute/checkout/build/host/Ashacky.app` instead asks launchd to start the installed session job, without restarting an already running VM. Load user jobs with `launchctl bootstrap gui/UID ABSOLUTE_PLIST` from that account's Aqua session. The session job has `RunAtLoad` without `KeepAlive`; otherwise guest logout could cause a new VM to respawn. One user LaunchAgent owns the session; SessionSync, control/device forwards and H.264 decoding run as its child services, with bounded restarts and process-group cleanup. Control and VP9 decoding are also supervised with QEMU. Four root jobs provide the two network backends, USB and power. Every generated job declares `AssociatedBundleIdentifiers` for Ashacky so macOS can associate it with the application. Background-task approval is separate from device privacy permissions; a custom app panel cannot grant either.

After ordinary boot and the attended checks pass, enable the requested dedicated-session login behavior. Record prior values before setting the account's `com.apple.loginwindow TALBlockSavingAndLaunch=true` and using `launchctl disable gui/UID/com.apple.Finder` and `launchctl disable gui/UID/com.apple.Dock.agent`. Those are per-user overrides tested only with SIP disabled on the reference macOS version. Do not issue system-domain disables or delete system apps. Test the next login only when the owner requests it; leave the current audit's login settings unchanged.

## Receipts, recovery and updates

The generated manifests are installation inputs, not receipts proving an installation happened. The installing agent must keep a private receipt of the files actually installed, their final hashes/owners/modes, original replaced contents, package diversions, group membership, exact launch labels and prior settings. Never mark an operation applied until its command succeeds. Apply directory modes explicitly after creation (the installing process’s umask can override `mkdir`), then apply file modes and write root config atomically. Check a desktop-user read of `/etc/ashacky/video.env` and a fresh start of its user service; a previously running broker can hide directory-access errors.

Recovery stops only the recorded new user/root jobs, restores the recorded Finder/Dock/loginwindow overrides, reverses the recorded VA diversion and PAM/GDM/desktop changes, unregisters only the installed DKMS package versions and restores stock BlueZ's prior state. Remove only unchanged installed files whose hashes match the receipt; preserve any subsequently modified files for review. Preserve the VM, NVRAM and private user data. Group removal requires confirming it belongs solely to this installation. Automated receipt application/rollback is not implemented; this is the installing agent's explicit workflow.

After a replacement passes attended acceptance, retire only the legacy jobs/apps recorded in its receipt. Review device privacy grants inherited by old Terminal/Python launches as well as old app bundle IDs. Use `tccutil reset SERVICE BUNDLE_ID` for a specific obsolete grant; keep the new Ashacky grants. Resetting a permission is not a reversible backup of consent. Read `sfltool dumpbtm` to identify background registrations; do not use the global `sfltool resetbtm` as a per-app cleanup tool. Location registrations have separate controls in System Settings. Keep a private list of any stale entries that still need attention.

A source update must wait until the affected runtime is stopped. Rebuild the one current generated runtime, refresh installed outputs from the checkout, update the receipt and validate. Fresh-account acceptance remains deferred. The owner-authorized reference migration and its remaining login acceptance are tracked in STATUS.md and VALIDATION.md.
