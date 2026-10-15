#!/usr/bin/env python3
"""Clone a material config cross-file, safely, for a different asset file.

Fix for the 5movie regression: patch_bigscreen_shader.py copied everything from
the template material *verbatim*, including `m_TexEnvs` with their `m_FileID`
external references. When the template lives in a different asset file than the
target (e.g. `MovieScreenAndroid` in resources.assets vs `MovieScreen` in
sharedassets0.assets), `fileID=2` points through a different externals table in
the destination, so Unity stalls on load with a dangling texture reference.

This tool copies the *whole template config* (m_Shader, m_ShaderKeywords,
m_Floats, m_Colors, m_LightmapFlags) onto a target material but keeps the
target's own `m_TexEnvs` (each texenv entry keeps whichever scale/offset/texture
pointer the target already had). The assigned runtime textures are unchanged -
Unity assigns content at runtime via SetTexture anyway.

Usage:
    python3 tools/patch_bigscreen_material.py <in.apk> <out.apk> \\
        --template MovieScreenAndroid --template-shader 825 \\
        --target MovieScreen --old-shader 319
"""

import argparse
import pathlib
import shutil
import sys
import tempfile
import zipfile

try:
    import UnityPy
except ImportError:
    sys.exit('UnityPy is required: pip install UnityPy')

BUNDLE_MEMBER = 'assets/bin/Data/data.unity3d'


def find_material(env, name):
    for obj in env.objects:
        if obj.type.name != 'Material':
            continue
        t = obj.read_typetree()
        if t.get('m_Name') == name:
            return obj, t
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--target')
    ap.add_argument('--template')
    ap.add_argument('--template-shader', type=int)
    ap.add_argument('--old-shader', type=int)
    args = ap.parse_args()

    with zipfile.ZipFile(args.src) as zin:
        blob = zin.read(BUNDLE_MEMBER)
    workdir = pathlib.Path(tempfile.mkdtemp(prefix='matclone-'))
    member_dir = workdir / 'bundle'
    member_dir.mkdir()
    (member_dir / BUNDLE_MEMBER.split('/')[-1]).write_bytes(blob)
    env = UnityPy.load(str(member_dir) + '/')

    target_obj, target = find_material(env, args.target)
    _, template = find_material(env, args.template)
    if target_obj is None or template is None:
        print(f'ABORT: material(s) not found (target={args.target!r}, template={args.template!r}).')
        return 1
    print(f'target   : {target["m_Name"]} file={target_obj.assets_file.name} shader={target["m_Shader"]["m_PathID"]}')
    print(f'template : {template["m_Name"]} file={[o for o in env.objects if o.type.name == "Material" and o.read_typetree().get("m_Name") == args.template][0].assets_file.name} shader={template["m_Shader"]["m_PathID"]}')
    if args.template_shader and template['m_Shader']['m_PathID'] != args.template_shader:
        print(f'ABORT: template {args.template!r} does not point at shader {args.template_shader}.')
        return 1
    if args.old_shader and target['m_Shader']['m_PathID'] != args.old_shader:
        print(f'note: target already points at shader {target["m_Shader"]["m_PathID"]} (expected {args.old_shader}).')

    # Copy template fields wholesale, EXCEPT m_TexEnvs which stay original.
    keep = dict(target['m_SavedProperties'])          # 'm_TexEnvs' stays the target's own
    for field in ('m_ShaderKeywords', 'm_LightmapFlags'):
        target[field] = template.get(field, target.get(field))
    target['m_Shader'] = dict(template['m_Shader'])
    sp = target.get('m_SavedProperties') or {}
    sp['m_Floats'] = template['m_SavedProperties']['m_Floats']
    sp['m_Colors'] = template['m_SavedProperties']['m_Colors']
    if 'm_TexEnvs' in sp:
        continue_texenvs = True
    else:
        continue_texenvs = False
    if not continue_texenvs:
        print('WARNING: target m_TexEnvs missing - copying template instead (cross-file intact task).')
        sp['m_TexEnvs'] = keep.get('m_TexEnvs') or template['m_SavedProperties']['m_TexEnvs']
    else:
        # deliberate: keep the target's texenvs as-is
        pass
    target['m_SavedProperties'] = sp
    # write back
    saved = target_obj.save_typetree(target)
    target_obj.data = bytearray(saved)

    out_bytes = {'data.unity3d': None}
    env.save(pack='original', out_path=str(member_dir) + '/')

    saved_bundle = member_dir / BUNDLE_MEMBER.split('/')[-1]
    print(f'saved bundle: {saved_bundle} ({saved_bundle.stat().st_size:,} bytes)')

    # ---- verify ---------------------------------------------------------
    verify_env = UnityPy.load(str(member_dir) + '/')
    _, vt = find_material(verify_env, args.target)
    if vt is None:
        print('ABORT: verification could not re-read the material.')
        return 1
    ok_shader = vt['m_Shader']['m_PathID'] == template['m_Shader']['m_PathID']
    ok_kw = vt['m_ShaderKeywords'] == template['m_ShaderKeywords']
    ok_floats = vt['m_SavedProperties'].get('m_Floats') == template['m_SavedProperties'].get('m_Floats')
    ok_texenvs = vt['m_SavedProperties'].get('m_TexEnvs') == target['m_SavedProperties'].get('m_TexEnvs')
    print(f'verify: shader={vt["m_Shader"]["m_PathID"]!r} ok={ok_shader}, keywords ok={ok_kw}, '
          f'floats ok={ok_floats}, texenvs preserved={ok_texenvs}')
    if not (ok_shader and ok_kw and ok_floats and ok_texenvs):
        print('ABORT: verification failed.')
        return 1

    with zipfile.ZipFile(args.src) as zin:
        members = zin.infolist()
        with zipfile.ZipFile(args.dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in members:
                blob2 = saved_bundle.read_bytes() if info.filename == BUNDLE_MEMBER else zin.read(info.filename)
                zout.writestr(info, blob2)
    # cleanup
    shutil.rmtree(workdir)
    print(f'wrote {args.dst} ({pathlib.Path(args.dst).stat().st_size:,} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
