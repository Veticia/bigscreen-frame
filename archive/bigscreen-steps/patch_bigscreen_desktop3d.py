#!/usr/bin/env python3
"""Patch #12: per-eye 3D (SBS / over-under) for presentation screens in BigscreenVR/Unlit/Desktop.

Why: with the presentation sections drawn in-scene (patch #11), a host's
side-by-side 3D stream showed both halves next to each other. On Quest the
OVROverlay did the eye split; PresentationSection.FormatTo* only sets the global
`_3dMode` (0 flat, 1 SBS, 2 over-under, 3 SBS right-left, 4 under-over; the same
convention as Unlit/BSVR_Texture3D_OES) and the Desktop shader (sharedassets0
#327) ignores it.

Patch, in both subprograms of shader 327 (each holds both the multi-pass and the
STEREO_MULTIVIEW_ON vertex code):
  * multi-pass vertex: declare `uniform int _3dMode; uniform int unity_StereoEyeIndex;`
    and after `uvs = in_TEXCOORD0.xy;` take the eye's half of the frame
    (unity_StereoEyeIndex, set by Unity per eye pass);
  * multiview vertex: declare `uniform int _3dMode;` and replace the no-op
    `vs_TEXCOORD0.y = vs_TEXCOORD0.y;` with the same split by gl_ViewID_OVR.
Length-preserving: the banner comments (`//////... SINGLE/MULTI PASS STEREO`)
and the two "To account for color data loss" comments are removed and the
difference is padded with spaces, so blob offsets and the GLSL length prefixes
stay exactly as Unity wrote them.

Usage:
    python3 tools/patch_bigscreen_desktop3d.py <in.apk> <out.apk>
"""

import pathlib
import re
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
    import lz4.block
except ImportError:
    sys.exit('UnityPy and lz4 are required:  pip install UnityPy lz4')

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import patch_bigscreen_oesblit_fix as oesblit  # noqa: E402

BUNDLE_MEMBER = 'assets/bin/Data/data.unity3d'
SHADER = ('sharedassets0.assets', 327, 'BigscreenVR/Unlit/Desktop')

MP_UV = b'uvs = in_TEXCOORD0.xy;'
MP_UV_NEW = (b'uvs = in_TEXCOORD0.xy;float e=float(unity_StereoEyeIndex);'
             b'if(_3dMode==1||_3dMode==3)uvs.x=uvs.x*.5+.5*(_3dMode==1?e:1.-e);'
             b'else if(_3dMode>1)uvs.y=uvs.y*.5+.5*(_3dMode==4?e:1.-e);')
MV_UV = b'vs_TEXCOORD0.y = vs_TEXCOORD0.y;'
MV_UV_NEW = (b'float e=float(gl_ViewID_OVR);'
             b'if(_3dMode==1||_3dMode==3)vs_TEXCOORD0.x=vs_TEXCOORD0.x*.5+.5*(_3dMode==1?e:1.-e);'
             b'else if(_3dMode>1)vs_TEXCOORD0.y=vs_TEXCOORD0.y*.5+.5*(_3dMode==4?e:1.-e);')
MV_DECL_AT = b'layout(num_views = 2) in;'
MV_DECL = b'uniform int _3dMode;' + MV_DECL_AT
MP_POS = b'          in highp vec3 in_POSITION0;'  # 2nd occurrence = multi-pass vertex
MP_DECL = b'          uniform int _3dMode;uniform int unity_StereoEyeIndex;' + MP_POS[10:]
COMMENT_RX = re.compile(rb'//////////+ (?:SINGLE|MULTI) PASS STEREO|// To account for color data loss[^\r\n]*')
PAD = b'\x00PAD\x00'


def fix_source(src: bytes) -> bytes:
    for needle, count in ((MP_UV, 1), (MV_UV, 1), (MV_DECL_AT, 1), (MP_POS, 2), (b'_3dMode', 0),
                          (b'unity_StereoEyeIndex', 0)):
        if src.count(needle) != count:
            raise SystemExit(f'ABORT: {needle!r} occurs {src.count(needle)}x, expected {count}.')
    comments = COMMENT_RX.findall(src)
    if len(comments) != 6:
        raise SystemExit(f'ABORT: expected 6 removable comments, found {len(comments)}.')
    new = src.replace(MP_UV, MP_UV_NEW).replace(MV_UV, MV_UV_NEW).replace(MV_DECL_AT, MV_DECL)
    first = new.find(MP_POS)
    second = new.find(MP_POS, first + 1)
    new = new[:second] + MP_DECL + new[second + len(MP_POS):]
    removed = 0

    def drop(_m):
        nonlocal removed
        removed += 1
        return PAD if removed == 1 else b''
    new = COMMENT_RX.sub(drop, new)
    deficit = len(src) - (len(new) - len(PAD))
    if deficit < 0:
        raise SystemExit(f'ABORT: edit is {-deficit} bytes too long.')
    new = new.replace(PAD, b' ' * deficit)
    assert len(new) == len(src)
    return new


def fix_blob(raw: bytes) -> bytes:
    if not oesblit.table_ok(raw):
        raise SystemExit('ABORT: shader 327 blob table is not intact.')
    out = bytearray(raw[:4 + 12 * len(oesblit.table(raw))])
    for off, length, _seg in oesblit.table(raw):
        sub = raw[off:off + length]
        v = sub.find(b'#version')
        n = struct.unpack_from('<I', sub, v - 4)[0]
        out += sub[:v] + fix_source(sub[v:v + n]) + sub[v + n:]
    out = bytes(out)
    if len(out) != len(raw) or not oesblit.table_ok(out):
        raise SystemExit('ABORT: repaired blob failed the table check.')
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_desktop3d.py <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    with zipfile.ZipFile(src) as zsrc:
        members = zsrc.infolist()
        work = SCRATCH / 'desktop3d-in.unity3d'
        work.write_bytes(zsrc.read(BUNDLE_MEMBER))

    env = UnityPy.load(str(work))
    obj = next((o for o in env.objects if o.type.name == 'Shader' and
                (o.assets_file.name, o.path_id) == SHADER[:2]), None)
    if obj is None:
        print('ABORT: shader 327 not found.')
        return 1
    t = obj.read_typetree()
    if t['m_ParsedForm']['m_Name'] != SHADER[2]:
        print(f'ABORT: shader 327 is {t["m_ParsedForm"]["m_Name"]!r}.')
        return 1
    fixed = fix_blob(oesblit.decompress(t))
    comp = lz4.block.compress(fixed, store_size=False)
    t['compressedBlob'] = list(comp)
    t['compressedLengths'] = [[len(comp)]]
    t['decompressedLengths'] = [[len(fixed)]]
    obj.save_typetree(t)
    print(f'patched: {SHADER[2]} per-eye 3D split ({len(fixed):,} bytes, offsets unchanged).')

    out_dir = SCRATCH / 'desktop3d-out'
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env.save(pack='original', out_path=str(out_dir) + '/')
    saved_bundle = next(p for p in out_dir.iterdir() if p.is_file())

    venv = UnityPy.load(str(saved_bundle))
    vobj = next(o for o in venv.objects if o.type.name == 'Shader' and (o.assets_file.name, o.path_id) == SHADER[:2])
    raw = oesblit.decompress(vobj.read_typetree())
    if raw != fixed or not oesblit.table_ok(raw) or raw.count(MP_UV_NEW) != 2 or raw.count(MV_UV_NEW) != 2:
        print('ABORT: shader 327 does not read back as written.')
        return 1
    print('verify: shader 327 re-read clean (2 subprograms, both stereo paths present).')

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout, zipfile.ZipFile(src) as zsrc:
        for info in members:
            zout.writestr(info, saved_bundle.read_bytes() if info.filename == BUNDLE_MEMBER
                          else zsrc.read(info.filename))
    work.unlink()
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
