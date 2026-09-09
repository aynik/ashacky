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

The instruction/build audit left the working hand-built installation untouched; the subsequent controlled reference migration was separately authorized. Review the instructions and validate builds in isolated output directories; inspect the working installation read-only when needed. Do not launch a separate setup agent, install validation outputs, replace live dependencies, change services or login settings, or restart the working VM as part of this audit. Temporary build-validation copies are not installed runtimes. Check that build destinations are not used by the running installation before writing them.

Fresh-account installation remains deferred until explicitly requested; the reference migration is now authorized. Missing acceptance results are limitations to record, not authorization to deploy. A second macOS account alone does not isolate system helpers or physical devices. When that test is requested, establish distinct helper identities and device/network isolation first. Migration of the existing account and retirement of obsolete runtime files remain separate later steps, preserving its VM and data.
