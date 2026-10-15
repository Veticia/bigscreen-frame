#!/usr/bin/env python3
"""Patch #9: draw the browser/tablet video on its in-scene mesh instead of an OVR quad layer.

Why: on the Frame the compositor draws every quad layer above the projection
layer (FramePort docs/FRAME_RUNTIME.md: "Quad layers are drawn above every
projection layer regardless of submission order"). Bigscreen shows the
browser/YouTube tablet through an OVROverlay quad (BigMediaRenderer
`regularOverlay`), so hands, avatars and props in front of the screen are
covered by it. Bigscreen already has an in-scene path for the same content,
`BigMediaRenderer$$UpdateRegularScreen` (CreateExternalTexture wrapper ->
`regularScreenRenderer.material.mainTexture`), which `Update` never calls.

Patch, two parts:
  1. libil2cpp.so: `UpdateRegularOverlay` (0x149fd1c) becomes a stub that
     disables the `regularOverlay` component (Behaviour.set_enabled(false), so
     OVROverlay submits no quad) and tail-calls `UpdateRegularScreen` with the
     same arguments (same signature: this, texId, frameSize, resizeRequired).
     Update keeps calling it because the overlay's GameObject stays active.
  2. data.unity3d: material `MovieScreen` (sharedassets0 path 18), used by the
     tablet's `RegularScreen` mesh and the five browser prefabs' RegularScreen
     meshes, has its shader switched from `Unlit/BSVR_Texture3D` (sampler2D,
     can't sample the OES video texture) to `BigscreenVR/Unlit/OESBlit`
     (globalgamemanagers shader 6, samplerExternalOES). Same-size PPtr change:
     (0, 319) -> (1, 6); external #1 of sharedassets0 is globalgamemanagers.assets.
     OESBlit tests depth LessEqual (opaque objects in front occlude it), its
     frame-ratio uniforms default to 1.0.

Expected input: 8ovr-style code + repaired OESBlit
(patch_bigscreen_oesblit_fix.py, e.g. 16flip).

Usage:
    python3 tools/patch_bigscreen_meshscreen.py <in.apk> <out.apk>
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
import patch_bigscreen_oesblit_fix as oesblit  # noqa: E402

BUNDLE_MEMBER = 'assets/bin/Data/data.unity3d'
LIB_SUFFIX = 'lib/arm64-v8a/libil2cpp.so'

UPDATE_REGULAR_OVERLAY = 0x149FD1C
UPDATE_REGULAR_SCREEN = 0x14A0584
BEHAVIOUR_SET_ENABLED = 0x18639EC
PROLOGUE = [0xD10183FF, 0xA90167FA, 0xA9025FF8, 0xA90357F6, 0xA9044FF4, 0xA9057BFD, 0x910143FD]
SCREEN_PROLOGUE = [0xD10143FF, 0xA9015FF8, 0xA90257F6, 0xA9034FF4, 0xA9047BFD, 0x910103FD]
SET_ENABLED_PROLOGUE = [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD]

GUARDS = [
    (0x1080594, [0xAA1303E0, 0x52800021, 0x1400003C], 'patch1 EntitlementCheck$$Start'),
    (0xB895CC, [0x52800000, 0xD65F03C0], 'patch2 RequiresOculusPlatform->false'),
    (0xBC85C4, [0xD65F03C0, 0xD503201F], 'patch3 GetRoomCodeFromClipboard no-op'),
    (0x1E25EFC, [0xD65F03C0, 0xD503201F], 'texparams set_wrapMode no-op'),
    (0xBFD1C4, [0xD65F03C0, 0xD503201F], 'patch6 ToggleOverlay no-op'),
]

MATERIAL = ('sharedassets0.assets', 18, 'MovieScreen')
OLD_SHADER = (0, 319)
NEW_SHADER = (1, 6)  # globalgamemanagers.assets : BigscreenVR/Unlit/OESBlit


def b_imm(src: int, dst: int, link: bool) -> int:
    off = (dst - src) // 4
    assert -(1 << 25) <= off < (1 << 25)
    return (0x94000000 if link else 0x14000000) | (off & 0x3FFFFFF)


def stub_words(base: int) -> list[int]:
    w = [
        0xA9BD7BFD,  # stp x29, x30, [sp, #-0x30]!
        0x910003FD,  # mov x29, sp
        0xA90153F3,  # stp x19, x20, [sp, #0x10]
        0xA9025BF5,  # stp x21, x22, [sp, #0x20]
        0xAA0003F3,  # mov x19, x0          this
        0xAA0103F4,  # mov x20, x1          texId
        0xAA0203F5,  # mov x21, x2          frameSize
        0x2A0303F6,  # mov w22, w3          resizeRequired
        0xF9401660,  # ldr x0, [x19, #0x28] regularOverlay
        0xB4000080,  # cbz x0, +0x10
        0x2A1F03E1,  # mov w1, wzr          enabled = false
        0xAA1F03E2,  # mov x2, xzr          methodInfo
        None,        # bl Behaviour$$set_enabled
        0xAA1303E0,  # mov x0, x19
        0xAA1403E1,  # mov x1, x20
        0xAA1503E2,  # mov x2, x21
        0x2A1603E3,  # mov w3, w22
        0xAA1F03E4,  # mov x4, xzr
        0xA9425BF5,  # ldp x21, x22, [sp, #0x20]
        0xA94153F3,  # ldp x19, x20, [sp, #0x10]
        0xA8C37BFD,  # ldp x29, x30, [sp], #0x30
        None,        # b UpdateRegularScreen
    ]
    w[12] = b_imm(base + 12 * 4, BEHAVIOUR_SET_ENABLED, True)
    w[21] = b_imm(base + 21 * 4, UPDATE_REGULAR_SCREEN, False)
    return w


EXPECTED_ASM = [
    'stp x29, x30, [sp, #-0x30]!', 'mov x29, sp', 'stp x19, x20, [sp, #0x10]', 'stp x21, x22, [sp, #0x20]',
    'mov x19, x0', 'mov x20, x1', 'mov x21, x2', 'mov w22, w3', 'ldr x0, [x19, #0x28]',
    f'cbz x0, #{UPDATE_REGULAR_OVERLAY + 13 * 4:#x}', 'mov w1, wzr', 'mov x2, xzr',
    f'bl #{BEHAVIOUR_SET_ENABLED:#x}', 'mov x0, x19', 'mov x1, x20', 'mov x2, x21', 'mov w3, w22',
    'mov x4, xzr', 'ldp x21, x22, [sp, #0x20]', 'ldp x19, x20, [sp, #0x10]', 'ldp x29, x30, [sp], #0x30',
    f'b #{UPDATE_REGULAR_SCREEN:#x}',
]


def words(data, addr, n):
    return list(struct.unpack_from(f'<{n}I', data, addr))


def check_stub(data) -> bool:
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    got = [f'{i.mnemonic} {i.op_str}' for i in
           md.disasm(bytes(data[UPDATE_REGULAR_OVERLAY:UPDATE_REGULAR_OVERLAY + 4 * len(EXPECTED_ASM)]),
                     UPDATE_REGULAR_OVERLAY)]
    ok = got == EXPECTED_ASM
    for g, e in zip(got, EXPECTED_ASM):
        if g != e:
            print(f'  MISMATCH: got {g!r}, expected {e!r}')
    return ok and len(got) == len(EXPECTED_ASM)


def patch_lib(data: bytearray) -> None:
    for addr, expected, label in GUARDS:
        if words(data, addr, len(expected)) != expected:
            raise SystemExit(f'ABORT: {label} @0x{addr:x} does not match an 8ovr-style build.')
    print('guard OK: input carries patches #1-#3, texparams, patch #6.')
    for addr, expected, label in ((UPDATE_REGULAR_OVERLAY, PROLOGUE, 'UpdateRegularOverlay'),
                                  (UPDATE_REGULAR_SCREEN, SCREEN_PROLOGUE, 'UpdateRegularScreen'),
                                  (BEHAVIOUR_SET_ENABLED, SET_ENABLED_PROLOGUE, 'Behaviour$$set_enabled')):
        if words(data, addr, len(expected)) != expected:
            raise SystemExit(f'ABORT: {label} @0x{addr:x} prologue mismatch '
                             f'({[hex(x) for x in words(data, addr, len(expected))]}).')
        print(f'fingerprint OK: {label} @0x{addr:x}.')
    for i, w in enumerate(stub_words(UPDATE_REGULAR_OVERLAY)):
        struct.pack_into('<I', data, UPDATE_REGULAR_OVERLAY + 4 * i, w)
    if not check_stub(data):
        raise SystemExit('ABORT: stub disassembly does not match.')
    print('patched: UpdateRegularOverlay -> disable regularOverlay, tail-call UpdateRegularScreen (disassembly verified).')


def find_objects(env):
    mat = shader6 = None
    for obj in env.objects:
        key = (obj.assets_file.name, obj.path_id)
        if obj.type.name == 'Material' and key == MATERIAL[:2]:
            mat = obj
        elif obj.type.name == 'Shader' and key == ('globalgamemanagers.assets', 6):
            shader6 = obj
    return mat, shader6


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_meshscreen.py <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

    with zipfile.ZipFile(src) as zsrc:
        members = zsrc.infolist()
        lib_name = next(n for n in zsrc.namelist() if n.endswith(LIB_SUFFIX))
        lib = bytearray(zsrc.read(lib_name))
        work = SCRATCH / 'meshscreen-in.unity3d'
        work.write_bytes(zsrc.read(BUNDLE_MEMBER))

    patch_lib(lib)

    env = UnityPy.load(str(work))
    mat, shader6 = find_objects(env)
    if mat is None or shader6 is None:
        print('ABORT: MovieScreen material or OESBlit shader not found.')
        return 1
    st = shader6.read_typetree()
    if (st.get('m_ParsedForm') or {}).get('m_Name') != 'BigscreenVR/Unlit/OESBlit':
        print('ABORT: globalgamemanagers shader 6 is not OESBlit.')
        return 1
    raw6 = oesblit.decompress(st)
    if not oesblit.table_ok(raw6) or b'uniform highp int _linearToSrgb;' not in raw6:
        print('ABORT: OESBlit is not repaired; run patch_bigscreen_oesblit_fix.py first.')
        return 1
    print('guard OK: OESBlit repaired (table intact, uniforms qualified).')
    ext = mat.assets_file.externals[NEW_SHADER[0] - 1].path
    if not ext.endswith('globalgamemanagers.assets'):
        print(f'ABORT: sharedassets0 external #{NEW_SHADER[0]} is {ext!r}.')
        return 1
    mt = mat.read_typetree()
    if mt.get('m_Name') != MATERIAL[2] or (mt['m_Shader']['m_FileID'], mt['m_Shader']['m_PathID']) != OLD_SHADER:
        print(f'ABORT: material {MATERIAL} is {mt.get("m_Name")!r} with shader {mt["m_Shader"]}.')
        return 1
    before = len(mat.get_raw_data())
    mt['m_Shader'] = {'m_FileID': NEW_SHADER[0], 'm_PathID': NEW_SHADER[1]}
    if len(mat.save_typetree(mt)) != before:
        print('ABORT: material size changed.')
        return 1
    print(f'patched: material MovieScreen shader {OLD_SHADER} -> {NEW_SHADER} (OESBlit), same size.')

    out_dir = SCRATCH / 'meshscreen-out'
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env.save(pack='original', out_path=str(out_dir) + '/')
    saved_bundle = next(p for p in out_dir.iterdir() if p.is_file())
    print(f'saved bundle: {saved_bundle.name} ({saved_bundle.stat().st_size:,} bytes)')

    # ---- verify --------------------------------------------------------
    venv = UnityPy.load(str(saved_bundle))
    vmat, vshader = find_objects(venv)
    vt = vmat.read_typetree()
    if (vt['m_Shader']['m_FileID'], vt['m_Shader']['m_PathID']) != NEW_SHADER or vt['m_Name'] != 'MovieScreen':
        print('ABORT: saved material does not read back as written.')
        return 1
    if oesblit.decompress(vshader.read_typetree()) != raw6:
        print('ABORT: OESBlit changed while saving.')
        return 1
    print('verify: material reads back with OESBlit; OESBlit blob unchanged.')

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
    if not check_stub(back) or any(words(back, a, len(e)) != e for a, e, _ in GUARDS):
        print('ABORT: output libil2cpp.so verification failed.')
        return 1
    work.unlink()
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
