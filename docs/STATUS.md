# Consolidation status

The reference installation was tested on an M1 MacBook Air, macOS 15.7.3, Debian forky/sid ARM64, GNOME 50 and the Debian 7.1 kernel family. That is evidence for this combination, not a supported-platform matrix for every Apple Silicon Mac or distribution.

## User-verified in the reference installation

- Direct Linux desktop after macOS login; Finder and Dock absent.
- Linux logout → macOS logout; coordinated power operations.
- Sleep/wake and bidirectional lock/unlock synchronization.
- Battery/AC status through the standard Linux power-supply interface; charger changes immediately reflected in GNOME.
- Touch ID for lock-screen authentication.
- Wi-Fi scan/connect and Bluetooth device control through GNOME.
- Trackpad and AirPods connected through host Bluetooth; audio endpoint switching and microphone input.
- Built-in camera bridge and SPICE audio.
- Native macOS/GNOME output volume and mute synchronization.
- Retina resolution, external display and monitor hotplug.
- Trackpad motion, scrolling and Firefox pinch-to-zoom.
- OpenGL/Vulkan rendering and light gaming.
- USB storage hotplug, reads, writes and reformatting after the explicit-root-port fix.

Upstream sources are now pinned submodules with Ashacky patches applied during preparation. Source recipes prepare from pinned inputs. The documented builds cover the guest modules and VA/FFmpeg stack, SPICE/GStreamer, QEMU, render server, frontend and macOS helpers. The assembled runtime passes relocation, dependency, signature, GPU-shader and silent audio checks without a VM. The audit and its limits are recorded in [VALIDATION.md](VALIDATION.md).

The reference installation now runs from this checkout. After the initial directory-permission failure, a second issue produced a black window: the SPICE client disabled GL scanout when built without EGL, even though the Metal frontend supports IOSurface scanout. The frontend now advertises that capability explicitly; the owner confirmed a working Linux desktop. Lock synchronization and host control are now linked into the Ashacky process. Current validation and cleanup are recorded in [VALIDATION.md](VALIDATION.md). Fresh-account and full logout/login acceptance remain separate checks.

## Audio behavior

Host device hotplug and Linux endpoint selection now use notifications instead of the normal one-second polling loop. The reference output connect/disconnect, selection and sound checks passed; input-device hotplug with this update remains an attended check. An older frontend or stale event channel retains compatibility polling.

Output master volume and mute are linked after an authorized clean VM restart; the owner confirmed the controls work. Live readback confirms matching host/guest state, unity Linux software master gain, advancing CoreAudio notifications and healthy audio/management services without automatic restarts. Twenty-one audio tests pass, including generated PCM through an isolated PipeWire daemon. The assembled frontend, guest payload and all 76 source tests passed validation.

Outputs without writable master level and mute retain local software volume. Microphone controls and per-application gain remain separate. Physical output switching/hotplug, fixed-volume hardware and rapid concurrent control changes have not been retested for this refinement. See [audio architecture](ARCHITECTURE.md#audio-management-events) for recovery and gain-transition limits.

## Bluetooth management behavior

The native notification path and guest BlueZ event consumer are installed after an authorized clean VM restart. Native event mode, facade restart, radio-watchdog health and the existing audio path passed runtime readback. The owner confirmed fast Bluetooth disconnect/reconnect updates, and the guest trace recorded both connected-state changes promptly after their revision tokens. Fresh pairing, physical host-radio toggles and discovery with new hardware have not been retested with this refinement. The update retains a 60-second reconciliation for external changes that lack a notification, two-second compatibility refresh for old/stale hosts, and bounded operation-status checks while a command is outstanding. Active scans can still delay device-management replies until their bounded inquiry finishes.

## Wi-Fi request delivery

The reference guest now uses kernel readiness notifications for scan/connect/disconnect requests instead of the bridge's 100 ms idle checks. The updated module and bridge passed live pending-request, cancellation, restart and scan checks; the existing network reconnected after a module-only reload. The owner confirmed nearby networks appear in GNOME and the connection works. The old-module compatibility path was also exercised before activation. A complete host disconnect/reconnect and radio toggle have not been repeated for this refinement. The new radio event refinement is described below. Signal strength still refreshes every five seconds; actual host scan/association time is unchanged.

## Wi-Fi radio synchronization

The updated frontend and guest radio service are active after a clean VM restart. External macOS power changes update Linux through CoreWLAN notifications, and Linux rfkill changes control host power; both off/on paths passed over Ethernet and the existing network reconnected. An idle sample across three status updates left the radio RPC worker asleep. The owner confirmed fast disconnection through GNOME. The slower association observed in that test led to the separate AP-selection update below. Missing/stale capability retains one-second host-status refresh. Signal quality remains a separate refinement.

## Wi-Fi AP selection and roaming

The updated guest module delegates automatic AP selection to macOS and reports the actual associated AP to Linux. An explicit BSSID pin remains strict. The bridge validates the network and supported security before accepting a host-selected AP, and consumes Wi-Fi revision events for subsequent link changes. The five-second signal deadline also reconciles missed link events. The connection contract is open Wi-Fi or WPA2-PSK/CCMP; enterprise authentication, SAE and required PMF are unsupported.

The module and matching guest files are installed. Three rapid off/on cycles reconnected in 2.4–3.7 seconds with matching host/guest APs and no different-BSSID failures; the Wi-Fi packet-path check passed. One cycle still logged transient macOS operation failures before recovering, so these samples do not establish retry-free or guaranteed reconnection latency. Unit and isolated kernel checks cover roaming, generation races, security and explicit-pin enforcement. Physical roaming between APs has not yet been observed with this update.

## Trackpad input waits

The installed guest input loop uses data/writability events and independent one-second readiness and held-contact deadlines instead of a fixed 100 ms idle check. Private socket and simulated-time tests cover contact acknowledgement, partial input, stale release, blocked replies, EOF and creation failure without changing the desktop's input path. The owner confirmed normal pointer movement, clicks/drag, two-finger scrolling and pinch after activation. A 15-second live sample fell from 194 to 90 context switches; host heartbeat and actual input contribute to these samples, so they are not pure timer counts. The existing host heartbeat and pointer-fallback gates remain; sleep/wake and forced host-side fallback have not been retested with this refinement.

## Camera demand

The camera service and usage helper use blocking event waits while idle. Reader demand starts host capture; reader closure stops it and replaces the last image with generated black frames. Loss of the usage monitor or writer wakes the service for recovery. The existing stream lease, cancellation limits and bounded retries remain, as does FFmpeg's internal worker behavior. The usage-event contract is verified with v4l2loopback 0.15.4; other versions require acceptance.

The updated pair is installed. An 18-second idle sample went from 179 main-loop wakeups and one usage-helper wakeup to zero for both. Two live discard-sink captures delivered host frames and returned to inactive demand; an intentional monitor exit produced one systemd recovery and reaped the old children. No recording was saved. App-specific visual quality and sleep/wake with this refinement remain separate checks.

## Video limits

The retained general VA-API path supports the tested H.264 and VP9 cases. VP9 shared-memory output substantially improved 1080p60 playback. mpv works with `vaapi-copy`; occasional fullscreen 4K60 drops remain possible. Firefox 4K60 is not reliable and may fall back to software or drop many frames. Software support for a codec does not imply host hardware acceleration: do not advertise HEVC, AV1 or VP8 as completed accelerated paths.

The custom Firefox, browser launcher/preload/preferences and queued VA-driver experiment are excluded. The reference browser video results used compatibility glue that is not shipped. The general VA-API path remains available to compatible clients such as mpv; sandboxed-browser hardware decoding is unverified without per-application workarounds.

## Remaining acceptance and portability work

The owner has authorized controlled migration of the existing reference installation. Fresh-account testing remains deferred; no separate setup agent is used. Ashacky.app now links the CocoaSpice display and permission services into one process; its new permission identity has passed attended grants and a restart check. The repo-built app has booted the migrated VM; a complete macOS logout/login after final cleanup still needs acceptance.

1. Apply the generated configs, signed bundles, privileged helper closure and Debian payload in a fresh account/VM, then execute the acceptance checklist. Builds and relocation passed; a clean-account boot and its device behavior remain untested.
2. Establish and test helper/network/device isolation before simultaneous installations. The plan uses per-account helper labels/sockets and binds video to a selected management address; video protocols are not authenticated and this alone is not proof of cross-account isolation.
3. Validate the Debian provisioning recipe, including cloud-init or installer bootstrap, management networking, DKMS/kernel updates, BlueZ ownership, PAM/GDM, GNOME and the VA diversion. The payload has been applied to the existing guest; a fresh guest bootstrap remains unverified.
4. Implement Ubuntu/Fedora and other desktop-version adapters before claiming support. Debian ARM64/GNOME 50 is the reference combination. Browser-specific integration is outside the package.
5. Test update/recovery and receipt-driven migration/uninstall. The reference migration records originals and applied changes in private receipts. A general-purpose automatic migration/uninstall command is not shipped.

Full Xcode is optional for rebuilding upstream shaders/graphics and is not a normal setup prerequisite. Homebrew libraries and Apple SDK versions are recorded build prerequisites, not byte-for-byte reproducibility guarantees. The repository remains a source-and-recipes project with a working reference installation; the instruction audit made no live changes; the later live reference migration is recorded separately in VALIDATION.md.
