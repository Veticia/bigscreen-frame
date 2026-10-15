#!/usr/bin/env python3
"""Patch #10: show browser and media video on in-scene meshes (depth-correct) with an OES multiview shader.

Why: build 17mesh moved BigMediaRenderer to its mesh, but (a) the browser
window is a different component, BigUIBrowser, with its own OVROverlay quad
(still drawn above everything by the Frame compositor), and (b) the mesh
rendered gray with OESBlit. Bigscreen renders the eyes with GL_OVR_multiview;
OESBlit has no multiview variant, so the eye-pass draw produces nothing.
Bigscreen ships the right shader, `Unlit/BSVR_Texture3D_OES` (resources.assets
825, samplerExternalOES + STEREO_MULTIVIEW_ON variant + _3dModeBigMedia stereo
modes, opaque, ZWrite on), but only via material MovieScreenAndroid in
resources.assets, which sharedassets0 cannot reference (no external entry).

Patch, applied to a 16flip-style APK (8ovr code + repaired OESBlit):
  1. libil2cpp.so, BigMediaRenderer: patch #9 stub (patch_bigscreen_meshscreen:
     UpdateRegularOverlay -> disable regularOverlay, tail-call UpdateRegularScreen).
  2. libil2cpp.so, BigUIBrowser$$UpdateActiveStates @0x10372d8:
     `ldrb w8, [x19, #0x10c]` -> `b 0x103736c`, i.e. always Bigscreen's own
     non-Oculus branch: TextureTarget.Regular, overlayGameObject inactive,
     regularScreen active. UpdateTextures then feeds regularScreenRenderer.
  3. data.unity3d: Shader sharedassets0 #319 (`Unlit/BSVR_Texture3D`, used only
     by material MovieScreen) gets the complete serialized data of shader 825
     (self-consistent; no dependencies), keeping its own name.
     MovieScreen is used by BigMediaRenderer's RegularScreen and the RegularScreen
     of the five browser prefabs.
  4. data.unity3d: the two "Underlay Impostor" MeshRenderers (children of
     BigMediaRenderer's regularOverlay / protectedExternalOverlay, shader
     Oculus/Underlay Impostor) are disabled. On Quest they punch the hole an
     underlay shows through; with the overlay component off (patch #9) the
     GameObject stays active and the impostor drew a gray rectangle 8 cm in
     front of the main screen (tested on 18oes).
  --flip-y (off by default): MovieScreen `_MainTex` scale/offset y (1, 0) ->
     (-1, 1). Tested on 18oes: NOT wanted (picture upside down; the
     RegularScreen transform is already scaled (-1, -1, -1)).

Usage:
    python3 tools/patch_bigscreen_oesscreen.py [--flip-y] <in.apk> <out.apk>
"""

import pathlib
import struct
import sys
import zipfile
import os
import tempfile

# Scratch space for the intermediate bundle (override with BIGSCREEN_SCRATCH).
SCRATCH = pathlib.Path(os.environ.get('BIGSCREEN_SCRATCH', tempfile.gettempdir())) / 'bigscreen-steps'
SCRATCH.mkdir(parents=True, exist_ok=True)

try:
    import UnityPy
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
except ImportError:
    sys.exit('UnityPy and capstone are required:  pip install UnityPy capstone')

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import patch_bigscreen_meshscreen as meshscreen  # noqa: E402
import patch_bigscreen_oesblit_fix as oesblit  # noqa: E402

BUNDLE_MEMBER = meshscreen.BUNDLE_MEMBER

ACTIVE_STATES_SITE = 0x10372D8
ACTIVE_STATES_ORIG = [0x39443268, 0x2A0003F4]  # ldrb w8, [x19, #0x10c]; mov w20, w0
ACTIVE_STATES_REGULAR = 0x103736C

SHADER_DST = ('sharedassets0.assets', 319, 'Unlit/BSVR_Texture3D')
SHADER_SRC = ('resources.assets', 825, 'Unlit/BSVR_Texture3D_OES')
MATERIAL = ('sharedassets0.assets', 18, 'MovieScreen')
IMPOSTOR_RENDERERS = [('sharedassets0.assets', 3538), ('sharedassets0.assets', 3566)]
IMPOSTOR_MATERIAL = (0, 54)  # sharedassets0 'Underlay Impostor'


def patch_active_states(lib: bytearray) -> None:
    if meshscreen.words(lib, ACTIVE_STATES_SITE, 2) != ACTIVE_STATES_ORIG:
        raise SystemExit(f'ABORT: BigUIBrowser$$UpdateActiveStates @0x{ACTIVE_STATES_SITE:x} mismatch.')
    print('fingerprint OK: BigUIBrowser$$UpdateActiveStates @0x10372d8.')
    struct.pack_into('<I', lib, ACTIVE_STATES_SITE, meshscreen.b_imm(ACTIVE_STATES_SITE, ACTIVE_STATES_REGULAR, False))
    if not check_active_states(lib):
        raise SystemExit('ABORT: UpdateActiveStates branch disassembly mismatch.')
    print('patched: BigUIBrowser always uses TextureTarget.Regular (in-scene screen).')


def check_active_states(lib) -> bool:
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    ins = next(md.disasm(bytes(lib[ACTIVE_STATES_SITE:ACTIVE_STATES_SITE + 4]), ACTIVE_STATES_SITE))
    return f'{ins.mnemonic} {ins.op_str}' == f'b #{ACTIVE_STATES_REGULAR:#x}'


def find(env):
    found = {}
    for obj in env.objects:
        key = (obj.assets_file.name, obj.path_id)
        if obj.type.name == 'Shader' and key in (SHADER_DST[:2], SHADER_SRC[:2], ('globalgamemanagers.assets', 6)):
            found[key] = obj
        elif obj.type.name == 'Material' and key == MATERIAL[:2]:
            found[key] = obj
        elif obj.type.name == 'MeshRenderer' and key in IMPOSTOR_RENDERERS:
            found[key] = obj
    return found


def main() -> int:
    args = [a for a in sys.argv[1:] if a != '--flip-y']
    flip_y = '--flip-y' in sys.argv[1:]
    if len(args) != 2:
        print('usage: patch_bigscreen_oesscreen.py [--flip-y] <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(args[0]), pathlib.Path(args[1])
    want_y = (-1.0, 1.0) if flip_y else (1.0, 0.0)

    with zipfile.ZipFile(src) as zsrc:
        members = zsrc.infolist()
        lib_name = next(n for n in zsrc.namelist() if n.endswith(meshscreen.LIB_SUFFIX))
        lib = bytearray(zsrc.read(lib_name))
        work = SCRATCH / 'oesscreen-in.unity3d'
        work.write_bytes(zsrc.read(BUNDLE_MEMBER))

    meshscreen.patch_lib(lib)
    patch_active_states(lib)

    env = UnityPy.load(str(work))
    f = find(env)
    if len(f) != 4 + len(IMPOSTOR_RENDERERS):
        print(f'ABORT: expected objects not found ({sorted(f)}).')
        return 1
    raw6 = oesblit.decompress(f[('globalgamemanagers.assets', 6)].read_typetree())
    if not oesblit.table_ok(raw6) or b'uniform highp int _linearToSrgb;' not in raw6:
        print('ABORT: OESBlit is not repaired; run patch_bigscreen_oesblit_fix.py first.')
        return 1
    print('guard OK: OESBlit repaired.')

    dst_obj, src_obj = f[SHADER_DST[:2]], f[SHADER_SRC[:2]]
    dt, st = dst_obj.read_typetree(), src_obj.read_typetree()
    if dt['m_ParsedForm']['m_Name'] != SHADER_DST[2] or st['m_ParsedForm']['m_Name'] != SHADER_SRC[2]:
        print('ABORT: shader names do not match the expected build.')
        return 1
    if list(dt) != list(st) or st['m_Dependencies'] or st['m_NonModifiableTextures'] or \
            st['m_ParsedForm']['m_Dependencies']:
        print('ABORT: shader 825 is not a self-contained drop-in for 319.')
        return 1
    st['m_ParsedForm']['m_Name'] = SHADER_DST[2]
    st['m_Name'] = dt['m_Name']
    dst_obj.save_typetree(st)
    print(f'patched: shader {SHADER_DST[:2]} now carries {SHADER_SRC[2]} programs (name kept {SHADER_DST[2]!r}).')

    mat = f[MATERIAL[:2]]
    mt = mat.read_typetree()
    if mt['m_Name'] != MATERIAL[2] or (mt['m_Shader']['m_FileID'], mt['m_Shader']['m_PathID']) != (0, 319):
        print(f'ABORT: MovieScreen is {mt["m_Name"]!r} with shader {mt["m_Shader"]} (expected (0, 319)).')
        return 1
    envs = dict(mt['m_SavedProperties']['m_TexEnvs'])
    te = envs['_MainTex']
    if (te['m_Scale']['y'], te['m_Offset']['y']) != (1.0, 0.0):
        print(f'ABORT: MovieScreen _MainTex ST is {te}.')
        return 1
    if flip_y:
        te['m_Scale']['y'], te['m_Offset']['y'] = want_y
        before = len(mat.get_raw_data())
        if len(mat.save_typetree(mt)) != before:
            print('ABORT: material size changed.')
            return 1
        print('patched: MovieScreen _MainTex scale/offset y -> (-1, 1).')
    else:
        print('MovieScreen _MainTex left unflipped.')

    for key in IMPOSTOR_RENDERERS:
        r = f[key]
        rt = r.read_typetree()
        mats = [(m['m_FileID'], m['m_PathID']) for m in rt['m_Materials']]
        if mats != [IMPOSTOR_MATERIAL] or rt['m_Enabled'] not in (1, True):
            print(f'ABORT: renderer {key} is not an enabled Underlay Impostor ({mats}, enabled={rt["m_Enabled"]}).')
            return 1
        before = len(r.get_raw_data())
        rt['m_Enabled'] = type(rt['m_Enabled'])(0)
        if len(r.save_typetree(rt)) != before:
            print('ABORT: renderer size changed.')
            return 1
    print(f'patched: {len(IMPOSTOR_RENDERERS)} Underlay Impostor renderers disabled.')

    out_dir = SCRATCH / 'oesscreen-out'
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env.save(pack='original', out_path=str(out_dir) + '/')
    saved_bundle = next(p for p in out_dir.iterdir() if p.is_file())
    print(f'saved bundle: {saved_bundle.name} ({saved_bundle.stat().st_size:,} bytes)')

    # ---- verify --------------------------------------------------------
    vf = find(UnityPy.load(str(saved_bundle)))
    v319, v825 = vf[SHADER_DST[:2]].read_typetree(), vf[SHADER_SRC[:2]].read_typetree()
    if v319['m_ParsedForm']['m_Name'] != SHADER_DST[2] or v319['compressedBlob'] != v825['compressedBlob'] or \
            v319['m_ParsedForm']['m_SubShaders'] != v825['m_ParsedForm']['m_SubShaders']:
        print('ABORT: shader 319 does not read back as a copy of 825.')
        return 1
    vm = vf[MATERIAL[:2]].read_typetree()
    vte = dict(vm['m_SavedProperties']['m_TexEnvs'])['_MainTex']
    if (vm['m_Shader']['m_FileID'], vm['m_Shader']['m_PathID']) != (0, 319) or \
            (vte['m_Scale']['y'], vte['m_Offset']['y']) != want_y:
        print('ABORT: MovieScreen does not read back as written.')
        return 1
    if any(vf[k].read_typetree()['m_Enabled'] for k in IMPOSTOR_RENDERERS):
        print('ABORT: an Underlay Impostor renderer reads back enabled.')
        return 1
    if oesblit.decompress(vf[('globalgamemanagers.assets', 6)].read_typetree()) != raw6:
        print('ABORT: OESBlit changed while saving.')
        return 1
    print(f'verify: shader 319 == 825 programs, MovieScreen ST y {want_y} on shader 319, '
          'impostors disabled, OESBlit unchanged.')

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        with zipfile.ZipFile(src) as zsrc:
            for info in members:
                if info.filename == BUNDLE_MEMBER:
                    blob = saved_bundle.read_bytes()
                elif info.filename == lib_name:
                    blob = bytes(lib)
                else:
                    blob = zsrc.read(info.filename)
                zout.writestr(info, blob)
    with zipfile.ZipFile(dst) as zin:
        back = bytearray(zin.read(lib_name))
    if not meshscreen.check_stub(back) or not check_active_states(back) or \
            any(meshscreen.words(back, a, len(e)) != e for a, e, _ in meshscreen.GUARDS):
        print('ABORT: output libil2cpp.so verification failed.')
        return 1
    work.unlink()
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
