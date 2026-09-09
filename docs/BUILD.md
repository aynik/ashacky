# Build the consolidated source

Build outputs stay in the checkout's `build/` directory. The build commands compile components without deploying a runtime or starting the VM; the prerequisite package command installs development tools and libraries. Use Python 3.12+, Git and patch for dependency preparation. These are functional reproduction recipes, not a promise of byte-identical outputs across SDKs or package-manager versions.

## Initialize upstream sources

From a Git clone of Ashacky, initialize its pinned submodules before building:

```sh
git submodule update --init --recursive --depth 1
```

A clone made with `--recurse-submodules` already initializes them. Recursive initialization is required: SPICE includes nested source dependencies. `.gitmodules` supplies public URLs; the Git entries record exact commits. A repository ZIP does not contain those checkouts. Upstream projects retain their own notices and histories.

On macOS, Apple's `/usr/bin/python3` may be older than required. Put the chosen Python 3.12+ and `pkg-config` on PATH before invoking `./ashacky`; verify with `./ashacky inventory`. For an Apple Silicon Homebrew installation this normally means adding `/opt/homebrew/bin` to PATH, including in non-interactive SSH build commands. Runtime LaunchAgents must use discovered absolute executable paths.

`./ashacky check` runs on either OS. Linux integration tests for evdev, inotify, GIO, PipeWire monitoring, Wi-Fi request readiness, camera lifecycle and the private Bluetooth bus are reported as skipped on macOS. On Linux, use the distro's Python with its evdev and PyGObject/GIO bindings. Install `dbus-daemon` so the private-bus check runs instead of being skipped; that test uses `dbus-run-session` and never claims the live system bus. Wi-Fi readiness tests use pipes and private socket fixtures, not the installed module or physical radio; compile and live module checks remain separate. Camera tests likewise use generated pixels, private sockets and child pipes, with no camera device or host capture.

The ANGLE dependency is part of UTM's WebKit fork and can be large. An optional sparse checkout inside `third_party/angle-webkit` can keep only `Source/ThirdParty/ANGLE`, `Configurations` and `Tools/ccache` in its working tree; the recorded commit must remain unchanged.

## Guest components

Build inside Linux with the target distro's compiler, libc, PAM, libva and kernel headers. The reference build/check uses `cc`, `make`, `pkg-config`, Python with `evdev` and PyGObject/GIO, a private D-Bus daemon, PAM/json-c development headers, libva/GBM/EGL/GLES development headers, LZ4 and Zstandard development headers. Kernel headers must match the intended kernel; do not ship the compiled module from another guest.

For the Debian reference, the userspace build/check/staging packages are:

```sh
sudo apt install build-essential git patch pkg-config python3 python3-evdev python3-gi dbus-daemon patchelf \
  libpam0g-dev libjson-c-dev libva-dev libgbm-dev libegl-dev libgles-dev \
  liblz4-dev libzstd-dev
```

This is not the guest runtime package list. The kernel build additionally needs matching `linux-headers-$(uname -r)`; DKMS, v4l2loopback and desktop runtime dependencies belong to provisioning.

```sh
./ashacky check
./ashacky build guest
./ashacky build guest --kernel "$(uname -r)"
```

This prepares the pinned rockchip-vaapi source with Ashacky's patch and compiles it with the maintained `guest/video` helpers. It produces the PAM module, camera-reader utility and VA driver. The kernel option builds Wi-Fi, Bluetooth rfkill and battery modules in `build/guest/drivers`. The distribution supplies v4l2loopback.

For a camera-only update, rebuild just its usage helper inside the guest:

```sh
mkdir -p build/guest
cc -O2 -Wall -Wextra guest/devices/camera_readers.c -o build/guest/camera-readers
```

Stage this binary with `camera_demand.py` from the same checkout. The build needs the distro's Linux UAPI headers; runtime additionally requires the v4l2loopback client-usage event verified with version 0.15.4. The helper does not replace or rebuild v4l2loopback. See [camera update and acceptance](DEVELOPMENT.md#updating-camera-demand-events).

### Optional Wi-Fi kernel link check

The normal source checks cover AP selection, security validation, link generations and status-file notifications with fixtures. To exercise the actual cfg80211 link ABI, this separate check builds a renamed copy of the current module for a dummy NIC in a fresh network namespace. It needs the running kernel's headers, `cc`, `make`, `ip` (iproute2), `unshare` (util-linux) and the module utilities. Build as the ordinary user, then explicitly run with root:

```sh
python3 tools/check-wifi-link-kernel.py
sudo python3 tools/check-wifi-link-kernel.py --run
```

The run verifies its recorded build hashes, creates a separate wiphy and misc device, exercises synthetic association/roaming/pin/security cases, then unloads the fixture. It does not call macOS, change the physical radio or use a saved network profile. The namespace isolates network state, not the kernel: use this only where loading a test module is appropriate. It is not part of `./ashacky check`, does not install the production DKMS module, and cannot establish physical roaming behavior.

### H.264 decoder build

H.264 needs the pinned remote FFmpeg decoder, not a stock FFmpeg library:

```sh
python3 tools/prepare-source.py videotoolbox-remote
mkdir -p build/ffmpeg
cd build/ffmpeg
../sources/videotoolbox-remote/ffmpeg/configure \
  --prefix="$PWD/../ffmpeg-prefix" \
  --disable-everything --disable-autodetect --disable-doc --disable-debug --enable-ffmpeg \
  --enable-network --enable-protocol=file,pipe,tcp \
  --enable-demuxer=mov,h264,hevc --enable-parser=h264,hevc \
  --enable-decoder=h264,hevc,h264_videotoolbox_remote,hevc_videotoolbox_remote \
  --enable-encoder=rawvideo --enable-muxer=rawvideo,null,framemd5 \
  --enable-filter=buffer,buffersink,format,scale \
  --enable-bsf=h264_mp4toannexb,hevc_mp4toannexb \
  --enable-liblz4 --enable-libzstd --enable-shared --disable-static \
  --enable-videotoolbox-remote
make -j3
make install
cd ../..
./ashacky build guest --ffmpeg-prefix "$PWD/build/ffmpeg-prefix"
```

The recipe disables optional library auto-detection so installed system graphics libraries do not silently become decoder dependencies. The broker build checks that `h264_videotoolbox_remote` is present. Its local build rpath points at the specified prefix. The Debian staging adapter installs the matching private library closure into the payload and rewrites its relative loader paths; the future installed location is `/opt/linuxhost-video/lib`. Enabling HEVC in this private FFmpeg dependency does not add HEVC profiles to the VA driver.

## Stage a Debian payload

After the guest and remote FFmpeg builds, generate/transfer the private installation plan described in PROVISIONING.md and run:

```sh
python3 tools/stage-guest.py --ffmpeg-prefix build/ffmpeg-prefix --plan build/plans/candidate
```

This Debian ARM64 adapter requires `patchelf`. It stages `build/guest-root` and manifests/actions for review; it never writes installed system files. The private FFmpeg library closure and broker receive relative runtime paths. PAM, VA, DKMS, service and GNOME integration files are staged, while the PAM/GDM merges, VA diversion, service activation and module installation remain explicit future installation actions. See PROVISIONING.md for those exact steps and recovery receipts.

## macOS components

Normal setup uses Apple Command Line Tools; full Xcode and the Metal compiler are not required. Install Command Line Tools with `xcode-select --install` if absent. For a new Apple Silicon installation with Homebrew, the development dependencies are:

```sh
brew install python@3.14 pkgconf ninja bison glib pixman jpeg-turbo openssl@3 \
  opus lz4 zstd libusb usbredir json-glib gettext
export PATH="/opt/homebrew/bin:$PATH"
```

Do not run package installs/upgrades on the working reference host as part of the audit. The isolated audit used its already-installed Homebrew libraries. The recipes select the checkout's private SPICE/GStreamer prefix before Homebrew and use Homebrew's SDK pkg-config shims for system zlib. Build-time Python packages are pinned in `upstream/build-requirements.txt` and installed into `build/python`. Compiler and resolved package versions are recorded in `build/runtime/toolchain.json`. Homebrew libraries and the Apple SDK are external prerequisites, not immutable source pins.

### Complete local runtime build

```sh
python3 tools/build-runtime.py all --jobs 3
python3 tools/assemble-runtime.py
python3 tools/check-runtime.py
```

These commands build and stage `build/host/Ashacky.app` and network helpers. They do not install services, register an app, change login settings or start a VM. Use an isolated checkout for validation if any build outputs are part of a running installation. The builders refuse redirected output directories and detected use of the host outputs.

The individual build stages are `deps`, `render-server`, `qemu`, `network`, `frontend`, and `helpers`, in that order. Run `deps` first to establish the Python environment and library prefix. Logs go to stdout/stderr; keep captured logs under ignored `build/`. Rebuild `frontend` before assembling again after dependency changes. A changed source-preparation receipt requires removing only that inactive component's generated source and build directory, as described in DEVELOPMENT.md.

The app icon source is `host/frontend/assets/Ashacky.png`, a tightly fitted 1024×1024 PNG with transparent corners. Keep the outer background transparent when replacing this asset. The source checker accepts only this specific RGBA icon and rejects embedded EXIF/text metadata; remove that metadata before committing a replacement. The frontend build uses macOS `sips` and `iconutil` to generate the standard icon sizes and package `Ashacky.icns` in the app's Resources directory, with `CFBundleIconFile` selecting it. Replace the source PNG and rebuild `frontend` and the assembled bundle to update the icon.

`check-runtime.py` copies the generated app to a temporary path containing spaces, verifies signatures and all Mach-O dependencies, queries QEMU's devices/HVF/GPU properties, loads the graphics and codec libraries, tests GStreamer with a silent `fakesink`, and creates the Metal shader pipeline. It opens no desktop window, VM, audio device, microphone, camera, network listener or privileged service. This validates relocation and component availability; it does not validate a fresh guest boot.

The Wi-Fi observer uses the app's existing signature and permission identity. Do not add the restricted `com.apple.wifi.events` entitlement to an ad-hoc signed build: the reference probe was rejected by AMFI with it. Event delivery without that added entitlement was observed on the reference macOS; another release must verify delivery or use the capability fallback. See the Wi-Fi radio section in ARCHITECTURE.md.

### Frontend-only validation while the VM runs

After a complete runtime build, a frontend-only change can reuse the existing pinned dependencies in a temporary output directory. From the checkout root, use the same Homebrew installation as the runtime build. The initial PATH below includes the standard Apple Silicon Homebrew location so it also works in a non-interactive SSH shell; adjust that entry if Homebrew is elsewhere.

```sh
(
  set -e
  export PATH="/opt/homebrew/bin:$PATH"
  ashacky_build_brew="$(brew --prefix)"
  export PATH="$ashacky_build_brew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  unset PKG_CONFIG_LIBDIR
  export PKG_CONFIG_PATH="$PWD/build/runtime/prefix/lib/pkgconfig:$PWD/build/runtime/prefix/share/pkgconfig:$ashacky_build_brew/opt/openssl@3/lib/pkgconfig:$ashacky_build_brew/opt/libffi/lib/pkgconfig"
  test ! -e build/frontend-review
  python3 tools/build-frontend.py --output build/frontend-review
  python3 tools/assemble-runtime.py --app build/frontend-review/Ashacky.app
  python3 tools/check-runtime.py --app build/frontend-review/Ashacky.app
)
```

This validates the candidate without replacing or launching the installed app. Do not run the canonical `build-runtime.py frontend` stage against an active runtime. The temporary recipe is for frontend code only: changed dependencies, QEMU or helpers require the corresponding stopped or isolated full build. Use a fresh review directory; inspect any existing one before removing it.

Follow DEVELOPMENT.md for guest updates and the clean session stop. Switch the validated bundle into the installation's existing canonical app path only after the supervisor, QEMU and frontend have stopped and released the disk/app. Start it through the existing session job and LaunchServices launcher. Retain a temporary recovery copy until attended acceptance, then remove it and the temporary review objects and update the private receipt. This adds no permanent runtime version store or additional app identity.

### Host helpers

```sh
./ashacky build host
./ashacky build host --installation /absolute/private/helper-build.json
```

The first command compiles VideoToolbox shared decoding. The frontend build links host control, lock synchronization and Wi-Fi/Bluetooth/audio/camera services into the Ashacky executable. There are no standalone SessionSync or LinuxHostControl executables. `check-runtime.py` runs the bundled lock-transition check and tests session-command cleanup, token validation and the clean-shutdown gate without locking/unlocking a real session or performing power actions. It also verifies that Metal's SPICE connection advertises IOSurface scanout even when SPICE was built without EGL. The second command also compiles root power and USB helpers with installation-specific constants. `tools/plan-installation.py` generates this input alongside reviewable service/config files (see PROVISIONING.md). Example schema:

```json
{
  "userID": 501,
  "groupID": 20,
  "usbRuntime": "/var/run/ashacky-501-usb",
  "powerRuntime": "/var/run/ashacky-501-power",
  "powerPolicy": "/Library/Application Support/Ashacky/501/power-policy.json",
  "powerClients": ["/absolute/checkout/build/host/Ashacky.app/Contents/MacOS/Ashacky"]
}
```

Replace IDs and paths by discovering the actual target account and its dedicated integration group. Use the canonical executable path reported by the OS; the power helper checks the real peer executable, not a claimed PID. This example is a build input, not permission to start root helpers at checkout paths. Install helpers and their dependencies into root-controlled locations before any root service runs them. Generate a root-owned power policy with `userID`, `enabled` and `allowOtherSessions` and a matching unprivileged control `powerSocket`.

After compiling the installation-specific helpers, stage their libraries into a root-installable directory:

```sh
python3 tools/assemble-privileged.py
```

This produces `build/host/PrivilegedHelpers` containing power, USB, socket_vmnet and Wi-Fi-interface discovery executables plus their relocated library closure. It signs and verifies each binary without running the helpers. The whole directory must later be installed root-owned and unwritable by the user; do not symlink privileged code or its libraries back to a user-writable checkout. The generated `network.sh` and power policy are separate installation inputs. See PROVISIONING.md for placement.

The source currently builds in Swift 5 language mode. The codec helper emits concurrency warnings with the current Swift compiler; upgrading to Swift 6 strict concurrency needs a separate review.

### H.264 host decoder

H.264 additionally needs the patched `vtremoted` server. `LinuxHostVideoShared` handles the retained VP9 shared-memory path; it does not replace this server. Build it separately on macOS 13+ with Swift 5.9+:

```sh
python3 tools/prepare-source.py videotoolbox-remote
swift build --package-path build/sources/videotoolbox-remote/vtremoted \
  --scratch-path build/host/vtremoted-build -c release
```

The executable is `build/host/vtremoted-build/release/vtremoted`. Include it in the final bundle; the VM supervisor starts it alongside `LinuxHostVideoShared`, without a separate LaunchAgent. The H.264 listener must match `LH_VIDEO_H264_HOST` in the guest's `/etc/ashacky/video.env`. Use an isolated VM address and the selected port (5557 in the reference topology), never the upstream LAN-wide service-install example. Both LZ4 and Zstandard are dynamically loaded by this server. The bundle patch tries executable-relative library paths first; assembly includes their matching dylibs and stable aliases. The default upstream listener is port 5555, so simply starting it without configuration does not match the guest broker.

## Pinned UTM binary assets

On macOS, fetch the selected graphics frameworks and CocoaSpice shader from the public UTM 5.0.5 release:

```sh
python3 tools/prepare-utm-assets.py
```

The approximately 303 MB download is cached as `build/downloads/UTM.dmg`. `upstream/utm-assets.lock.json` pins the release URL, byte count, archive SHA-256 and component tree hashes. The tool verifies the download, mounts it read-only without opening Finder, copies only the allowlisted components to `build/upstream/utm`, then unmounts it. It does not install or launch UTM and does not read an installed copy from `/Applications`.

The extracted frameworks are EGL, GLESv2, MoltenVK, epoxy, VirGL and the Vulkan loader. Framework structure and relative symlinks are preserved. The cache remains pristine; stage copies into the final bundle before any architecture thinning, loader-path edits or signing. The shader bundle's source/header hashes must match the prepared CocoaSpice source, so a shader change cannot silently use an incompatible old binary. See [THIRD_PARTY.md](../THIRD_PARTY.md) for provenance and license boundaries.

These assets do not contain our patched standalone QEMU executable or `virgl_render_server`. The runtime builder compiles those targets with Command Line Tools and the assembler stages their dependencies. Fetching the assets alone does not produce a runnable VM.

## Pinned QEMU / SPICE / graphics sources

`dependencies.json` defines source preparation and patch order. Submodule commits are pinned by Git, with no duplicate revision lockfile. Prepare individual dependencies as needed:

```sh
python3 tools/prepare-source.py qemu
python3 tools/prepare-source.py cocoaspice
python3 tools/prepare-source.py spice-server
python3 tools/prepare-source.py spice-protocol
python3 tools/prepare-source.py spice-gtk
python3 tools/prepare-source.py gstreamer
python3 tools/prepare-source.py gst-plugins-base
python3 tools/prepare-source.py gst-plugins-good
python3 tools/prepare-source.py socket-vmnet
```

The command prints the source path to use. Unmodified dependencies use `third_party/<name>` directly; dependencies requiring patches are exported to `build/sources/<name>`, including nested sources, and patched there. Configure their build outputs outside the pristine submodule tree. Preparation checks that every input submodule matches its recorded pin and has no local changes, then records input pins and patch hashes. Changed inputs require explicitly removing and rebuilding that component's generated source directory. See DEVELOPMENT.md for the update workflow.

UTM's patches are read from its pinned submodule; only Ashacky's additions are in `patches/`. The VA driver and H.264 support are reconstructed from rockchip-vaapi with `patches/rockchip-vaapi.patch`. The frontend keymap is generated from the keycodemapdb submodule into `build/host/frontend-generated/keymap.h` during the frontend build. Neither upstream source copies nor the generated header are maintained in Ashacky.

QEMU source uses a release archive recorded with its SHA-256 in `upstream/archives.lock.json`: UTM's release includes bundled sources and the firmware used by the reference installation. The preparation tool verifies the archive checksum and omits only its pinned EDK2 emulator external X11 header symlink; all other entries pass Python's safe data extraction filter. The ARM VM firmware does not use that emulator link.

QEMU is UTM's 10.0.12 source plus its v5.0.5 patch series, the captured local display reconnect fixes and the maintained `linuxhost-shmem` device. The original implementation also contains Cocoa-window changes, retained in the local patch for source fidelity; the current UI uses the separate SPICE frontend. Shared-memory source and the Meson registration must both be present.

The critical QEMU build features are `aarch64-softmmu`, HVF, SPICE with GL, OpenGL, VirGL, Venus/Neptune and USB redirection. Preserve `-accel hvf,ipa-granule-size=0x1000` at runtime and the device layout in `host/session/session.py`; these are required by the tested stock-Mesa path. Do not reuse older SPICE-disabled configure commands.

The reference SPICE server used protocol 0.14.4, server 0.14.3 with UTM patches, Opus, and disabled GStreamer server streaming/SASL/smartcard. The client used patched spice-gtk 0.42 with GThread coroutines, Opus, LZ4 and USB redirection. GStreamer 1.19.1 built app, audioconvert, audioresample, audiotestsrc, volume, typefind, playback, osxaudio, autodetect and level plugins. The runtime builder implements these options with optional features disabled and Meson dependency downloads disabled. USB redirection is explicitly enabled in the client. Release-version patches supply the metadata absent from Git exports so SPICE cannot accidentally identify itself using the Ashacky repository version.

The graphics submodule pins follow `third_party/utm/patches/sources` and remain available for source inspection or optional upstream rebuilds. Normal setup uses the verified frameworks in `build/upstream/utm/Frameworks`. The runtime builder generates epoxy headers from the pinned XML registries and stages matching ANGLE/VirGL headers for locally compiled consumers. Retain upstream notices. `third_party/utm/scripts/build_dependencies.sh` supplies reference build flags, but should not be run wholesale for normal setup because its ANGLE/MoltenVK source builds require Xcode.

## Frontend and bundle assembly

For a non-disruptive frontend review with the dependencies already built, use a temporary output inside `build/`. Set the same `PATH` and `PKG_CONFIG_PATH` shown below before running:

```sh
python3 tools/build-frontend.py --output build/frontend-review
python3 tools/assemble-runtime.py --app build/frontend-review/Ashacky.app
python3 tools/check-runtime.py --app build/frontend-review/Ashacky.app
```

This does not select or launch a runtime. Keep the installed app at its existing canonical path. Remove temporary review outputs after validation or a coordinated clean-stop replacement; they are not a version store. Without these options, the normal output remains `build/host/Ashacky.app`.

The `frontend` stage selects the private dependency prefix automatically. For a manual component build, use the same environment and run:

```sh
python3 tools/build-frontend.py
```

This compiles the frontend from source with Command Line Tools and copies the verified UTM shader into `build/host/Ashacky.app`. It prepares the pinned UTM assets automatically if needed. Rebuilding the shader is an explicit `--compile-shaders` option for maintainers with the Metal compiler; it is not part of normal setup.

Check that the copied shader loads and forms a rendering pipeline on the host GPU without starting a window or VM:

```sh
clang -fobjc-arc -O2 tests/metal-shader.m -framework Foundation -framework Metal \
  -o build/host/metal-shader-check
build/host/metal-shader-check "$PWD/build/host/Ashacky.app/Contents/Resources/CocoaSpice_CocoaSpiceRenderer.bundle"
```

The final bundle needs the frontend, control and codec executables (both `LinuxHostVideoShared` and `vtremoted`); QEMU and `virgl_render_server`; firmware from the verified QEMU/EDK2 source distribution (see `upstream/firmware.json`); the graphics/SPICE/GStreamer dependency closure; GStreamer plugins; and a bundle-relative MoltenVK ICD. Preserve framework structure, rewrite install names/rpaths deliberately, audit `otool -L` and dynamically loaded libraries, and sign nested code before the completed app. QEMU needs the provided hypervisor entitlement. Bundled source-generated resources are allowed; references to mutable files outside a signed bundle are not assumed to satisfy signing.

`assemble-runtime.py` implements that closure: it accepts only libraries from the checkout's generated build inputs and Homebrew, preserves the six framework structures, relocates dependent libraries, removes developer rpaths, carries GStreamer plugins and codec dlopen libraries, and signs nested code before the app. It includes source/license notices and private dependency provenance. Its minimum macOS version is raised to the highest deployment target among the bundled binaries; a Homebrew bottle can raise that requirement above the frontend source's baseline. The source repository does not redistribute those binaries.

The complete bundle was built and passed the non-VM relocation checks. Launching it with a fresh guest, hardware acceptance and new-account login behavior remain deliberately untested. No build reads the old installed UTM app or reference runtime directories.

## Optional: rebuild shaders and graphics from source

This is an optional maintainer workflow, not a requirement for setting up Ashacky. To modify the shader itself, run `python3 tools/build-frontend.py --compile-shaders` with Apple's compiler. Rebuilding upstream ANGLE/MoltenVK through their Xcode recipes also requires full Xcode. Normal setup fetches their pinned binaries instead.

1. Install an Xcode release compatible with the host macOS using [Apple's downloads](https://developer.apple.com/download/all/) and [compatibility table](https://developer.apple.com/xcode/system-requirements). For the reference macOS 15.7.3, [Xcode 26.3 requires macOS 15.6 or later](https://developer.apple.com/documentation/xcode-release-notes/xcode-26_3-release-notes); this establishes host compatibility, not a completed Ashacky build test. Do not assume the newest App Store release supports the host.
2. Open Xcode once and complete its license and initial component setup.
3. Select its actual developer directory for this build shell. Adjust the example if Xcode has a different name or location:

```sh
export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
xcodebuild -version
```

For Xcode versions with a separately downloaded Metal Toolchain, install it through Xcode's Components settings or run:

```sh
xcodebuild -downloadComponent metalToolchain
```

This is [Apple's documented component installation](https://developer.apple.com/documentation/xcode/downloading-and-installing-additional-xcode-components). Older Xcode releases may already include the compiler. Confirm the tools are discoverable and the compiler starts:

```sh
xcrun --sdk macosx --find metal
xcrun --sdk macosx --find metallib
xcrun --sdk macosx metal --version
```

Keep `DEVELOPER_DIR` in the environment for subsequent builds, including SSH builds. It selects the toolchain for these commands without changing the system-wide `xcode-select` setting. Merely finding a tool path is not proof that a downloadable component is installed; the actual shader compilation in `tools/build-frontend.py` must succeed. Record Xcode/compiler versions with build validation results.

The complete source-rebuild path remains unvalidated. Record its results separately from the normal Command Line Tools build with release assets.

## Validated during this consolidation

See `VALIDATION.md` for actual checks performed on this source revision. Component compilation does not prove fresh provisioning, permissions, login behavior or visual rendering. Those require the new-account acceptance round described in INSTALL.md.
