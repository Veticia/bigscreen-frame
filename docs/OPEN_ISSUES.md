# Open issues

Problems seen on the headset with the current build (24subs, sha256 `3cf780ee…`). **Their causes are
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
4. If the decoder is confirmed as the cause, report it to FramePort with the timings.

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
