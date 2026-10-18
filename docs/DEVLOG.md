# Development log

How the port got from "stuck at the login screen" to a working Bigscreen on the Steam Frame
(October 2026). Each build was tested on the headset; the scripts for every step are in
[`archive/bigscreen-steps/`](../archive/bigscreen-steps/), and the final patch set is described in
[PATCHES.md](PATCHES.md).

## Phase 1: getting in

| Build | Change | Result |
|---|---|---|
| *(pristine)* | FramePort install, no patches | Login stuck at 55% (Meta entitlement request never answered) |
| fixed / 3fix | entitlement → success, PC login flow, clipboard no-ops; presentation materials → Desktop shader; OESBlit precision | **Logs in, rooms work.** Magenta screens gone; browser gray, remote screens black |
| 4tex / 7tex | `Texture.set_wrapMode/filterMode` no-ops | No visible change, kept (removes GL errors on external textures) |
| 5movie, 6movie | movie-screen material clones across asset files | **App does not start**: cross-file material references dangled |

## Phase 2: why is everything gray? (the overlay route)

| Build | Change | Result |
|---|---|---|
| 8ovr | `PresentationOverlayController.ToggleOverlay` → `ret` | Ambient light follows content (last good state of this phase) |
| 9ovr | also `UpdateRegularOverlay` → `ret` | Ambient light died |
| 10rt–14rt | "Route A": blit the OES texture into a RenderTexture and hand that to the overlay | 10rt crashed; 11rt–14rt ran but stayed gray |

**The real cause** (found while preparing Route A's next step): the "precision fix" from 3fix had
grown the OESBlit GLSL by 24 bytes per subprogram *without* updating the shader blob's internal
subprogram table. From 3fix on, Unity dropped the program at every launch
(`Failed to load GpuProgram from binary shader data`). "Magenta gone" had meant "nothing drawn".
The ambient light kept working because it samples the same video with a different shader
(FastBlurOES). Route A could never work either: it fed a 2D RenderTexture into an OES-only shader.

| Build | Change | Result |
|---|---|---|
| 15oes | 8ovr + length-preserving OESBlit fix | **Browser/YouTube visible**, upside down |
| 16flip | + vertical flip in OESBlit | Browser correct, but it **draws over** avatars and props in front of it |

The Frame's compositor puts quad layers above the scene, so overlays can never be occluded. Every
screen had to move into the 3D scene.

## Phase 3: screens in the scene

| Build | Change | Result |
|---|---|---|
| 17mesh | BigMediaRenderer → its unused `UpdateRegularScreen` mesh path; MovieScreen → OESBlit | Occlusion **not** fixed (the browser window is a different component, `BigUIBrowser`); TV channel gray (OESBlit is a blit shader, not usable on the scene mesh) |
| 18oes | BigUIBrowser → `TextureTarget.Regular`; shader #319 ← Bigscreen's own OES screen shader #825; ST flip | **Occlusion works.** Picture upside down; main screen hidden behind a gray rectangle |
| 19screen | drop the flip; disable the Underlay Impostors | Browser, floating window, main screen all correct. TV main screen black/see-through |
| 20present | `ToggleOverlay(false)` instead of `ret` (presentation sections in-scene) | **Peer/room screen sharing visible**; SBS 3D shown as two pictures |
| 21stereo | per-eye split in the Desktop shader by `_3dMode` | **3D correct** |
| 22tv | `isProtectedExternalEnabled = false` | TV still not on the main screen (only the mirrors) |
| 23show | stub also re-activates `regularScreen` | **TV on the main screen**; subtitles flash for one frame |
| 24subs | subtitle fallback duration 4 s | **Subtitles readable.** |
| 25ou | shader #319: OU / UO branches swapped | **Over-under local videos (DLNA) show the right eye in each eye.** Current release |

Screenshots of the TV channel in two environments decided 22tv → 23show: the mirror screens (a PC
monitor, seat-back screens) showed the channel while the main screen was missing, so the texture was
fine and only the screen object was switched off.

## Lessons

- **Unity shader blobs are containers.** Any GLSL edit must keep each subprogram's byte length, or
  the table, GLSL length prefixes and alignment go stale. Check for `Failed to load GpuProgram` after
  every shader change (`dev/unity_shaders.py check`).
- **"No longer magenta" is not "fixed".** Unity's error shader turns into *nothing drawn* when a
  program fails to load.
- **Look for the PC path.** Bigscreen keeps its non-Oculus code paths (in-scene screens, account
  login), so most fixes are one-instruction switches to them, not new code.
- **Byte-compare builds.** `dev/apk_diff.py` at object level caught a material copy that differed from
  what an older tool's comments claimed, and proved the final one-command build identical to the
  tested one.
- **Read the log end to end before patching.** The FramePort session log showed which overlays got
  swapchains (`FrameBridge: new layer ... usable=1`), which texture path ran (`Updating the regular
  texture`) and where the app froze (`MediaCodec::reclaim` on the render thread).
