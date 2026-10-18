# Installing Bigscreen Beta on the Steam Frame

Four steps: get the original APK, build the patched APK, install it with FramePort, sign in.

## 0. Requirements

- A Steam Frame set up with **FramePort** ([frameport.app](https://frameport.app); the setup is
  summarised in [docs/FRAMEPORT.md](../docs/FRAMEPORT.md#setup)).
- Python **3.10+** and the dependencies: `pip install -r requirements.txt`
  (UnityPy, lz4, capstone).
- About **3.5 GB** free disk space: the APK is 1.2 GB and the build needs ~2 GB of temporary space.

## 1. Get the original APK

Supported build: **`com.bigscreenvr.bigscreen` 0.950.2.a276a2-beta-perma-rooms-beta,
versionCode 4688857**, 1,175,485,713 bytes, sha256
`6cae96955397be3522c238196635a061c5f9da465f032cbe34b817dddbb4a18a`.

Either:

- **From a Quest** that has Bigscreen installed:
  `python3 tools/quest_extract.py -p com.bigscreenvr.bigscreen` (needs `adb` and developer mode), or
- **From the Meta store** with your Meta account (Bigscreen is free; press *Get* on meta.com first):

  ```bash
  node tools/quest_store_fetch.mjs --login          # follow the printed sign-in URL
  node tools/quest_store_fetch.mjs --callback "oculus://..."
  node tools/quest_store_fetch.mjs --app 2497738113633933
  ```

Check it: `sha256sum com.bigscreenvr.bigscreen.apk`. A different version will not work; the
patches target exact code addresses (see [Other versions](#other-versions)).

## 2. Build the patched APK

```bash
python3 bigscreen/build_bigscreen.py path/to/com.bigscreenvr.bigscreen.apk \
    Games/com.bigscreenvr.bigscreen-frame/com.bigscreenvr.bigscreen.apk
```

It takes 2–5 minutes and prints each step. On success it prints the output's sha256. For the
supported input that is **`e3597c526cfa876929162db4a626a1ba99a44e0b0e1250d36115b8b60c6cbb88`**, the
build tested on a Steam Frame.

If your system temp directory is small (or RAM-backed), pass `--tmp some/dir/on/a/disk`.

The script aborts without writing anything if the input is not the supported build, or if any
patch site does not look exactly as expected.

## 3. Install with FramePort

FramePort re-signs the APK and installs it.

- **First install:** keep the patched APK in its own folder (one folder per game, as above), then in
  FramePort **Add games → Scan a folder…**, pick the parent folder, open Bigscreen and **Install on Frame**.
- **Bigscreen already installed via FramePort:** use the game's **Newer APK** action and pick the patched file.

Recommended per-game settings (what the tested install used): **Show 360° backgrounds and
videos** (`equirect_emul`) on; FramePort's defaults for the rest. See [docs/FRAMEPORT.md](../docs/FRAMEPORT.md).

## 4. Sign in

Bigscreen starts in its PC login flow: sign in with a **Bigscreen account** (create one in the app
or on the website). No Meta account is involved on the Frame.

## Troubleshooting

Known problems are listed in the [README](../README.md#known-issues); their log evidence is in
[docs/OPEN_ISSUES.md](../docs/OPEN_ISSUES.md). Reports of new cases help.

Collect logs with Bigscreen's `…` menu in FramePort → **Report a problem**, while the problem is
still visible. Exit Bigscreen afterwards so the session log is written.

## What the patcher changes

13 code patches in `libil2cpp.so` and 5 asset patches in `data.unity3d`. Each is listed with its
reason in [docs/PATCHES.md](../docs/PATCHES.md). Summary:

- login: skip the Meta entitlement check and use Bigscreen's PC account flow;
  no-op clipboard reads (Lepton has no clipboard service);
- video on the Frame's GLES (Mesa/Zink): fix the OES blit shader, disable texture-parameter calls on
  external textures;
- draw every screen **in the 3D scene** instead of as compositor overlay layers (the Frame
  composites quad layers on top of everything): browser, media player, TV channels, room presentation screens;
- per-eye 3D for shared screens; subtitles that stay visible.

## Other versions

Bigscreen updates change every address. To port the patches, find each method listed in
[docs/PATCHES.md](../docs/PATCHES.md) in the new build (see [docs/REVERSING.md](../docs/REVERSING.md)),
update the addresses and fingerprints in `bspatch/code_patches.py` / `bspatch/asset_patches.py`,
and update `SUPPORTED_APK_SHA256`. Contributions welcome.
