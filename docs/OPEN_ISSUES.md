# Open issues

Problems seen on the headset with the current build (26ssdp, sha256 `8294e36e…`; issue 1 first seen on 24subs). **Most causes are
not determined yet.** Each candidate (Bigscreen itself, these patches, Lepton/Android, FramePort) is
still open unless a section says otherwise. This page collects the evidence and the leads. Issues 5 and 13
were settled by experiment.

## 1. Video errors: TV channel switching, freeze

### What was seen

| Action | Result |
|---|---|
| First TV channel | Plays (including on the main screen and with subtitles) |
| Switch to another TV channel | *"There was an error loading the content"*; later attempts error too |
| Try each TV channel (2026-10-19) | Only two channels play; the others fail the same way. They may be limited to the US (see issue 2) |
| Switch between the two channels that play (2026-10-19, FramePort 1.3.0, 120 Hz) | Works |
| Play a channel, stop it, then play a DLNA file | Stops cleanly (no timeout); the DLNA file plays |
| Switch from a playing channel to one that fails (403) | Once: the old player's release timed out, the flush of its H.264 decoder failed (`internalError -110`); afterwards YouTube (VP9) and every later video failed until restart. Repeated in the next session with many switches including blocked channels: no lock-up, DLNA and TV played afterwards |
| Same session, a 384×216 stream | Iris failed on its first packets and FramePort's software fallback took over; leaving another 384×216 stream, `Player release timed out` again, but the next channel got a decoder at once (no flush failure) |
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

- Switch to a blocked channel (2026-10-19): FramePort's plugin logged 30 fps until 30 s before the switch,
  then nothing. At the switch, ExoPlayer's release of the old player timed out (500 ms); 3 s later the old
  decoder's flush failed (`signalError … internalError -110`, `Codec reported err 0xffffff92 … in state 7`),
  *before* the new channel's 403 arrived; ACodec force-released it 6 s after the stop. That decoder stayed
  registered: YouTube's web view then asked for `OMX.frameport.vp9.decoder` and Android tried to reclaim the
  stuck `OMX.frameport.avc.decoder` (`MediaCodec::reclaim(0x…)`), and YouTube didn't load; later TV attempts
  reclaimed the same instance and timed out again. Switching between two channels that play, and stopping a
  channel, did not fail in the same session type. The 10-bit decoder change only affects HEVC (this is H.264).
  Not reproducible on demand: slow releases (`Player release timed out`) happen fairly often, and only once
  did the decoder's flush also fail, which is what left it stuck.
- Second lock-up (2026-10-19, also on a build without `no-protected-overlay`): again the plugin's 5-second
  output lines stopped 15–20 s **before** the channel switch (first lock-up: 30 s before), then the flush
  failed 3 s after the stop. So the decoder's thread stalls *during* playback, and the switch's flush is
  never handled. Closing the hardware session is not the slow part: a test decoder that closed sessions on
  a background thread and logged closes of 100 ms or more logged none, and still locked up. Next: find the
  call the decoder thread blocks in (candidates: FFmpeg's V4L2 frame fetch, which waits with no time limit,
  and locking the output buffer, which waits on the consumer's fence), then bound that wait. In both sessions the slow/failed starts
  involved small or changing stream resolutions (384×216; 960×540 → 1920×1080), a lead only.

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
`entitlement` patches).

Lead (2026-10-19): only two TV channels play and every other one fails, which looks like a region limit
(US-only channels) rather than the port; it would also explain part of issue 1 (a "switch" to a blocked
channel fails whatever the decoder does). Next steps: try the failing channels from a US connection (VPN
on the network the Frame uses), and log the failing URLs to compare their request with a working one.

## 3. Browser clicks register as drags

With controllers, a click while the hand moves slightly becomes a click-and-drag; holding still works.
Possibly Bigscreen's drag threshold together with the Frame controllers' pose noise. Low priority.

## 4. Subtitles only on the main screen

TV subtitles appear on the main screen but not on the mirror screens. Probably the same on Quest (the
subtitle text belongs to `BigContentController`; mirrors copy only the video texture). Not checked on a Quest.

## 5. 10-bit video (HEVC Main 10): fixed in FramePort 1.4.0

With FramePort 1.2.1's decoder, a Main 10 file stays at 0:00 and then blocks every later decoder start
(issue 1, DLNA rows). A change to FramePort's decoder (below) makes it play; FramePort 1.4.0 ships it, so
nothing has to be installed by hand any more. Where the limit sits:

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
3. Offered to FramePort as [pull request #426](https://github.com/spoopyghosty0/frameport/pull/426) (same binary,
   with the profile check in `native/hevc/hevc_profile.h` and a host test); the fix is in FramePort 1.4.0.
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

## 8. Controller laser starts at the wrist with FramePort's Quest 3 identity (workaround: Quest 2)

FramePort 1.3.0 adds `device.headset_model` to every Quest game, default Quest 3: the OpenXR system name
and OVRPort's headset UUID say "Meta Quest 3". OVRPlugin logs `systemName Meta Quest 3`, but Bigscreen's
Oculus Utilities 1.78 have no name for that headset yet and log `SystemHeadset Placeholder_11` (as Quest 2:
`SystemHeadset Oculus_Quest_2`). With it, the controller laser starts at the wrist instead of the finger.
Workaround: Headset model **Quest 2** in FramePort (keeps OVRPort's original answers; everything else here
was tested that way). Confirmed on the headset with FramePort 1.3.0: `systemName Oculus Quest2`,
`SystemHeadset Oculus_Quest_2`, and the laser starts at the index finger again. There is no Quest 3 build to switch to: the Meta store serves the same build
(0.950.2.a276a2, build `38732876729693280`) to Quest 2, Quest Pro, Quest 3 and Quest 3S (checked per headset
type, 2026-10-19; only the original Quest gets an older build). Not known: whether a real Quest 3 shows the
same laser with this build (its OVRPlugin would report the same headset), so this may or may not be a
parity gap. Possibly worth telling
FramePort, since other games with older Oculus Utilities would meet the same unknown headset.

## 9. Foveated rendering: Android doesn't boot

With FramePort's foveated rendering on, Lepton enabled Valve's Vulkan foveation layer twice
(`'libVkLayer_VALVE_fdm_injection.so' enabled multiple times, skipping!`) and its Android stopped at
`Waiting for boot...`: black screen, no app log at all. Bigscreen renders with GLES, which runs on Vulkan
through Mesa's Zink, so the layer sits under it. One attempt, cause not investigated; leave foveation off.

## 10. DLNA file sometimes plays as `null` (intermittent)

In one session (2026-10-19, after a TV channel had played) every DLNA file failed:
`ArgumentNullException: Value cannot be null. Parameter name: uriString` in `App.PlayVideoFileUrl` ←
`VideoFileNavPage.OnFileSelected`, then a `NullReferenceException` in `BigContentController.PlayLocalVideo`.
The next session with the same steps worked, so it is intermittent.

What the code does: `VideoFileNavPage.LoadLocalFile` plays `File.Url` for media-server files;
`File.CreateMediaServerFile` copies it from `Element.FileUrl`, and the item `Element` constructor sets
`FileUrl` only from a `<res>` that carries a `duration` attribute (a `<res>` without one is taken as the icon).
Bigscreen browses with `Filter=*`, and minidlna returned a duration for all 2537 entries, so the missing URL
came from Bigscreen's side. A second media server had appeared on the network by then (Windows Media Player
sharing on another PC); both servers have a top-level folder named "Video", and `DLNABrowser` looks
containers up by path (`TryFindContainer`). Unproven lead. Nothing here is Frame-specific, so a Quest would
likely behave the same. Next time: note the folder path and whether the other server is listed; minidlna's
request log (`log_level=http=debug`) shows which server Bigscreen asked.

## 11. Seek bar does nothing for the first local file after a TV channel

Play a TV channel, then open a DLNA file: seeking does nothing (reproduced twice, 2026-10-19). Stopping and
reopening the file makes seeking work; YouTube followed by a file is fine. The player never receives the
seeks (no `Seek processed`), while decoding and playback are identical to the working reopen, so the
controls keep state from the live TV stream. The TV channels tested don't use Bigscreen's protected media
session (see PATCHES.md, `no-protected-overlay`), so that patch is not involved; most likely the same on a
Quest. Workaround: stop and reopen the file.

## 12. Shop purchases fail with "Unable to start a purchase of this item"

Observed on the headset 2026-10-31. The shop opens and lists items, but buying the *Sports Bar* environment
($9.99) shows an in-app dialog:

> Unable to start a purchase of this item.
> Try closing Bigscreen and restart through your platform's app library.

The purchase fails inside Bigscreen: no other activity starts. `logcat` at the time has no
`ActivityTaskManager` START, and the focused activity stays
`com.bigscreenvr.bigscreen/com.unity3d.player.UnityPlayerActivity`. Screenshot and logcat are in
`local/evidence/` (`20261031-shop-purchase-error.jpg`, `logcat-shop-20261031.txt`; both ignored by git).

At startup the same session logs:

```
E/Unity: Exception: Oculus platform not initialised.
  at Bigscreen.Cloud.Utils+<GetOculusUserData>d__3.MoveNext ()
  at Bigscreen.Cloud.Accounts+<FetchStoreProfileData>d__47.MoveNext ()
  at Bigscreen.Cloud.Accounts.FetchStoreProfileData ()
```

and the app reports `PRODUCT NAME: Oculus Quest`. The build expects Oculus Platform services (accounts and
IAP) that the port does not provide; that is the leading explanation for the failed purchase, not yet proven
to be the dialog's direct cause. The dialog text exists in `global-metadata.dat` (sha256 `1d87710b…`);
REA also found `BigscreenShop`, `FindBigscreenStoreCheckout`, `https://bigscreenvr.com/purchase/`, the
strings *"Check your inbox or click below to complete the purchase in your browser"* and *"An email with a
link to the purchase page has been sent"*, and Oculus purchase code (`ovr_IAP_ConsumePurchase`,
`Purchase_Error_CreateOculusOrder`). So the APK also contains a web checkout, but the runtime failure happens
before either flow completes.

Next:

1. Watch `adb logcat` live while pressing buy, and record the exception immediately before the dialog.
2. Find the dialog string's XREF in `libil2cpp.so` to identify the failing branch (Oculus IAP init,
   entitlement, or web checkout).
3. Test the dialog's advice: close Bigscreen completely, relaunch it from the Steam library, and retry.


## 13. Microphone cuts in and out (cause found; fix offered to FramePort)

Others in the room heard the Frame's microphone cut in and out, in nearly every second. **Cause found and
fixed on the headset** (2026-10-31, FramePort 1.4.0); the fix is offered to FramePort as
[pull request #549](https://github.com/spoopyghosty0/frameport/pull/549), patch `frame.unity_mic_ring`. It is
a Frame-only difference: Bigscreen's own code works as intended, and on a Quest the cause can't occur (below).

### Where the audio was lost

Each layer was measured during humming and silence in a room with another participant (Bigscreen sends Opus
even in silence: there is no voice detection):

| Layer | Result |
|---|---|
| Frame's mic and PipeWire (raw ALSA mic, echo-cancel chain, Lepton's loopback source) | continuous; the processed signal is only the default source volume (0.82) lower |
| Lepton's audio HAL / AudioFlinger (`dumpsys media.audio_flinger`, record track) | continuous, 100 % of real time delivered to Bigscreen's `AudioRecord` |
| OpenSL ES → FMOD (Unity's audio engine) | every 512-frame buffer arrives and is re-queued |
| FMOD → Unity's `Microphone` clip (`Microphone.GetPosition`) | **13 % of real time**: stands still 200–550 ms, then jumps 512–1536 frames |
| Bigscreen `MicInput.Tick` → `BigSoupBroadcastMicAudio` | sends everything the clip offers |
| Network (mic packets to the Mediasoup server) | 25–35 per second instead of 200, with 0.2–0.56 s gaps |

The refresh rate made no difference (72 Hz and 120 Hz the same), and neither did a +10 dB mic gain.

### Cause

1. Lepton's audio HAL (Valve's `frame-public/android_hardware_waydroid`, `adev_open_input_stream`) opens the
   microphone as 48 kHz **stereo 32-bit**. Unity's FMOD asks OpenSL ES for **mono 16-bit** with the fast
   capture path. Android 11 grants that path only when format and channel mask match the HAL's
   (`RecordThread::createRecordTrack_l`), so it is refused:
   `AudioFlinger: createRecordTrack_l(): mismatch between requested flags (00000005) and input flags (00000001)`.
2. The normal path (20 ms notifications, a 2880-frame track) fills FMOD's buffers in bursts: about 70 % of
   wake-ups fill four 512-frame buffers back to back, every ~40 ms.
3. FMOD's recording ring is four DSP buffers (`System::getDSPBufferSize`: 4 × 512 frames, 43 ms), and it
   knows new audio only by the ring index of the next buffer. A 4-buffer burst brings the index back where
   it was: FMOD sees no new audio and the burst is overwritten. Only bursts of 1–3 buffers get through.

On a Quest the fast path delivers about one buffer at a time, so the ring is never lapped.

### How it was found

All observation was passive except two temporary tests (a gain change, reverted; the in-memory fix below).

- [`dev/mic_probe.py`](../dev/mic_probe.py): mic taps on the Frame, `pw-top`, logcat, `dumpsys`, and a
  `tcpdump` of the mic transport, on one clock.
- Bigscreen's code: `libBigSoup.so` (with symbols) and `MicInput` from `libil2cpp.so` (method addresses from
  `global-metadata.dat`), decompiled with REA/Ghidra and capstone. FMOD's record path in `libunity.so`.
- Live tracing: Lepton apps run as the Frame's `steamos` user, so the host's `gdb` attaches to Bigscreen
  without Android root. Breakpoints that only log and continue were set on `MicInput.Tick` (position),
  `BigMicStream::Broadcast`, libwilhelm's `audioRecorder_callback` / `IBufferQueue_Enqueue`, and FMOD's
  record update and record position (`libunity.so` `0x1240178`, `0x124c724`, callback `0x124d32c`).

### Fix

The buffer count FMOD's recorder start reads from `getDSPBufferSize` becomes 16 (8192 frames, longer than
the whole 2880-frame AudioFlinger track): four instructions in `libunity.so`. Sound output keeps Unity's own
buffers.

- In memory (gdb at the recorder start): 201 mic packets/s of 200, no gap over 68 ms.
- The patched `libunity.so` in a re-signed copy of the deployed APK: 200 packets/s, no gap over 68 ms, and
  the voice no longer cuts out.

Until FramePort ships the patch, a rebuild from FramePort brings the dropouts back. The deeper fix would be
in Lepton: a mono 16-bit capture profile that Android can give the fast path.
