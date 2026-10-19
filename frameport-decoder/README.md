# 10-bit HEVC for FramePort's video decoder

Bigscreen decodes video through Android's MediaCodec. On the Frame that is FramePort's shared decoder
plugin (`OMX.frameport.hevc/avc/vp9.decoder`, FramePort `native/hevc`). In FramePort 1.2.1 an HEVC Main 10
(10-bit) video stays at 0:00 and then blocks every later video until the app restarts. This folder holds a
change to that plugin that makes Main 10 play, and scripts to build and install it. It is not part of
FramePort yet. Background: [docs/OPEN_ISSUES.md](../docs/OPEN_ISSUES.md#5-10-bit-video-hevc-main-10-fixed-in-a-frameport-decoder-change-not-yet-upstream).

## What the patch changes

The Frame's hardware decoder (Qualcomm Iris, kernel 6.18) decodes Main 10 only into its compressed `Q10C`
layout; the plugin always asks for NV12, and a Main 10 stream in such a session returns no pictures.
[`0001-hevc-decode-Main-10-in-software.patch`](0001-hevc-decode-Main-10-in-software.patch), against
FramePort v1.2.1 (`f0eb7850`):

- HEVC streams whose SPS profile is beyond Main go straight to the FFmpeg software decoder that the plugin
  already contains (it falls back to it when the hardware is busy);
- its 10-bit pictures are rounded to 8-bit before the plugin's existing outputs (HDR10 is not tone-mapped);
- the plugin lists Main 10 (level 5.1), so players select it;
- decoder revision 8 → 9.

8-bit HEVC, H.264 and VP9 keep the hardware path. Software decoding on the Frame's CPU (4 threads, as the
plugin uses): a real 1080p Main 10 episode at 232 fps, a synthetic 4K Main 10 clip at 72 fps.

## Build

Needs Linux x86-64, Python 3.12+, git, make, perl, a host C compiler, rsync/ssh, the Android
[NDK r27c](https://dl.google.com/android/repository/android-ndk-r27c-linux.zip) (exactly 27.2.12479018,
which FramePort's builder requires) and the Frame set up with FramePort.

```bash
python3 frameport-decoder/build_decoder.py --ndk path/to/android-ndk-r27c --out work/decoder --verify-toolchain
```

It clones FramePort v1.2.1 (checking the commit), copies Lepton's Android libraries from the Frame over SSH
(FramePort's key; or pass `--lepton-root`), applies the patch and runs FramePort's own builder, which
downloads and checks FFmpeg 7.1.1. `--verify-toolchain` first builds the unpatched plugin and requires
FramePort's exact binary (`141de01f…`); the builder is deterministic, and the patched result should be
the build tested on the headset (`libstagefrighthw.so` `4fdc1a1f…`).

## Install and roll back

Quit the game on the Frame (Lepton must not be running), then:

```bash
python3 frameport-decoder/install_decoder.py work/decoder
python3 frameport-decoder/install_decoder.py --rollback     # back to the version before
```

The build is added next to FramePort's decoder in `~/.local/share/frameport/video-codec/versions/` and
`current` is switched to it; the previous version stays. FramePort keeps an installed decoder of the same
or a newer revision, so it won't replace this one with an older revision. After a FramePort update, check
whether FramePort ships revision 9 or newer itself; if so, roll back (or rebuild on top of it).

## License

The patch modifies FramePort's GPL-3.0-only source and is distributed under the **GPL-3.0-only** (the rest
of this repository is GPL-3.0-or-later). The build links FFmpeg (LGPL-2.1, as configured by FramePort's
builder). No binaries are included.
