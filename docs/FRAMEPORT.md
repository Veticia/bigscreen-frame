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

Tested with FramePort 1.2.1 and 1.3.0 and their suggested recipe for Bigscreen, with these exceptions:

| Setting | Result |
|---|---|
| **Headset model** (`device.headset_model`, new in 1.3.0, default Quest 3) | Set it to **Quest 2**. With Quest 3, OVRPlugin reports `systemName Meta Quest 3`, but Bigscreen's Oculus Utilities 1.78 predate the Quest 3 and log `SystemHeadset Placeholder_11`; the controller laser then starts at the wrist instead of the finger. With Quest 2 (`SystemHeadset Oculus_Quest_2`) it starts at the index finger again (checked with 1.3.0); everything else in this repo was tested that way. Whether a real Quest 3 shows the same with this Bigscreen build is not known. |
| **Display refresh rate** (`adapter.refresh_rate`) | 120 Hz works: Bigscreen reports `Current display frequency 120`, frame pacing settles at about 120 fps. |
| **Foveated rendering** | Leave it off. With it on, Valve's Vulkan foveation layer (`VALVE_fdm_injection`) was enabled twice and Lepton's Android never finished booting (`Waiting for boot...`, black screen, no app log). Bigscreen is GLES, which runs on Vulkan through Mesa's Zink here. One attempt; not investigated further. |
| **Show 360° backgrounds and videos** (`equirect_emul`) | Not needed: 360° room backgrounds show without it. Bigscreen creates one small (128×128) equirect layer that the Frame's runtime drops, so FramePort's log check reports `equirect-layers-dropped`; nothing visible is missing. |

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
