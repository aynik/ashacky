# Build and consolidation validation

Performed on 2026-09-08. No reference VM, installed service, login setting or production executable was replaced.

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

## Remaining validation limits

These checks establish functional build/staging reproduction with the recorded host SDK and existing Homebrew prerequisites, not byte-identical output or clean-machine installation. No generated app was used to boot a VM, and no generated privileged helper, PAM/GDM change, DKMS package, service or login configuration was installed. The cloud-init seed attachment, fresh-account boot, hardware/session behavior after parameterization, other distro adapters, and receipt-driven migration/uninstall still require attended acceptance. See [INSTALL.md](INSTALL.md).

The host codec compiles in Swift 5 mode with concurrency warnings that require attention before Swift 6 mode. The patched H.264 server emits a Swift pointer-conversion warning at its VideoToolbox hardware-property query, and CocoaSpice emits existing Objective-C warnings. The optional source shader rebuild remains unvalidated because the Metal compiler is absent; the normal release-asset build does not need it.
