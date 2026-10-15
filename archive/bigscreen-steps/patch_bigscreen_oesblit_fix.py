#!/usr/bin/env python3
"""Repair BigscreenVR/Unlit/OESBlit (shader 6): Mesa-safe uniform precision, same byte length.

Why: patch_bigscreen_shader_precision.py rewrites `uniform   int _x;` ->
`uniform   highp int _x;` inside the decompressed shader blob, which grows each
GLSL subprogram by 24 bytes. A Unity shader blob is a container:

    u32 count; count x (u32 offset, u32 length, u32 segment); subprograms...

and each subprogram stores its GLSL source with a u32 length prefix. That tool
only updated the outer LZ4 lengths, so the container table and the source
length prefixes went stale: subprogram 1's offset now points into the tail of
subprogram 0. On the Frame Unity logs, once per launch,

    Failed to load GpuProgram from binary shader data in 'BigscreenVR/Unlit/OESBlit'.

and OESBlit draws nothing. OESBlit is OVROverlay's EnableOES copy material
(tex2DMaterial): OVROverlay.PopulateLayer does Graphics.Blit(OES texture,
tempRT, OESBlit) then CopyTexture(tempRT -> compositor swapchain). With the
shader broken, the browser/YouTube quad layer stays gray. (The room ambient
light kept working because it samples the same OES texture with a different
shader, Bigscreen/FastBlurOES.)

Fix, length-preserving per subprogram:
  * `uniform   int _linearToSrgb;` / `_premultiply;` -> `uniform highp int ...;`
    (4 declarations, +4 bytes each = +16);
  * the duplicated third prelude line `#extension GL_OES_EGL_image_external : enable`
    (an exact repeat of the first line) -> a 32-byte blank comment line (-16).
So every offset, length prefix and alignment stays exactly as Unity wrote it.

--flip-y (tested need on the Frame: with the fix alone, the browser/YouTube
quad shows upside down): the vertex shader's
`uvs.y = in_TEXCOORD0.y * _frameToTextureHeightRatio;` becomes
`uvs.y = (1.0 - in_TEXCOORD0.y) * _frameToTextureHeightRatio;` (+8 bytes, taken
from the blank comment line, which shrinks to 24 bytes). This samples the same
frame region, flipped vertically. OESBlit is only OVROverlay's OES copy material,
so other quads (menus) are unaffected.

Input: any Bigscreen APK whose shader 6 is pristine or carries the broken
precision patch (both are recognised; anything else aborts).

Usage:
    python3 tools/patch_bigscreen_oesblit_fix.py [--flip-y] <in.apk> <out.apk>
"""

import argparse
import hashlib
import pathlib
import re
import struct
import sys
import zipfile

try:
    import UnityPy
    import lz4.block
except ImportError:
    sys.exit('UnityPy and lz4 are required:  pip install UnityPy lz4')

BUNDLE_MEMBER = 'assets/bin/Data/data.unity3d'
SHADER_NAME = 'BigscreenVR/Unlit/OESBlit'
PRISTINE_LEN = 11972
BROKEN_LEN = 12020
SUBPROGRAM_MAGIC = b'\x3c\x51\x07\x0c'

EXT_LINE = b'#extension GL_OES_EGL_image_external : enable\r\n'           # 48 bytes
EXT3_LINE = b'#extension GL_OES_EGL_image_external_essl3 : enable\r\n'
UNIFORM_RX = re.compile(rb'uniform   int (_linearToSrgb|_premultiply);')
BROKEN_RX = re.compile(rb'uniform   highp int (_linearToSrgb|_premultiply);')
UV_Y_LINE = b'uvs.y = in_TEXCOORD0.y * _frameToTextureHeightRatio;'
UV_Y_FLIPPED = b'uvs.y = (1.0 - in_TEXCOORD0.y) * _frameToTextureHeightRatio;'


def decompress(t) -> bytes:
    blob = bytes(bytearray(t['compressedBlob']))
    clens, dlens = t['compressedLengths'], t['decompressedLengths']
    if len(clens) != 1 or len(clens[0]) != 1:
        raise SystemExit(f'ABORT: unexpected blob layout {clens}')
    return lz4.block.decompress(blob[:clens[0][0]], uncompressed_size=dlens[0][0])


def table(raw: bytes):
    n = struct.unpack_from('<I', raw, 0)[0]
    return [struct.unpack_from('<III', raw, 4 + 12 * i) for i in range(n)]


def table_ok(raw: bytes) -> bool:
    ents = table(raw)
    pos = 4 + 12 * len(ents)
    for off, length, _seg in ents:
        if off != pos or raw[off:off + 4] != SUBPROGRAM_MAGIC:
            return False
        pos = off + length
    return pos == len(raw)


def source_span(sub: bytes) -> tuple[int, int]:
    """(start, length) of the GLSL source inside one subprogram."""
    v = sub.find(b'#version')
    if v < 4:
        raise SystemExit('ABORT: no GLSL source in subprogram')
    n = struct.unpack_from('<I', sub, v - 4)[0]
    if not sub[v:v + n].rstrip(b'\x00').endswith(b'#endif\r\n      \n'):
        raise SystemExit('ABORT: GLSL length prefix does not match the source')
    return v, n


def fix_subprogram(sub: bytes, flip_y: bool = False) -> bytes:
    start, n = source_span(sub)
    src = sub[start:start + n]
    head = b'#version 300 es\n' + EXT_LINE + EXT3_LINE + EXT_LINE + EXT3_LINE
    if not src.startswith(head):
        raise SystemExit('ABORT: unexpected OESBlit prelude')
    if len(UNIFORM_RX.findall(src)) != 4:
        raise SystemExit('ABORT: expected 4 unqualified int uniforms')
    new = UNIFORM_RX.sub(rb'uniform highp int \1;', src)
    expect = 16
    if flip_y:
        if new.count(UV_Y_LINE) != 1:
            raise SystemExit('ABORT: expected exactly one uvs.y line in the vertex shader')
        new = new.replace(UV_Y_LINE, UV_Y_FLIPPED)
        expect += len(UV_Y_FLIPPED) - len(UV_Y_LINE)
    grow = len(new) - len(src)
    if grow != expect:
        raise SystemExit(f'ABORT: rewrite grew {grow} bytes, expected {expect}')
    third = len(b'#version 300 es\n' + EXT_LINE + EXT3_LINE)
    blank = b'//' + b' ' * (len(EXT_LINE) - grow - 4) + b'\r\n'
    new = new[:third] + blank + new[third + len(EXT_LINE):]
    if len(new) != len(src):
        raise SystemExit('ABORT: length-preserving rewrite failed')
    return sub[:start] + new + sub[start + n:]


def repaired_blob(raw: bytes, flip_y: bool = False) -> bytes:
    if len(raw) == BROKEN_LEN and not table_ok(raw):
        restored = BROKEN_RX.sub(rb'uniform   int \1;', raw)
        if len(restored) != PRISTINE_LEN or not table_ok(restored):
            raise SystemExit('ABORT: broken-precision OESBlit did not restore cleanly')
        print('  input shader 6: broken precision patch detected -> restored pristine blob')
        raw = restored
    elif len(raw) == PRISTINE_LEN and table_ok(raw):
        print('  input shader 6: pristine')
    else:
        raise SystemExit(f'ABORT: shader 6 blob is neither pristine nor the known broken patch '
                         f'({len(raw)} bytes, table_ok={table_ok(raw)})')
    print(f'  pristine blob sha256 {hashlib.sha256(raw).hexdigest()[:16]}')
    out = bytearray(raw[:4 + 12 * len(table(raw))])
    for off, length, _seg in table(raw):
        out += fix_subprogram(raw[off:off + length], flip_y)
    out = bytes(out)
    if len(out) != len(raw) or not table_ok(out):
        raise SystemExit('ABORT: repaired blob failed the table check')
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src', type=pathlib.Path)
    ap.add_argument('dst', type=pathlib.Path)
    ap.add_argument('--flip-y', action='store_true',
                    help='also flip the copied image vertically (Frame shows it upside down)')
    args = ap.parse_args()

    with zipfile.ZipFile(args.src) as zsrc:
        members = zsrc.infolist()
        bundle_bytes = zsrc.read(BUNDLE_MEMBER)
        non_bundle = {i.filename: zsrc.read(i.filename)
                      for i in members if i.filename != BUNDLE_MEMBER}

    work = pathlib.Path('/tmp/opencode/oesblit-in.unity3d')
    work.write_bytes(bundle_bytes)
    del bundle_bytes
    env = UnityPy.load(str(work))

    target = None
    for obj in env.objects:
        if obj.type.name != 'Shader':
            continue
        t = obj.read_typetree()
        if (t.get('m_ParsedForm') or {}).get('m_Name') == SHADER_NAME:
            target = (obj, t)
            break
    if not target:
        print(f'ABORT: {SHADER_NAME} not found.')
        return 1
    obj, t = target
    print(f'found {SHADER_NAME} (path_id {obj.path_id})')

    fixed = repaired_blob(decompress(t), args.flip_y)
    if args.flip_y:
        print('  flip-y: vertex uvs.y = (1.0 - in_TEXCOORD0.y) * ratio')
    comp = lz4.block.compress(fixed, store_size=False)
    t['compressedBlob'] = list(comp)
    t['compressedLengths'] = [[len(comp)]]
    t['decompressedLengths'] = [[len(fixed)]]
    obj.data = bytearray(obj.save_typetree(t))
    print(f'  repaired: {len(fixed):,} bytes ({len(comp):,} compressed), table OK')

    out_dir = pathlib.Path('/tmp/opencode/oesblit-out')
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env.save(pack='original', out_path=str(out_dir) + '/')
    saved_bundle = next(p for p in out_dir.iterdir() if p.is_file())
    print(f'saved bundle: {saved_bundle.name} ({saved_bundle.stat().st_size:,} bytes)')

    # ---- verify --------------------------------------------------------
    venv = UnityPy.load(str(saved_bundle))
    for vobj in venv.objects:
        if vobj.type.name == 'Shader' and vobj.path_id == obj.path_id:
            raw = decompress(vobj.read_typetree())
            break
    else:
        print('ABORT: shader vanished from the saved bundle.')
        return 1
    if raw != fixed or not table_ok(raw):
        print('ABORT: saved shader 6 does not read back as written.')
        return 1
    for off, length, _seg in table(raw):
        start, n = source_span(raw[off:off + length])
        src = raw[off + start:off + start + n]
        if UNIFORM_RX.search(src) or src.count(b'uniform highp int ') != 4 or src.count(EXT_LINE) != 1 or \
                src.count(UV_Y_FLIPPED if args.flip_y else UV_Y_LINE) != 1:
            print('ABORT: verify of the GLSL text failed.')
            return 1
    print(f'verify: shader 6 re-read clean ({len(table(raw))} subprograms, offsets/lengths unchanged).')

    with zipfile.ZipFile(args.dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in members:
            blob = saved_bundle.read_bytes() if info.filename == BUNDLE_MEMBER \
                else non_bundle[info.filename]
            zout.writestr(info, blob)
    work.unlink()
    print(f'wrote {args.dst}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
