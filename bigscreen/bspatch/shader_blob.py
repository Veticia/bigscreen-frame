"""Unity 2020.3 GLES shader blobs: container helpers and the two GLSL rewrites.

A Shader's compressedBlob decompresses (LZ4 block) to

    u32 count; count x (u32 offset, u32 length, u32 segment); subprograms...

and every subprogram keeps its GLSL source behind a u32 length prefix. Edits
here are length-preserving per subprogram, so offsets, length prefixes and
alignment stay exactly as Unity wrote them. (Growing the text without fixing
the table makes Unity log "Failed to load GpuProgram from binary shader data"
and drop the program.)
"""

import re
import struct

import lz4.block

SUBPROGRAM_MAGIC = b'\x3c\x51\x07\x0c'


def decompress(tree: dict) -> bytes:
    blob = bytes(bytearray(tree['compressedBlob']))
    clens, dlens = tree['compressedLengths'], tree['decompressedLengths']
    if len(clens) != 1 or len(clens[0]) != 1:
        raise SystemExit(f'ABORT: unexpected shader blob layout {clens}')
    return lz4.block.decompress(blob[:clens[0][0]], uncompressed_size=dlens[0][0])


def store(tree: dict, raw: bytes) -> None:
    comp = lz4.block.compress(raw, store_size=False)
    tree['compressedBlob'] = list(comp)
    tree['compressedLengths'] = [[len(comp)]]
    tree['decompressedLengths'] = [[len(raw)]]


def table(raw: bytes) -> list[tuple[int, int, int]]:
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


def map_sources(raw: bytes, fn) -> bytes:
    """Apply fn(glsl) -> glsl (same length) to every subprogram's source."""
    if not table_ok(raw):
        raise SystemExit('ABORT: shader blob table is not intact')
    out = bytearray(raw[:4 + 12 * len(table(raw))])
    for off, length, _seg in table(raw):
        sub = raw[off:off + length]
        v = sub.find(b'#version')
        n = struct.unpack_from('<I', sub, v - 4)[0]
        src = sub[v:v + n]
        new = fn(src)
        if len(new) != len(src):
            raise SystemExit('ABORT: GLSL rewrite changed the source length')
        out += sub[:v] + new + sub[v + n:]
    out = bytes(out)
    if len(out) != len(raw) or not table_ok(out):
        raise SystemExit('ABORT: rewritten blob failed the table check')
    return out


def sources(raw: bytes) -> list[bytes]:
    res = []
    for off, length, _seg in table(raw):
        sub = raw[off:off + length]
        v = sub.find(b'#version')
        res.append(sub[v:v + struct.unpack_from('<I', sub, v - 4)[0]])
    return res


# --------------------------------------------------------------------------- OESBlit (shader 6)

EXT_LINE = b'#extension GL_OES_EGL_image_external : enable\r\n'            # 48 bytes
EXT3_LINE = b'#extension GL_OES_EGL_image_external_essl3 : enable\r\n'
OESBLIT_PRELUDE = b'#version 300 es\n' + EXT_LINE + EXT3_LINE + EXT_LINE + EXT3_LINE
OESBLIT_INT_RX = re.compile(rb'uniform   int (_linearToSrgb|_premultiply);')
UV_Y = b'uvs.y = in_TEXCOORD0.y * _frameToTextureHeightRatio;'
UV_Y_FLIPPED = b'uvs.y = (1.0 - in_TEXCOORD0.y) * _frameToTextureHeightRatio;'


def fix_oesblit(src: bytes) -> bytes:
    """Matching `highp int` uniform precision (Mesa refuses the mismatch) + vertical flip.

    Bytes come from the duplicated third prelude line (an exact repeat of the first)."""
    if not src.startswith(OESBLIT_PRELUDE) or len(OESBLIT_INT_RX.findall(src)) != 4 or src.count(UV_Y) != 1:
        raise SystemExit('ABORT: OESBlit source is not the expected original')
    new = OESBLIT_INT_RX.sub(rb'uniform highp int \1;', src).replace(UV_Y, UV_Y_FLIPPED)
    grow = len(new) - len(src)                                  # 4 x 4 + 8 = 24
    third = len(b'#version 300 es\n' + EXT_LINE + EXT3_LINE)
    blank = b'//' + b' ' * (len(EXT_LINE) - grow - 4) + b'\r\n'
    return new[:third] + blank + new[third + len(EXT_LINE):]


# --------------------------------------------------------------------------- BSVR_Texture3D_OES (shader 825 -> 319)

OU_BRANCH = b'_3dModeBigMedia == 2) { // OU'
UO_BRANCH = b'_3dModeBigMedia == 4) { // UO'


def fix_texture3d_ou(src: bytes) -> bytes:
    """Swap the over-under and under-over branches: the shader treats texture v 0.5-1 as the top half, but raw
    samplerExternalOES coordinates from a video decoder run top-down (v 0 = first row), so OU showed each eye
    the other eye's picture. Same length (digits and comments trade places)."""
    if src.count(OU_BRANCH) != 1 or src.count(UO_BRANCH) != 1 or b'\0' in src:
        raise SystemExit('ABORT: BSVR_Texture3D_OES source is not the expected original')
    return src.replace(OU_BRANCH, b'\0').replace(UO_BRANCH, OU_BRANCH).replace(b'\0', UO_BRANCH)


# --------------------------------------------------------------------------- Desktop (shader 327)

MP_UV = b'uvs = in_TEXCOORD0.xy;'
MP_UV_NEW = (b'uvs = in_TEXCOORD0.xy;float e=float(unity_StereoEyeIndex);'
             b'if(_3dMode==1||_3dMode==3)uvs.x=uvs.x*.5+.5*(_3dMode==1?e:1.-e);'
             b'else if(_3dMode>1)uvs.y=uvs.y*.5+.5*(_3dMode==4?e:1.-e);')
MV_UV = b'vs_TEXCOORD0.y = vs_TEXCOORD0.y;'
MV_UV_NEW = (b'float e=float(gl_ViewID_OVR);'
             b'if(_3dMode==1||_3dMode==3)vs_TEXCOORD0.x=vs_TEXCOORD0.x*.5+.5*(_3dMode==1?e:1.-e);'
             b'else if(_3dMode>1)vs_TEXCOORD0.y=vs_TEXCOORD0.y*.5+.5*(_3dMode==4?e:1.-e);')
MV_DECL_AT = b'layout(num_views = 2) in;'
MP_POS = b'          in highp vec3 in_POSITION0;'          # 2nd occurrence = multi-pass vertex stage
MP_DECL = b'          uniform int _3dMode;uniform int unity_StereoEyeIndex;' + MP_POS[10:]
DESKTOP_COMMENT_RX = re.compile(rb'//////////+ (?:SINGLE|MULTI) PASS STEREO|// To account for color data loss[^\r\n]*')
_PAD = b'\x00PAD\x00'


def fix_desktop_stereo(src: bytes) -> bytes:
    """Per-eye half of SBS / over-under frames by the global _3dMode (0 flat, 1 SBS, 2 OU, 3 SBS-RL, 4 UO).

    Room for the code comes from the banner comments, the rest is padded with spaces."""
    for needle, count in ((MP_UV, 1), (MV_UV, 1), (MV_DECL_AT, 1), (MP_POS, 2), (b'_3dMode', 0),
                          (b'unity_StereoEyeIndex', 0)):
        if src.count(needle) != count:
            raise SystemExit(f'ABORT: Desktop shader: {needle!r} occurs {src.count(needle)}x, expected {count}')
    if len(DESKTOP_COMMENT_RX.findall(src)) != 6:
        raise SystemExit('ABORT: Desktop shader: removable comments not found')
    new = src.replace(MP_UV, MP_UV_NEW).replace(MV_UV, MV_UV_NEW).replace(MV_DECL_AT, b'uniform int _3dMode;' + MV_DECL_AT)
    second = new.find(MP_POS, new.find(MP_POS) + 1)
    new = new[:second] + MP_DECL + new[second + len(MP_POS):]
    removed = 0

    def drop(_m):
        nonlocal removed
        removed += 1
        return _PAD if removed == 1 else b''
    new = DESKTOP_COMMENT_RX.sub(drop, new)
    deficit = len(src) - (len(new) - len(_PAD))
    if deficit < 0:
        raise SystemExit(f'ABORT: Desktop shader edit is {-deficit} bytes too long')
    return new.replace(_PAD, b' ' * deficit)
