#!/usr/bin/env python3
"""Fix unqualified uniform precision in Bigscreen's shaders (Mesa/Zink link fix).

Bigscreen's hand-written shaders declare some uniforms with no precision
qualifier:

    uniform   int _linearToSrgb;
    uniform   int _premultiply;

GLSL ES gives `int` a different default precision per stage (highp in vertex
shaders, mediump in fragment shaders). Declaring the same uniform unqualified in
both stages therefore produces mismatching declarations, and Mesa/Zink refuses
to link the program:

    GLSL link error: error: declarations for uniform `_linearToSrgb` have
    mismatching precision qualifiers

Qualcomm's driver accepted it, so this never mattered on a Quest. Unity's
fallback when a shader fails to link is its error shader, which renders magenta.
This hits BigscreenVR/Unlit/OESBlit (external-texture blits used for browser and
video surfaces) and BigscreenVR/Unlit/Desktop, among others.

Fix: give the uniforms an explicit, matching precision in every occurrence
(mediump is supported for int in both stages on GLSL ES 3.0).

Usage:
    python3 tools/patch_bigscreen_shader_precision.py <in.apk> <out.apk>
"""

import argparse
import pathlib
import re
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

BUNDLE_MEMBER = 'assets/bin/Data/data.unity3d'

# Only the unqualified forms are touched. `highp` is the right target: it is the
# vertex-shader default for int, and fragment `highp int` is already used
# elsewhere in this build (shaders 327/796/817 state "precision highp int;"), and
# GLSL ES 3.00 requires highp support in both stages.
UNIFORM_FIXES = [
    (re.compile(rb'uniform(\s+)int(\s+)_linearToSrgb;'),
     rb'uniform\1highp int\2_linearToSrgb;'),
    (re.compile(rb'uniform(\s+)int(\s+)_premultiply;'),
     rb'uniform\1highp int\2_premultiply;'),
]


def decompress_shader(t) -> bytes | None:
    """Decompress a Unity Shader object's compressedBlob (LZ4 block chunks)."""
    clens = t.get('compressedLengths') or []
    dlens = t.get('decompressedLengths') or []
    blob = bytes(bytearray(t.get('compressedBlob') or b''))
    if not blob or not clens or not dlens:
        return None
    out = bytearray()
    off = 0
    for c_size, d_size in zip(clens[0], dlens[0]):
        chunk = blob[off:off + c_size]
        off += c_size
        try:
            out += lz4.block.decompress(chunk, uncompressed_size=d_size)
        except Exception:
            return None
    return bytes(out)


def recompress_shader(text: bytes) -> tuple[bytes, int, int]:
    """Recompress as a single LZ4 block; returns (blob, compressed, decompressed)."""
    comp = lz4.block.compress(text, store_size=False)
    return comp, len(comp), len(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src', type=pathlib.Path)
    ap.add_argument('dst', type=pathlib.Path)
    args = ap.parse_args()

    with zipfile.ZipFile(args.src) as zsrc:
        members = zsrc.infolist()
        bundle_bytes = zsrc.read(BUNDLE_MEMBER)
        non_bundle = {i.filename: zsrc.read(i.filename)
                      for i in members if i.filename != BUNDLE_MEMBER}

    work = SCRATCH / 'precision-in.unity3d'
    work.write_bytes(bundle_bytes)
    env = UnityPy.load(str(work))

    fixed = []
    for obj in env.objects:
        if obj.type.name != 'Shader':
            continue
        t = obj.read_typetree()
        text = decompress_shader(t)
        if not text:
            continue

        # Split the source into its stage sections so we only rewrite a uniform
        # that is declared unqualified in MORE THAN ONE stage - that is the exact
        # condition Mesa rejects. A single-stage declaration is fine as long as
        # the stage's stated (or language-default) precision is consistent.
        s = text.decode('utf-8', errors='surrogateescape')
        vi = s.find('#ifdef VERTEX')
        fi = s.find('#ifdef FRAGMENT')
        vs = s[vi:fi] if vi >= 0 and fi > vi else ''
        fs = s[fi:] if fi >= 0 else ''

        total = 0
        new_text = text
        for name in (b'_linearToSrgb', b'_premultiply'):
            rx = re.compile(rb'uniform(\s+)int(\s+)' + name + rb';')
            if rx.search(vs.encode('utf-8', 'surrogateescape')) and \
               rx.search(fs.encode('utf-8', 'surrogateescape')):
                repl = rb'uniform\1highp int\2' + name + rb';'
                new_text, n = rx.subn(repl, new_text)
                total += n
        if not total:
            continue

        blob, c_len, d_len = recompress_shader(new_text)
        t['compressedBlob'] = list(blob)
        t['compressedLengths'] = [[c_len]]
        t['decompressedLengths'] = [[d_len]]
        if 'offsets' in t:
            t['offsets'] = [[0]]
        saved = obj.save_typetree(t)
        obj.data = bytearray(saved)
        fixed.append((obj.path_id, total, len(text), d_len))
        print(f'  shader {obj.path_id}: {total} uniform(s) qualified, '
              f'{len(text):,} -> {d_len:,} bytes ({c_len:,} compressed)')

    if not fixed:
        print('ABORT: no shader with unqualified uniforms found.')
        return 1

    out_dir = SCRATCH / 'precision-out'
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env.save(pack='original', out_path=str(out_dir) + '/')
    saved_bundle = next(p for p in out_dir.iterdir() if p.is_file())
    print(f'saved bundle: {saved_bundle.name} ({saved_bundle.stat().st_size:,} bytes)')

    # ---- verify --------------------------------------------------------
    venv = UnityPy.load(str(out_dir) + '/')
    checked = 0
    for obj in venv.objects:
        if obj.type.name != 'Shader' or obj.path_id not in [f[0] for f in fixed]:
            continue
        t = obj.read_typetree()
        text = decompress_shader(t)
        if text is None:
            print(f'ABORT: shader {obj.path_id} no longer decompresses.')
            return 1
        for rx, _ in UNIFORM_FIXES:
            if rx.search(text):
                print(f'ABORT: shader {obj.path_id} still has an '
                      f'unqualified uniform.')
                return 1
        checked += 1
    print(f'verify: {checked} shader(s) re-read clean (no unqualified uniforms).')
    if checked != len(fixed):
        print(f'ABORT: verified {checked} of {len(fixed)} shaders.')
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