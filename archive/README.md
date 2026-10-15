# Archive: step-by-step patch scripts

The scripts written during the investigation, one per step, in the order of
[docs/DEVLOG.md](../docs/DEVLOG.md). Each takes an APK of the *previous* step and writes the next one.
They are kept as a record of what was tried. **Use `bigscreen/build_bigscreen.py` to build**: it applies
the final patch set from the original APK in one pass and produces a byte-identical result.

Not maintained: they write scratch files to `/tmp/opencode/` and expect intermediate builds.

| Script | Step | In the final build? |
|---|---|---|
| `patch_bigscreen.py` | entitlement, PC login, clipboard (+ optional YUV-binder no-ops, never used: `--skip-yuv-bypass`) | yes |
| `build_bigscreen_fixed.py` | first combined build: code #1–3 + presentation materials (property merge variant) | superseded |
| `patch_bigscreen_shader.py` | material clone (`Desktop` → PresentationScreen / BlankPresentationScreen) | yes (as a full copy) |
| `patch_bigscreen_material.py` | material clone that keeps the target's texture slots (5movie/6movie experiments) | no |
| `patch_bigscreen_shader_precision.py` | OESBlit precision fix. **Broken**: grows the GLSL and corrupts the blob table | no, replaced |
| `patch_bigscreen_texparams.py` | `Texture.set_wrapMode/filterMode` no-ops | yes |
| `patch_bigscreen_overlay.py` | `ToggleOverlay` → `ret` (8ovr) | no, replaced by forcing `false` |
| `patch_bigscreen_regularoverlay.py` | `UpdateRegularOverlay` → `ret` (9ovr) | no |
| `patch_bigscreen_rttexture.py` | "Route A": RenderTexture blit into the overlay (10rt–14rt) | no, dead end |
| `patch_bigscreen_oesblit_fix.py` | length-preserving OESBlit fix (+ `--flip-y`) (15oes/16flip) | yes |
| `patch_bigscreen_meshscreen.py` | BigMediaRenderer in-scene stub v1; MovieScreen → OESBlit (17mesh) | stub yes, material no |
| `patch_bigscreen_oesscreen.py` | BigUIBrowser Regular target, shader 319 ← 825, impostors off (18oes/19screen) | yes |
| `patch_bigscreen_presentation.py` | `ToggleOverlay(false)` (20present) | yes |
| `patch_bigscreen_desktop3d.py` | Desktop shader per-eye 3D (21stereo) | yes |
| `patch_bigscreen_noprotected.py` | `isProtectedExternalEnabled = false` (22tv) | yes |
| `patch_bigscreen_showscreen.py` | stub v2: also `regularScreen.SetActive(true)` (23show) | yes |
| `patch_bigscreen_subtitles.py` | subtitle fallback duration (24subs) | yes |
