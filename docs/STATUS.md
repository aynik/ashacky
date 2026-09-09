# Consolidation status

The reference installation was tested on an M1 MacBook Air, macOS 15.7.3, Debian forky/sid ARM64, GNOME 50 and the Debian 7.1 kernel family. That is evidence for this combination, not a supported-platform matrix for every Apple Silicon Mac or distribution.

## User-verified in the reference installation

- Direct Linux desktop after macOS login; Finder and Dock absent.
- Linux logout → macOS logout; coordinated power operations.
- Sleep/wake and bidirectional lock/unlock synchronization.
- Touch ID for lock-screen authentication.
- Wi-Fi scan/connect and Bluetooth device control through GNOME.
- Trackpad and AirPods connected through host Bluetooth; audio endpoint switching and microphone input.
- Built-in camera bridge and SPICE audio.
- Retina resolution, external display and monitor hotplug.
- Trackpad motion, scrolling and Firefox pinch-to-zoom.
- OpenGL/Vulkan rendering and light gaming.
- USB storage hotplug, reads, writes and reformatting after the explicit-root-port fix.

Upstream sources are now pinned submodules with Ashacky patches applied during preparation. Source recipes prepare from pinned inputs. The documented builds cover the guest modules and VA/FFmpeg stack, SPICE/GStreamer, QEMU, render server, frontend and macOS helpers. The assembled runtime passes relocation, dependency, signature, GPU-shader and silent audio checks without a VM. The audit and its limits are recorded in [VALIDATION.md](VALIDATION.md).

The controlled reference migration has installed the repository-built guest payload with private rollback receipts. Its core services and H.264/VP9 `vaapi-copy` smoke tests pass against the original host. The first host cutover failed before QEMU started because the root runtime parent lacked group traversal permission. The watchdog restored the old host automatically. The reviewed fix initializes that parent explicitly, removes the custom permission panel and consolidates user services under one LaunchAgent; another attended host login is pending. Source parameterization, a changed account, a clean guest, build dependency changes and regenerated signing identities still require acceptance tests.

## Video limits

The retained general VA-API path supports the tested H.264 and VP9 cases. VP9 shared-memory output substantially improved 1080p60 playback. mpv works with `vaapi-copy`; occasional fullscreen 4K60 drops remain possible. Firefox 4K60 is not reliable and may fall back to software or drop many frames. Software support for a codec does not imply host hardware acceleration: do not advertise HEVC, AV1 or VP8 as completed accelerated paths.

The custom Firefox, browser launcher/preload/preferences and queued VA-driver experiment are excluded. The reference browser video results used compatibility glue that is not shipped. The general VA-API path remains available to compatible clients such as mpv; sandboxed-browser hardware decoding is unverified without per-application workarounds.

## Remaining acceptance and portability work

The owner has authorized controlled migration of the existing reference installation. Fresh-account testing remains deferred; no separate setup agent is used. Ashacky.app now links the CocoaSpice display and permission services into one process; its new permission identity has passed attended grants and a restart check. Host cutover still needs logout/login acceptance.

1. Apply the generated configs, signed bundles, privileged helper closure and Debian payload in a fresh account/VM, then execute the acceptance checklist. Builds and relocation passed; boot, permissions and device behavior from this new layout have not been tested.
2. Establish and test helper/network/device isolation before simultaneous installations. The plan uses per-account helper labels/sockets and binds video to a selected management address; video protocols are not authenticated and this alone is not proof of cross-account isolation.
3. Validate the Debian provisioning recipe, including cloud-init or installer bootstrap, management networking, DKMS/kernel updates, BlueZ ownership, PAM/GDM, GNOME and the VA diversion. The payload has been applied to the existing guest; a fresh guest bootstrap remains unverified.
4. Implement Ubuntu/Fedora and other desktop-version adapters before claiming support. Debian ARM64/GNOME 50 is the reference combination. Browser-specific integration is outside the package.
5. Test update/recovery and receipt-driven migration/uninstall. The reference migration records originals and applied changes in private receipts. A general-purpose automatic migration/uninstall command is not shipped.

Full Xcode is optional for rebuilding upstream shaders/graphics and is not a normal setup prerequisite. Homebrew libraries and Apple SDK versions are recorded build prerequisites, not byte-for-byte reproducibility guarantees. The repository remains a source-and-recipes project with a working reference installation; the instruction audit made no live changes; the later controlled guest migration is recorded separately in VALIDATION.md.
