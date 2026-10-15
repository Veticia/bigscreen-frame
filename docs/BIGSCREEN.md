# Bigscreen Beta: app profile

Meta store id `2497738113633933`, package **`com.bigscreenvr.bigscreen`**. Free on the Meta store
(free to play, offers in-app purchases).

| | |
|---|---|
| Version | 0.950.2.a276a2-beta-perma-rooms-beta, versionCode 4688857 (build 38732876729693280) |
| Size | 1,175,485,713 bytes, single APK (no OBB, no split APKs) |
| SHA-256 | `6cae96955397be3522c238196635a061c5f9da465f032cbe34b817dddbb4a18a` |
| ABI | arm64-v8a only |
| Engine | Unity 2020.3.33f1, IL2CPP |
| XR | OVRPlugin 1.89.1 / Oculus Utilities 1.78 on OpenXR (also ships `libvrapi.so`, `libUnityOpenXR.so`) |
| Graphics | OpenGL ES 3 (no Vulkan) |
| Media | ExoPlayer 2.18.1 (`libbig-media-player.so`, `libffmpegJNI.so`), Vuplex 3D WebView 4.8 on Android System WebView |
| Network | `libBigSoup.so` (Mediasoup-based rooms: voice, peer desktop video/audio) |

## Feature status on the Steam Frame

See the table in the [README](../README.md#what-works). Before the port, the guess was that rooms,
TV channels and YouTube would be dead because of Meta services. They are not: Bigscreen keeps its
PC code paths (own account login, in-scene screens), and the patches switch the Quest build to them.

## Where things live (for porting to a newer build)

| Feature | Classes |
|---|---|
| Login | `BIG_APP_LOADER`, `Oculus.Platform.Samples.EntitlementCheck` |
| Browser window | `Bigscreen.UI.BigUIBrowser`, `Bigscreen.Browser.BackgroundBrowser` |
| Media / main screen | `Bigscreen.Media.BigContentController`, `BigMediaRenderer`, `BigMediaPlayer` |
| Mirror screens | `MirroredScreen` |
| Room / presentation screens | `Bigscreen.Screens`, `PresentationViewController`, `PresentationSection`, `PresentationOverlayController` |
| Ambient room light | `Bigscreen.Lighting.BigLightingController` |
| Subtitles | `BigContentController.OnSubtitleCue`, `SubtitleTimer` |
