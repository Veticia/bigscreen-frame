# The patches

Target: `com.bigscreenvr.bigscreen` 0.950.2.a276a2-beta-perma-rooms-beta (versionCode 4688857),
Unity 2020.3.33f1, IL2CPP, OVRPlugin 1.89.1 on OpenXR. Addresses are offsets in
`lib/arm64-v8a/libil2cpp.so` (equal to virtual addresses in this build). Method names come from
Il2CppDumper. Implementation: [`bigscreen/bspatch/`](../bigscreen/bspatch/).

Steam Frame specifics that drive almost everything below:

- **Lepton** (Valve's Android runtime) has no Meta platform services and no clipboard service.
- GLES is **Mesa on Zink** (Turnip, Adreno 750), stricter than Qualcomm's driver about GLSL and
  texture parameters.
- The OpenXR compositor draws **quad layers above the 3D scene** and has no underlays, so every
  OVROverlay-based screen covers whatever is in front of it.

## Login

### `entitlement` — `Oculus.Platform.Samples.EntitlementCheck$$Start` @0x1080594
Bigscreen asks Meta's platform for an entitlement and waits for an answer that never comes on
the Frame (login stalls at 55%). The patch replaces the request with
`HandleEntitlementCheckResult(this, true)` (`mov x0, x19; mov w1, #1; b 0x108068c`).

### `pc-login` — `BIG_APP_LOADER.get_RequiresOculusPlatform` @0xb895cc
Returns `false`, so the loader takes the PC branch: no Oculus platform init, Bigscreen's own
account login. Details: [analysis/login-and-entitlement.md](analysis/login-and-entitlement.md).

### `clipboard-*` — `HomePage/JoinRoom/SocialPage$$GetRoomCodeFromClipboard`
Opening the room pages reads the clipboard for an invite code. Lepton has no clipboard service
(`No service published for: clipboard`), which ends in a JNI abort. The three methods become
`ret` (room codes can still be typed).

## Video on Mesa

### `texture-wrapmode`, `texture-filtermode` — `Texture.set_wrapMode` @0x1e25efc, `set_filterMode` @0x1e25f8c
Bigscreen sets wrap/filter modes on `Texture2D.CreateExternalTexture` wrappers of OES video
textures. On Mesa that raises `GL_INVALID_VALUE`. Both setters become no-ops; normal textures keep
Unity's default clamp/bilinear state.

### OESBlit shader — `globalgamemanagers.assets` Shader #6 `BigscreenVR/Unlit/OESBlit`
OVROverlay's OES copy material. Its GLSL declares `uniform int _linearToSrgb/_premultiply` with
no precision in both stages. GLSL ES gives `int` different default precisions per stage, so Mesa
refuses to link (`mismatching precision qualifiers`). The patch makes them `highp int` and flips the
copy vertically (the overlay path showed it upside down).

The edit is **length-preserving**: the bytes come from a duplicated `#extension` line. Unity's
shader blob is a table of subprograms with offsets and GLSL length prefixes. An earlier attempt
that grew the text corrupted that table, and Unity then logged
`Failed to load GpuProgram from binary shader data` and dropped the program
([DEVLOG.md](DEVLOG.md)). With all screens now in-scene this shader is mostly unused; it stays fixed
because any remaining overlay copy depends on it.

## Screens in the 3D scene

Bigscreen has three screen systems. On Quest each one shows its picture through an OVROverlay
quad, with a mesh in the scene that only punches an underlay hole. Each system also contains a
non-overlay path (used on PC), and the patches switch to it.

### Browser window — `browser-in-scene`, `BigUIBrowser$$UpdateActiveStates` @0x10372d8
`UpdateActiveStates` picks `TextureTarget.Overlay` when the VR SDK is Oculus and *desktop UI only*
is off, otherwise `TextureTarget.Regular` (overlay object off, `regularScreen` mesh on). The first
instruction becomes `b 0x103736c`, always the Regular branch. `UpdateTextures` then feeds
`regularScreenRenderer.material.mainTexture`.

### Media player / main room screen — `media-in-scene`, `BigMediaRenderer$$UpdateRegularOverlay` @0x149fd1c
`BigMediaRenderer.Update` always ends by tail-calling `UpdateRegularOverlay`. Its unused twin
`UpdateRegularScreen` (0x14a0584) feeds the in-scene `RegularScreen` mesh. `UpdateRegularOverlay`
is replaced by a 27-instruction stub:

1. `regularOverlay.enabled = false` (`Behaviour$$set_enabled`): no quad is submitted;
2. `regularScreen.SetActive(true)` (`GameObject$$SetActive`): `BigContentController.Reset/HideScreen`
   deactivate the mesh and `ShowScreen` only re-activates the overlay (it never needed the mesh on
   Quest). The stub only runs while `regularOverlay`'s GameObject is active, so Bigscreen's own
   hiding still works;
3. tail-call `UpdateRegularScreen` with the original arguments.

### TV channels — `no-protected-overlay`, `BigContentController$$.ctor` @0xb98fd0
`isProtectedExternalEnabled` (+0xD0, initialised to `true`) makes protected media sessions (TV
channels) render into `protectedExternalOverlay`, an OVROverlay Android-surface layer, and makes
`ShowScreen` activate that overlay instead of `regularOverlay`. Initialising it to `false` selects
Bigscreen's texture playback path (`BigMediaPlayer.StartProtectedMediaSession`) and the regular
screen. The picture was never really protected: mirrors and ambient light sampled it all along.

### In-scene shader for the media and browser screens — Shader `sharedassets0` #319
`MovieScreen` (#18), the material of `RegularScreen` and of the five browser prefabs' screens, uses
`Unlit/BSVR_Texture3D`, a `sampler2D` shader that cannot sample OES video textures (blank or gray).
Bigscreen ships the right variant, `Unlit/BSVR_Texture3D_OES` (`resources.assets` #825, also handles
stereo layouts). `sharedassets0` cannot reference `resources.assets` (no external entry), so the
patch copies #825's complete serialized shader into #319, keeping #319's name. #319 has no other
users. Orientation is correct as is: the `RegularScreen` transform is scaled (-1, -1, -1).

### Underlay impostors — MeshRenderers `sharedassets0` #3538, #3566
Children of the two media overlays (`Oculus/Underlay Impostor`, 8 cm in front of the screen) that
punch the underlay hole. With the overlay off they drew a gray rectangle over the main screen.
Disabled.

### Room / presentation screens — `presentation-in-scene`, `PresentationOverlayController$$ToggleOverlay` @0xbfd1f0
`ToggleOverlay(enableOverlay)` with `false` is the PC state: OVROverlays off, section renderers on,
`UnderlayCutout` and `UnderlayMask` inactive. `mov w20, w1` becomes `mov w20, wzr`, so the argument
is always false. (Disabling the method instead froze the prefab state: an active cutout, i.e. a
see-through hole, which made the main screen black.)

### Presentation materials — Materials `sharedassets0` #9 `BlankPresentationScreen`, #25 `PresentationScreen`
Both pointed at `Unlit/Texture` (no `_YPlane`/`_UVPlane`, black `_Color`), so YUV peer video bound to
them showed black. They become full copies of the `Desktop` material (#12,
`BigscreenVR/Unlit/Desktop`, samplerExternalOES Y/UV planes, multiview variant), names kept.

### Per-eye 3D for shared screens — Shader `sharedassets0` #327 `BigscreenVR/Unlit/Desktop`
`PresentationSection.FormatToSBS/OverUnder/...` only set the global `_3dMode`
(0 flat, 1 SBS, 2 over-under, 3 SBS right-left, 4 under-over) and `_3dModeDSL`. On Quest the
OVROverlay did the eye split; the Desktop shader ignored `_3dMode`, so a host's SBS stream showed
both halves side by side. The patch adds the split to the vertex stage of both variants:

- multi-pass: by `unity_StereoEyeIndex` (the Frame build renders multi-pass);
- `STEREO_MULTIVIEW_ON`: by `gl_ViewID_OVR`.

Length-preserving again (banner comments removed, spaces padded); all four stage/variant
combinations compile cleanly with `glslangValidator`.

## Subtitles

### `subtitle-duration` + `subtitle-cave` — `<SubtitleTimer>d__103$$MoveNext` @0xdb973c
`OnSubtitleCue` shows a cue and starts `SubtitleTimer(cue.Duration.TotalSeconds)`, which waits and
clears the text. CEA-608 captions (TV channels) carry no duration, so each line vanished after one
frame. The `fcvt s0, d8` that converts the duration branches to a 6-instruction cave:
`s0 = d > 0.125 ? d : 4.0` (NaN takes the fallback). A new cue stops the previous timer, so lines
still change on time. The cave lives at 0x149fd88, the dead remainder of `UpdateRegularOverlay`
behind the stub.

## Verification

- `bspatch.code_patches.apply` checks every original word (and the prologues of every function the
  new code calls), writes, then checks words and disassembly.
- `bspatch.asset_patches` checks names/shader ids before editing; the build re-reads the saved bundle
  and checks every patched object; shader blobs must keep an intact subprogram table.
- `tests/test_bspatch.py` checks the generated words against the build tested on the headset.
- `dev/apk_diff.py original.apk patched.apk` shows exactly 56 changed code words (12 runs) and 7 changed
  objects (shaders 6, 319, 327; materials 9, 25; renderers 3538, 3566). The one-command build is
  byte-identical (sha256 `3cf780ee…`) to the headset-tested build.
