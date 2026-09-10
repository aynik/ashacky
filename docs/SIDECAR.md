# Sidecar display controls

Ashacky uses macOS Sidecar to connect an iPad as the guest's external display.
Linux sends discovery/connect/disconnect requests through the existing guest
agent and authenticated SPICE channel. The host app launches an embedded helper
in its graphical session; there is no runtime SSH, additional permission app,
LaunchAgent or new virtual port.

The supported layout for this work is the built-in screen plus one external
screen. Disconnect an HDMI/DisplayPort monitor before connecting an iPad. AirPlay
TV control is separate work and is not implemented here.

## GNOME controls

The existing Ashacky extension adds a **Displays** tile to Quick Settings. Open
its submenu to discover available iPads, then choose the device's **Connect** or
**Disconnect** action. **Display Settings** opens GNOME's usual display panel.
Discovery runs on menu open, explicit **Refresh**, and monitor changes while the
menu is open. There is no background discovery polling loop. Wake/unlock a missing
iPad, then refresh; an empty list does not prove the backend is unsupported.

macOS creates the Sidecar screen, and the existing CocoaSpice/virtual-GPU hotplug
path supplies the second Linux output. No additional guest screen-capture or
encoding stage is introduced. GNOME Network Displays is not required or patched.

Only the configured guest desktop user and root may request these operations.
The guest strips unknown fields, including supplied tokens and command strings.
Connection targets are fixed-format opaque IDs, never executable names or shell
fragments. The native helper checks the current Mac console user and refuses a
new connection when the external-display slot is occupied or cannot be determined.
Repeated already-satisfied operations are no-ops.

Disconnect is explicit: it can disconnect the selected iPad even if that Sidecar
session was started manually in macOS. Ashacky does not automatically disconnect
pre-existing sessions or save a preferred iPad for automatic connection.

## Failure and lifetime behavior

Private Sidecar calls run in `Ashacky.app/Contents/MacOS/AshackySidecar`, outside
the display/input thread. One helper request runs at a time. Native completions
are asynchronous; a 20-second completion deadline reports an unknown outcome,
a 25-second process alarm bounds synchronous private API stalls, and the parent
has its own 28-second deadline. Helper output is limited to 16 KiB and lists to
16 destinations. Raw native device identifiers and private NSError userInfo are
not forwarded.

Timeout or cancellation kills the helper, but does not guarantee that macOS
cancels an operation it has already accepted. Refresh the list and inspect the
screen before retrying. Do not assume failure means the iPad stayed disconnected.
Host-controlled locking, sleep, session resignation and control-channel closure
cancel pending helper processes; a late child completion cannot satisfy another
request. These cancellation paths have fixture coverage; physical lock/sleep
during an outstanding native connection remains an attended check.

The guest agent handles at most eight concurrent clients. A slow Sidecar request
therefore does not serialize the lock command behind discovery or connection.

## Build and isolated host checks

The normal frontend build compiles the helper with Command Line Tools and bundles
it into the existing app. Full Xcode is unnecessary. Use the candidate build and
runtime checks in [BUILD.md](BUILD.md#frontend-only-validation-while-the-vm-runs).
For an attended host-only probe, run from the repository root in the active Mac
user's graphical terminal:

```sh
mkdir -p build/sidecar
xcrun swiftc -swift-version 5 \
  host/devices/SidecarBackend.swift tools/sidecar-probe.swift \
  -o build/sidecar/sidecar-probe
build/sidecar/sidecar-probe list
```

Use the exact `id` returned for the chosen iPad; keep real output private:

```sh
read -r -p 'Sidecar destination ID: ' sidecar_id
build/sidecar/sidecar-probe connect "$sidecar_id"
build/sidecar/sidecar-probe list
```

When ready to end the test:

```sh
build/sidecar/sidecar-probe disconnect "$sidecar_id"
build/sidecar/sidecar-probe list
```

An administrative SSH shell belongs to a background bootstrap session. On the
reference Mac it could discover the iPad but connecting returned Sidecar error
-101. Launching the same helper in the user's graphical bootstrap session
succeeded. For development from SSH only, run the command as the logged-in user
with the appropriate GUI bootstrap namespace (privilege may be required):

```sh
sudo launchctl asuser "$(id -u)" sudo -u "$(id -un)" \
  "$PWD/build/sidecar/sidecar-probe" list
```

This is a development recipe, not a runtime dependency or a general definition of
every Sidecar -101 error. The packaged helper inherits Ashacky's GUI context
without sudo. A native success reply may precede a topology update; verify the
resulting Linux screen independently.

## Validation status

The reference macOS 15.7.3 host passed real iPad discovery, connect, disconnect
and reconnect through the isolated helper. GNOME reported two logical displays,
removed the second on disconnect, and restored it at 2388×1668 on reconnect. The
owner confirmed the Sidecar connection. The primary Linux desktop stayed running.

Native fixtures cover success, bad target IDs, inactive session, busy handling,
hung children, oversized/malformed replies, native failure and cancellation with
late completion. Guest tests cover authorization and concurrent lock handling.
GJS controller fixtures cover on-demand refresh, duplicate suppression, failure
reconciliation and disabling the extension; they do not render real shell widgets.

The integration is active after an attended VM restart. The installed guest files
match the staged sources, the extension is active, and discovery succeeds through
the authenticated SPICE channel. The owner confirmed the GNOME menu works; a live
Mutter observer recorded the second output disappearing on disconnect and returning
on reconnect. The guest agent and control broker reported no automatic restarts.
Unattended iPad reconnection, a locked/asleep iPad, physical sleep/lock during
connection, and multiple external screens are not claimed as validated behavior.

Private entry points were identified from the MIT-licensed
[SidecarLauncher source](https://github.com/Ocasio-J/SidecarLauncher/blob/4b7a9df950a64239b2a073428f0390fc16934a9e/SidecarLauncher/main.swift)
and verified against runtime metadata. Ashacky's adapter is a new implementation;
it does not bundle SidecarLauncher. Apple can change these undocumented APIs in
a macOS update.
