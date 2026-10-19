# FramePort notes for Bigscreen

## Setup

Once per headset (from FramePort's own instructions):

1. Download FramePort for Linux/macOS/Windows from its
   [releases](https://github.com/spoopyghosty0/frameport/releases/latest) and start it.
2. In FramePort: **Steam Frame → Start setup**. Frame and PC on the same network, or USB.
3. On the Frame: **SteamVR dashboard → Launch a program → Desktop**, then the app menu →
   **System → Konsole**, and run `curl -fsSL https://frameport.app/s | bash`.
4. Click **Allow** in FramePort when it shows the same 4-digit code as the Frame. Steam restarts
   once; confirm when it offers to install **Lepton** (Valve's Android runtime).

## Per-game settings used in testing

Tested with FramePort 1.2.1 and its suggested recipe for Bigscreen, unchanged (FramePort's defaults).

"Show 360° backgrounds and videos" (`equirect_emul`) is not needed and was off in the tested install:
360° room backgrounds show without it. Bigscreen creates one small (128×128) equirect layer that the
Frame's runtime drops, so FramePort's log check reports `equirect-layers-dropped`; nothing visible is
missing.

## How Bigscreen meets the Frame's compositor

- Bigscreen (Unity 2020.3, OVRPlugin 1.89 on OpenXR) shows its screens as **OVROverlay quad
  layers** on Quest. The Frame's runtime only composes quad and projection layers, and draws
  **quads above every projection layer**, whatever the submission order (see FramePort's
  `docs/FRAME_RUNTIME.md`). Overlay screens therefore cover avatars and props, and Quest-style
  underlays (a punched hole in the eye buffer) show nothing. The patches move every screen into
  the 3D scene instead ([PATCHES.md](PATCHES.md)).
- Eye buffers are created as two `array=1` swapchains (multi-pass); swapchains are `GL_SRGB8_ALPHA8`
  only (FrameBridge retries `GL_RGBA8` requests with it).

## Video decoding

Video is decoded by FramePort's OMX components (`OMX.frameport.avc/vp9/hevc.decoder`, source in
FramePort's `native/hevc`), for Bigscreen's ExoPlayer as well as the web view. Unresolved video
errors and what the logs show about them: [OPEN_ISSUES.md](OPEN_ISSUES.md).
