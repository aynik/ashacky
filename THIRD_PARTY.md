# Attribution and license boundaries

Ashacky depends substantially on upstream work. Its original glue code uses MIT; existing notices and the licenses below take precedence for adapted or imported material. No upstream framework binaries or VM image are committed to this repository. Setup downloads selected upstream binaries as generated build inputs.

| Material | Origin / license |
| --- | --- |
| `guest/drivers/wifi`, `guest/drivers/battery` | Linux modules, GPL-2.0 as identified in source |
| `guest/drivers/bluetooth` | Linux rfkill module, GPL-2.0-or-later |
| `patches/rockchip-vaapi.patch`, `guest/video` C/H helpers and broker sources | LGPL-2.1-or-later; VA driver and H.264 reconstruction derived from [woodyst/rockchip-vaapi](https://github.com/woodyst/rockchip-vaapi), including Eduardo García-Mádico Portabella's notices |
| `host/video` | LGPL-2.1-or-later as identified in source |
| `host/qemu`, QEMU patches | [QEMU](https://www.qemu.org) / [UTM QEMU](https://github.com/utmapp/qemu); preserve each affected file's license, principally GPL-2.0-or-later |
| CocoaSpice patch and dependency | [utmapp/CocoaSpice](https://github.com/utmapp/CocoaSpice), Apache-2.0, copyright osy; patch records Ashacky changes to display reconnection, ports, USB and rendering |
| Generated `build/host/frontend-generated/keymap.h` | Generated from [keycodemapdb](https://github.com/qemu/keycodemapdb) at the recorded submodule commit; used under its BSD-3-Clause option; generator command and database checksum remain in the header |
| SPICE and GStreamer patches | UTM v5.0.5 patches to the corresponding upstream projects; preserve affected-file LGPL notices and any individual exceptions |
| socket_vmnet dependency | [lima-vm/socket_vmnet](https://github.com/lima-vm/socket_vmnet), Apache-2.0; pinned source only, no installed helper binary included |
| VideoToolbox remote patch/dependency | [davelindo/videotoolbox_remote](https://github.com/davelindo/videotoolbox_remote); modified FFmpeg and Swift codec files retain their upstream licensing, including FFmpeg LGPL/GPL conditions for the selected build |

`upstream/utm-assets.lock.json` records the public UTM release used for prebuilt EGL/GLESv2, epoxy, VirGL, Vulkan loader, MoltenVK and the CocoaSpice Metal shader. The fetcher preserves selected framework/resource contents and verifies their tree hashes. Original component licenses still apply; the UTM download is not a blanket license for all included projects. Matching source submodules and their notices remain available. Any future distributed Ashacky bundle must include the required notices and satisfy the applicable source-distribution obligations.

Exact Git revisions are recorded as submodule entries, with public URLs in `.gitmodules`. `dependencies.json` records patch order; `upstream/archives.lock.json` records release-archive hashes. The graphics submodule pins follow UTM v5.0.5's source manifest, available in `third_party/utm/patches/sources`. UTM's patches remain in that submodule rather than being copied into Ashacky. Their own license/notice files must accompany any future redistributed build. Source attribution alone does not fulfill binary-distribution obligations.

`LICENSES/` retains the GPL, LGPL, Apache and keycodemapdb BSD texts used by this source import. Rebuilding dependencies must preserve their additional individual notices. Do not apply the root MIT license to these upstream materials or to a bundled binary distribution as a whole.

The `upstream/import-provenance.json` historical paths and hashes identify selected reference-source inputs before consolidation edits; they are not reproducible-build hashes or a promise that renamed/parameterized files remain byte-identical.
