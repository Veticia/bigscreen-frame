# Open issues

Problems seen on the headset with the current build (26ssdp, sha256 `8294e36e…`; issue 1 first seen on 24subs). **Their causes are
not determined yet.** Each candidate (Bigscreen itself, these patches, Lepton/Android, FramePort) is
still open. This page collects the evidence and the leads; nothing here has been confirmed by an
experiment.

## 1. Video errors: TV channel switching, freeze

### What was seen

| Action | Result |
|---|---|
| First TV channel | Plays (including on the main screen and with subtitles) |
| Switch to another TV channel | *"There was an error loading the content"*; later attempts error too |
| Open a YouTube tab while a TV channel plays | Works: YouTube replaces the channel on the floating window and the screens |
| After channel-switch errors, join an almost full public room | App froze |
| DLNA local files: an H.264 file, then a 10-bit HEVC file, then others | H.264 plays; HEVC stays at 0:00; every following file fails to load |
| DLNA local files, 8-bit only, switched three times | All play |

### Log evidence (FramePort session logs)

- Decoders: every video (ExoPlayer and the web view's Chromium) uses
  `OMX.frameport.avc.decoder` / `OMX.frameport.vp9.decoder`. FramePort's
  `media_codecs_frameport.xml` declares `concurrent-instances max=1` for them.
- When YouTube replaced a playing TV channel, the TV player's teardown logged
  `MediaCodec::reclaim(0x…) OMX.frameport.avc.decoder` and an ExoPlayer `CodecException`; YouTube itself
  played normally, so this error was not visible as a failure.
- Channel switch: Bigscreen's `BigMediaPlayer.StartStream` → `ReleaseResources` → ExoPlayer
  `Player release timed out` (ExoTimeoutException; ExoPlayer's default release timeout is 500 ms).
  The new player then fails in `MediaCodec.flush()` (via `onPositionReset`):
  `ACodec: signalError(omxError 0x80001001, internalError -110)`, `CodecException 0xffffff92`.
  ACodec logs `forcing the release of codec` about 5 s after the release started.
- Earlier, in the web view: `cr_MediaCodecBridge: Failed to flush MediaCodec ... -110` after it had
  opened two AVC decoders in a row.
- Freeze: the last line from Unity's render thread was
  `MediaCodec::reclaim(0x…) OMX.frameport.avc.decoder` while joining the room (a peer desktop stream
  needs a decoder); afterwards BigSoup logs `Mic audio overflowed buffer; client likely stopped
  requesting samples` until the app was closed, so Unity had stopped while networking continued.
- DLNA local files (no TV path, so `no-protected-overlay` is not involved): the HEVC file was Main 10
  (`hvc1.2.4.H120`); `MediaCodecInfo` reported `NoSupport [codec.profileLevel]` for both
  `OMX.frameport.hevc.decoder` and `OMX.google.hevc.decoder`, ExoPlayer used the FramePort one anyway and
  no frame came out. Leaving it: `Player release timed out`, then the flush failed
  (`signalError … internalError -110`, `CodecException 0xffffff92`). The next file:
  `MediaCodec::reclaim(0x…) OMX.frameport.hevc.decoder`, `forcing the release of codec`, load failed;
  every later file failed too, until Bigscreen was restarted.

### Candidate causes (unverified)

- **Decoder side** (FramePort `native/hevc`): one-instance limit; a slow release; a flush that does not
  complete in time. `onPortFlushCompleted` in `frameport_hevc.cpp` tears down a running hardware (Iris)
  session instead of flushing it in place, which could be slow.
- **App side** (Bigscreen / ExoPlayer): Bigscreen releases the old player on the Unity thread and
  immediately starts a new one; with a 500 ms release timeout, the old codec may still exist. On
  Quest, several hardware decoders are available, so this pattern may simply never fail there.
- **Patch side**: `no-protected-overlay` sends TV channels down Bigscreen's texture playback path
  instead of the Android-surface overlay. That changes how the decoder's output surface is used, and
  might change release or flush behaviour. Not yet compared with an unpatched path.
- **Platform**: Android's MediaCodec resource manager behaviour on Lepton when reclaiming an instance.

### Next steps

1. Reproduce with a minimal app or `adb shell` test outside Bigscreen: two `MediaCodec` instances of
   `OMX.frameport.avc.decoder`, and flush + release timings, to separate decoder from app behaviour.
2. Compare channel switching with and without `no-protected-overlay` (build without that patch; TV then
   plays only on the mirrors, but switching can still be observed).
3. Check whether ExoPlayer's release timeout or Bigscreen's release/start order can be changed (Java side,
   `com.bigscreenvr.bigmediaplayer.BigMediaPlayer`).
4. Reproduce the DLNA case on purpose: play a 10-bit HEVC file, then switch to an H.264 file. If it
   repeats, one unplayable file is enough to block every later decoder start.
5. If the decoder is confirmed as the cause, report it to FramePort with the timings.

## 2. Some streams answer HTTP 403

ExoPlayer: `HttpDataSource$InvalidResponseCodeException: Response code: 403` for some TV channels / videos,
others play. Not determined whether the server rejects the content (region, licensing), the request
(headers, tokens), or the account session as established by the PC login flow (`pc-login`,
`entitlement` patches). Next step: log the failing URLs and compare their request with a working one.

## 3. Browser clicks register as drags

With controllers, a click while the hand moves slightly becomes a click-and-drag; holding still works.
Possibly Bigscreen's drag threshold together with the Frame controllers' pose noise. Low priority.

## 4. Subtitles only on the main screen

TV subtitles appear on the main screen but not on the mirror screens. Probably the same on Quest (the
subtitle text belongs to `BigContentController`; mirrors copy only the video texture). Not checked on a Quest.

## 5. 10-bit video (HEVC Main 10): fixed in a FramePort decoder change, not yet upstream

With FramePort 1.2.1's decoder, a Main 10 file stays at 0:00 and then blocks every later decoder start
(issue 1, DLNA rows). A change to FramePort's decoder (below) makes it play; until FramePort ships it, it
has to be built and installed by hand. Where the limit sits:

- **Bigscreen**: ships no decoders and uses what Android offers (Bigscreen staff on Steam, 2023: *"we rely
  on the codecs that are natively supported by the OS"*). On a Quest, Qualcomm's own Android codecs decode
  Main 10 in hardware, so it most likely plays there. No report either way was found.
- **Chip**: the Frame's SoC is SM8650 (Snapdragon 8 Gen 3; `gcc-sm8650` in the kernel log). It decodes Main 10
  in hardware on Android phones.
- **Host kernel driver**: SteamOS uses the upstream V4L2 driver `qcom-iris` (`/dev/video-dec0`). 10-bit
  decoding (P010, QC10C) for it was posted by Neil Armstrong (Linaro) in April–June 2026, tested on SM8650,
  and merged for **Linux 7.2** ([v3 cover letter](https://lwn.net/Articles/1072174/)); a QC10C fix followed
  in July 2026. The Frame runs **6.18** (`iris_driver` 6.18.0). Without a coded format set its decoder lists
  only 8-bit capture formats, but **with HEVC selected it offers `Q10C`** (Qualcomm compressed 10-bit) and its
  `hevc_profile` menu includes **Main 10** (checked 2026-10-19 with `v4l2-ctl`). There is no plain P010, so
  hardware Main 10 output exists only in Qualcomm's compressed (UBWC) layout. FramePort's component always
  asks for NV12 capture; a Main 10 stream in an NV12 session returns no pictures, which is the hang.
- **Android in Lepton**: a container on the same kernel, not an emulator. Qualcomm's Android codec stack
  (downstream `msm_vidc` + vendor Codec2/OMX libraries) is absent, so Android only has Google's software
  codecs (`OMX.google.hevc.decoder` reports `NoSupport` for Main 10 on this Android version) plus FramePort's
  `OMX.frameport.*` components. Those forward to the host's iris driver and handle 8-bit 4:2:0 (NV12) only,
  by design (FramePort `native/hevc/README`). The container cannot get more than the host driver gives.

So the hardware can decode Main 10, but only into a compressed layout FramePort's component cannot read
yet; the hang comes from FramePort's component asking for NV12. None of it is in Bigscreen.

### Next steps

1. Done: the driver offers Main 10 and `Q10C` (see above).
2. Done, works on the headset: FramePort's component built from v1.2.1 with a change
   ([`frameport-decoder/`](../frameport-decoder/): patch, build and install scripts; decoder revision 9,
   installed next to FramePort's revision 8, which stays for rollback). HEVC streams whose SPS profile is beyond Main go
   straight to FFmpeg's software decoder (inside the same component), its 10-bit pictures are rounded to
   8-bit planar, and the component lists Main 10 (level 5.1). HDR10 is not tone-mapped. The same build
   without the change reproduces FramePort's binary byte for byte (NDK r27c, Lepton rootfs libraries).
   Tested in Bigscreen: 1080p and 4K Main 10 test clips and a real 1080p Main 10 episode play; 8-bit HEVC
   and H.264 afterwards play too. FFmpeg's software HEVC decoder on the Frame's CPU (4 threads, as the
   component uses): real 1080p Main 10 232 fps, synthetic 4K Main 10 72 fps.
3. Offered to FramePort: [pull request #426](https://github.com/spoopyghosty0/frameport/pull/426) (same binary,
   with the profile check in `native/hevc/hevc_profile.h` and a host test).
4. 10-bit in hardware later: a `Q10C` capture path in FramePort's component, which needs the compressed
   layout decoded (GPU) before the 8-bit output; or P010 if a later kernel offers it.

## 6. DLNA discovery was unreliable on Lepton (fixed in 26ssdp)

Lepton puts Android behind `pasta` (userspace network proxy). Tested on the Frame with a private pasta
instance using Lepton's options: a single SSDP M-SEARCH from a fresh UDP socket got no unicast replies,
while repeating it about 1.5 s later on the same socket got replies every time (12/12). Bigscreen's
`DLNAService.FindDevices` sends one M-SEARCH per search, so a search sometimes finds nothing. Build 26ssdp
resends it once after 1.5 s (`ssdp-cave` in `bspatch/code_patches.py`, see [PATCHES.md](PATCHES.md#dlna)).
On the headset the server was found every time. Opening the server list takes 7–8 s; most of that is
Bigscreen's own wait (in an unpatched run that did find the server, it fetched the description in the
same second as the search and started browsing 11 s later), so a Quest should be similar.

Also still to test on the headset: peer screen share with SBS/OU 3D (needs a second person).

## 7. DLNA subtitles and audio: Bigscreen's own limits (same as Quest)

The goal is Quest parity, so these are recorded, not patched:

- **ASS/SSA subtitle tracks stay blank**; SRT tracks show, also as a second track after switching (test
  MKVs: SRT+ASS, ASS only, ASS+SRT). Subtitle decoding is ExoPlayer's (Java) and track handling is
  Bigscreen's; `BigMediaPlayer$TextOutputListener.onCues` forwards every cue that has text. Nothing in that
  path is Frame-specific, so a Quest should behave the same. Not checked on a Quest.
- **Sidecar `.srt` files** are not loaded: Bigscreen's Java player never adds external subtitle sources
  (no `SubtitleConfiguration` / merged sources in `com.bigscreenvr`). The SRT loader of the bundled AVPro
  player is only reachable from AVPro's own `MediaPlayer`, which local and DLNA playback do not use.
  Workaround: mux the `.srt` into the file (`ffmpeg -i in.mkv -i in.srt -map 0 -map 1 -c copy out.mkv`).
- **E-AC3 audio** is dropped (`FfmpegLibrary: No eac3 decoder available`). Test clips with a beep: AC3 5.1
  plays, E-AC3 5.1 (MKV) and E-AC3 stereo (MP4) are silent. AC3 is decoded by Bigscreen's own FFmpeg, which
  has no E-AC3; Lepton's Android has no AC3/E-AC3 decoder at all (only Google's MP3, AAC, Vorbis, Opus, FLAC,
  AMR, G.711). Same on Quest: Quest 2 users report E-AC3 films silent in Bigscreen
  ([r/bigscreen](https://www.reddit.com/r/bigscreen/comments/r3s7ha/does_anyone_know_why_eac3_audio_formats_dont_play/),
  [Emby forum](https://emby.media/community/topic/91248-dlna-in-bigscreen-on-oculus/page/4/)), and Quest 3's
  MediaCodec has no Dolby decoder
  ([Meta forum](https://communityforums.atmeta.com/discussions/dev-unity/no-dolby-atmos-decoder-in-mediacodec-on-meta-quest-3-v78/1331125)).
  Workaround: re-encode the audio (e.g. `-c:v copy -c:a aac`, or `ac3` to keep 5.1).
