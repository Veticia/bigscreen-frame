# Inspecting Bigscreen yourself

What you need to find, check or extend a patch.

## Setup

1. Extract the IL2CPP pieces from the APK:

   ```bash
   mkdir -p work && cd work
   unzip -o ../com.bigscreenvr.bigscreen.apk lib/arm64-v8a/libil2cpp.so \
       assets/bin/Data/Managed/Metadata/global-metadata.dat
   ```

2. Run [Il2CppDumper](https://github.com/Perfare/Il2CppDumper) on them (needs .NET):

   ```bash
   Il2CppDumper lib/arm64-v8a/libil2cpp.so assets/bin/Data/Managed/Metadata/global-metadata.dat dump/
   ```

   You get `dump/dump.cs` (classes, fields with offsets, methods with RVAs), `script.json` and
   `stringliteral.json`. In this build RVAs equal file offsets.

3. `pip install -r requirements.txt pyelftools` for the dev tools.

## Tools (`dev/`)

| Tool | Use |
|---|---|
| `il2cpp_disasm.py --lib L --dump D 'Class$$Method'` | Symbolized disassembly: calls named, metadata slots and **string literals** resolved (through the ELF relocations) |
| `il2cpp_disasm.py ... --callers 'Class$$Method'` | Direct callers (`bl`/`b`) of a method |
| `apk_diff.py a.apk b.apk` | Changed zip members, changed code word runs, changed Unity objects |
| `unity_shaders.py app.apk list --grep OES` | Shaders with blob-table state and multiview code |
| `unity_shaders.py app.apk dump sharedassets0.assets#327 out/` | GLSL of each subprogram |
| `unity_shaders.py app.apk check` | Shaders whose subprogram table is broken |
| `unity_tree.py app.apk sharedassets0.assets 1223` | GameObject hierarchy with components, materials, shaders |
| `unity_tree.py app.apk --refs sharedassets0.assets 18` | Renderers that use a material |

## Reading IL2CPP ARM64

- `x0` = `this` (instance methods), then the arguments; the last argument is the `MethodInfo*`
  (passing `xzr` is fine for the calls patched here).
- Field access is `ldr/strb xN, [x0, #offset]` with the offsets from `dump.cs`.
- `adrp x8, page; ldr x8, [x8, #off]; ldr x0, [x8]` loads a metadata slot (type, method or string
  literal). Slots are filled through relocations; `il2cpp_disasm.py` resolves them.
- Interface calls go through the class's interface table (`ldrh w9, [x8, #0x12a]` loops); virtual calls are
  `ldp x9, x1, [x8, #slot]; blr x9`.
- `bl 0xab8394` / `0xab8444` / `0xab8468` and friends are IL2CPP runtime helpers (metadata init, class init,
  null-reference throw).

## Unity assets

- `data.unity3d` holds `globalgamemanagers.assets`, `sharedassets0.assets`, `resources.assets`, ...
  Objects are addressed by (file, path id). A PPtr's `m_FileID` indexes the file's externals list,
  so a reference can only point into files that list lists: `sharedassets0` can reach
  `globalgamemanagers` (#1) but not `resources.assets`.
- Shader blobs: see the docstring of `bigscreen/bspatch/shader_blob.py`. Keep edits length-preserving.
- UnityPy: `obj.save_typetree(tree)` stores the new data and returns it; `obj.get_raw_data()` keeps
  returning the old bytes until the bundle is saved and reloaded.

## Testing on the headset

FramePort writes `~/.local/share/frameport/logs/<package>-session-*.log` when the app exits. Useful greps:

- `FrameBridge: xrCreateSwapchain` / `new layer ... usable=` — which compositor layers exist;
- `Updating the regular texture` / `Updating the overlay texture` — which screen path is fed;
- `Failed to load GpuProgram`, `GLSL link error` — shader problems;
- `MediaCodec::reclaim`, `signalError`, `Player release timed out` — video decoder errors ([OPEN_ISSUES.md](OPEN_ISSUES.md)).

Note: capstone formats some floating-point immediates with the system locale (`#4,00000000`); compare
instruction words, not text, for those.
