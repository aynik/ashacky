# Ashacky Linux

A dedicated macOS account that opens directly into a Linux desktop, with macOS handling the hardware underneath.

Ashacky combines QEMU/HVF, one Ashacky.app containing the CocoaSpice frontend and macOS device services and a small set of Linux integrations. The working prototype runs Debian/GNOME on an M1 MacBook Air, including accelerated graphics, native Wi-Fi and Bluetooth controls, audio device selection, camera, multitouch, USB storage and synchronized sessions.

**This repository is the first source consolidation. The existing installation works; a fresh installation from this checkout has not yet been validated.** Build/staging recipes and validation limits are listed in [BUILD.md](docs/BUILD.md) and [STATUS.md](docs/STATUS.md). The reference installation now runs from this checkout; fresh installations still require attended acceptance.

## Intended installation

1. Create a dedicated macOS user, for example `linux`, and log into it.
2. Give your setup agent sudo access. Passwordless sudo can simplify setup; the agent must inspect the actual sudoers include configuration and validate changes with `visudo`. Runtime does not need passwordless sudo.
3. Clone this repository somewhere permanent in that user's home directory, including its submodules (`git clone --recurse-submodules <repository-url>`).
4. Tell your agent: **“Read README.md and install Ashacky for the current macOS user, using my preferred Linux distribution.”**
5. Complete macOS permission prompts and attended device/login checks when requested.

The agent's workflow is in [INSTALL.md](docs/INSTALL.md), with concrete configuration and Debian steps in [PROVISIONING.md](docs/PROVISIONING.md). Host builds, bundle relocation and offline configuration/payload generation have been checked; applying them in a fresh account remains unverified. Debian/GNOME is the initial integration target; Ubuntu and Fedora are planned adapters, not tested support claims.

Video integration is the general VA-API driver and decoder service. Browser-specific launchers, preload helpers and settings are excluded; sandboxed-browser acceleration is not guaranteed.

An optional [virtual FIDO2 prototype](docs/FIDO2.md) connects Linux passkey clients to Secure Enclave signing with native Touch ID/Mac password authorization. It is disabled by default. The reference Firefox registration, sign-in and Mac password-fallback checks pass; remaining native/restart acceptance is tracked in the guide. It does not unlock GNOME Keyring.

Full Xcode is not a setup requirement. Ashacky compiles its host code with Command Line Tools and fetches pinned graphics frameworks and shaders from a checksummed UTM release. UTM is used as a download source; its app is not installed or launched.

## One checkout

The repository is the source of truth. Upstream projects are pinned Git submodules in `third_party/`; Ashacky's changes to them live in `patches/`. Scripts run from the checkout; current binaries and generated patched sources live under the ignored `build/` directory. Unmodified dependencies build directly from their submodule sources. There is no version store or active-version selector.

Use links only where an OS integration location needs them. Root-owned helpers, Linux kernel/PAM/VA modules and service definitions require installed outputs with appropriate permissions. Those outputs must be generated from this tree and recorded, never edited as separate maintained source copies. Signed macOS bundles remain coherent build outputs.

VM disks, firmware variables, account configuration, keys, tokens, logs and user files live outside tracked source. Updating the checkout must be coordinated with stopping the affected services and VM.

## Repository commands

```sh
git submodule update --init --recursive --depth 1
./ashacky inventory
./ashacky check
./ashacky build guest
./ashacky build host
```

These commands initialize sources, inventory, check or compile components; they do not install anything, restart services, open a VM or change login behavior. Build on the corresponding OS. See [BUILD.md](docs/BUILD.md) for the complete runtime build, bundle assembly, relocation checks and Debian payload staging. `tools/plan-installation.py` generates private review files without applying them. Download Git sources rather than a repository ZIP, which omits submodules.

## Components

| Directory | Responsibility |
| --- | --- |
| `host/frontend` | Borderless Metal/SPICE display, input, clipboard, audio and monitor handling |
| `host/session`, `host/control` | VM supervision, host authentication, power coordination and macOS screen locking |
| `host/devices`, `host/transport` | Host device services, private SPICE control and optional administrative SSH helpers |
| `helpers` | Restricted privileged power and USB helpers |
| `guest/drivers`, `guest/devices`, `guest/input` | Standard Linux device interfaces and management bridges |
| `guest/auth`, `guest/gnome`, `guest/libexec` | Touch ID PAM and desktop/session integration |
| `guest/video`, `host/video` | General VA-API driver and VideoToolbox decoding |
| `third_party` | Pinned upstream submodules, including UTM's existing patch collection |
| `patches` | Ashacky changes applied to upstream sources during preparation |
| `host/qemu` | Ashacky's shared-memory device implementation |
| `upstream` | Checksummed release assets, firmware and historical source provenance |
| `tools`, `tests` | Local build and verification tools |

Read [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the implemented boundaries, [DEVELOPMENT.md](docs/DEVELOPMENT.md) for updates and [THIRD_PARTY.md](THIRD_PARTY.md) for licensing. Existing `linuxhost` identifiers are retained where they form paths, device names or protocol contracts; renaming those is not required to use the Ashacky project name.
