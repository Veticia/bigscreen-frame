#!/usr/bin/env python3
"""Clone a known-good material config onto another material.

Why: Bigscreen's `PresentationScreen` / `BlankPresentationScreen` materials point
at `Unlit/Texture` (path_id 301), which has no `_YPlane` / `_UVPlane` properties.
The YUV texture binds that feed peer-desktop video therefore fail silently and
the screen renders black (or, after a naive shader-pointer-only swap, magenta,
because the material's property block and keywords no longer match its shader).

The `Desktop` material (path_id 12 in sharedassets0.assets) is the worked
example: it points at `BigscreenVR/Unlit/Desktop` (path_id 327) and carries the
full YUV property block:

    m_ShaderKeywords: "_EMISSION"
    TexEnvs: _UVPlane, _YPlane
    Floats : _Brightness, _ClipPower, _DiffuseAmount, _DisintegrateAmount,
             _DissolveEdge, _GlossMapScale, _GlossyReflections, _GrungeAmount,
             _RimPower, _Scale, _Tighten, _TileFactor, _Width, ...

The Android direct-movie example is `MovieScreenAndroid`: it points at the
external-OES movie shader and carries the matching plain/external property
block. The regular `MovieScreen` material points at the plain `sampler2D`
movie shader, while direct Android playback wraps the decoded external
texture and assigns it as `_MainTex`.

This tool copies a complete configuration (shader pointer + saved properties +
keywords + lightmap flags) from `Desktop` onto a chosen target material, keeping
only the target's own name. One material is changed per run so a bad result is
easy to attribute.

Usage:
    python3 tools/patch_bigscreen_shader.py <in.apk> <out.apk> [--target NAME]
    python3 tools/patch_bigscreen_shader.py <in.apk> <out.apk> \
        --template MovieScreenAndroid --template-shader 825 \
        --target MovieScreen --old-shader 319

    --target defaults to PresentationScreen. Use --target BlankPresentationScreen
    for the other one. Use --revert to put the original pointer back.

The script re-reads the written bundle and proves the change + that nothing else
in the material differs from the Desktop template except m_Name.
"""

import argparse
import copy
import pathlib
import sys
import zipfile

try:
    import UnityPy
except ImportError:
    sys.exit('UnityPy is required:  pip install UnityPy')

BUNDLE_MEMBER = 'assets/bin/Data/data.unity3d'
TEMPLATE_MATERIAL = 'Desktop'          # the known-good YUV material
TEMPLATE_SHADER_PID = 327              # BigscreenVR/Unlit/Desktop
OLD_SHADER_PID = 301                   # Unlit/Texture, no YUV properties


def find_material(env, name):
    for obj in env.objects:
        if obj.type.name != 'Material':
            continue
        t = obj.read_typetree()
        if t.get('m_Name') == name:
            return obj, t
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src', type=pathlib.Path)
    ap.add_argument('dst', type=pathlib.Path)
    ap.add_argument('--target', default='PresentationScreen')
    ap.add_argument('--template', default=TEMPLATE_MATERIAL)
    ap.add_argument('--template-shader', type=int, default=TEMPLATE_SHADER_PID)
    ap.add_argument('--old-shader', type=int, default=OLD_SHADER_PID)
    args = ap.parse_args()

    with zipfile.ZipFile(args.src) as zsrc:
        members = zsrc.infolist()
        bundle_bytes = zsrc.read(BUNDLE_MEMBER)
        non_bundle = {i.filename: zsrc.read(i.filename)
                      for i in members if i.filename != BUNDLE_MEMBER}

    full_path = pathlib.Path('/tmp/opencode/shaderswap-in.unity3d')
    full_path.write_bytes(bundle_bytes)
    env = UnityPy.load(str(full_path))

    _, template = find_material(env, args.template)
    if template is None or template['m_Shader']['m_PathID'] != args.template_shader:
        print(f'ABORT: template material {args.template!r} not found or '
              f'not pointing at shader {args.template_shader}.')
        return 1

    target_obj, target = find_material(env, args.target)
    if target_obj is None:
        print(f'ABORT: target material {args.target!r} not found.')
        return 1
    if target['m_Shader']['m_PathID'] != args.old_shader:
        print(f'note: target already points at shader '
              f"{target['m_Shader']['m_PathID']} (expected {args.old_shader}).")

    # Build the new material config: a full copy of the template, renamed.
    new_material = copy.deepcopy(template)
    new_material['m_Name'] = args.target

    # Difference check that matters: everything except m_Name must come from the
    # template. This is what the naive pointer-only swap got wrong.
    differing = [k for k in new_material
                 if k != 'm_Name' and new_material[k] != target.get(k)]
    print(f'fields taken from the template that differ from the old material: '
          f'{differing}')

    saved = target_obj.save_typetree(new_material)
    target_obj.data = bytearray(saved)

    out_dir = pathlib.Path('/tmp/opencode/shaderswap-out')
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env.save(pack='original', out_path=str(out_dir) + '/')

    saved_bundle = None
    for p in out_dir.iterdir():
        if p.is_file():
            saved_bundle = p
            break
    if saved_bundle is None:
        print('ABORT: UnityPy did not write the bundle.')
        return 1
    print(f'saved bundle: {saved_bundle.name} ({saved_bundle.stat().st_size:,} bytes)')

    # ---- verify --------------------------------------------------------
    verify_env = UnityPy.load(str(out_dir) + '/')
    _, vtarget = find_material(verify_env, args.target)
    _, vtemplate = find_material(verify_env, args.template)
    if vtarget is None or vtemplate is None:
        print('ABORT: verification could not re-read the materials.')
        return 1
    ok_shader = vtarget['m_Shader']['m_PathID'] == args.template_shader
    diffs = [k for k in vtarget
             if k != 'm_Name' and vtarget[k] != vtemplate[k]]
    print(f'verify: {args.target} shader pid = '
          f"{vtarget['m_Shader']['m_PathID']} ({'ok' if ok_shader else 'WRONG'})")
    print(f'verify: fields differing from template: {diffs or "none"}')
    if not ok_shader or diffs:
        print('ABORT: verification failed.')
        return 1

    with zipfile.ZipFile(args.dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in members:
            blob = saved_bundle.read_bytes() if info.filename == BUNDLE_MEMBER \
                else non_bundle[info.filename]
            zout.writestr(info, blob)
    print(f'wrote {args.dst}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())