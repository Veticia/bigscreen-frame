#!/usr/bin/env python3
"""Inspect GLES shaders inside a Unity APK / data.unity3d.

    unity_shaders.py app.apk list [--grep OES]           # file#pid, name, blob table state, multiview
    unity_shaders.py app.apk dump sharedassets0.assets#327 OUTDIR   # GLSL of every subprogram
    unity_shaders.py app.apk check                       # blobs whose subprogram table is inconsistent
    unity_shaders.py app.apk grep _3dMode unity_StereoEyeIndex      # shaders mentioning any word

A table that does not add up is what makes Unity log "Failed to load GpuProgram
from binary shader data" (see bigscreen/bspatch/shader_blob.py).
"""

import argparse
import pathlib
import struct
import sys
import zipfile

import lz4.block

BUNDLE = 'assets/bin/Data/data.unity3d'
MAGIC = b'\x3c\x51\x07\x0c'


def load(path: pathlib.Path):
    import UnityPy
    return UnityPy.load(zipfile.ZipFile(path).read(BUNDLE) if path.suffix == '.apk' else str(path))


def blobs(tree) -> list[bytes]:
    blob = bytes(bytearray(tree.get('compressedBlob') or b''))
    out, off = [], 0
    for cl, dl in zip(tree.get('compressedLengths') or [], tree.get('decompressedLengths') or []):
        for c, d in zip(cl, dl):
            out.append(lz4.block.decompress(blob[off:off + c], uncompressed_size=d))
            off += c
    return out


def table_state(raw: bytes) -> str:
    n = struct.unpack_from('<I', raw, 0)[0]
    pos = 4 + 12 * n
    for i in range(n):
        o, l, _ = struct.unpack_from('<III', raw, 4 + 12 * i)
        if o != pos or raw[o:o + 4] != MAGIC:
            return 'BROKEN'
        pos = o + l
    return 'ok' if pos == len(raw) else 'BROKEN'


def sources(raw: bytes) -> list[bytes]:
    n = struct.unpack_from('<I', raw, 0)[0]
    res = []
    for i in range(n):
        o, l, _ = struct.unpack_from('<III', raw, 4 + 12 * i)
        sub = raw[o:o + l]
        v = sub.find(b'#version')
        if v >= 4:
            res.append(sub[v:v + struct.unpack_from('<I', sub, v - 4)[0]])
    return res


def shaders(env):
    for o in env.objects:
        if o.type.name != 'Shader':
            continue
        try:
            t = o.read_typetree()
        except Exception:
            continue
        yield f'{o.assets_file.name}#{o.path_id}', (t.get('m_ParsedForm') or {}).get('m_Name'), t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('bundle', type=pathlib.Path, help='APK or data.unity3d')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list'); p.add_argument('--grep', default='')
    p = sub.add_parser('dump'); p.add_argument('shader'); p.add_argument('outdir', type=pathlib.Path)
    sub.add_parser('check')
    p = sub.add_parser('grep'); p.add_argument('words', nargs='+')
    args = ap.parse_args()
    env = load(args.bundle)

    for key, name, t in shaders(env):
        try:
            raws = blobs(t)
        except Exception as e:
            raws = []
            if args.cmd == 'check':
                print(f'{key} {name}: LZ4 error {e}')
        single = len(raws) == 1
        if args.cmd == 'list' and args.grep.lower() in (name or '').lower():
            state = table_state(raws[0]) if single else f'{len(raws)} chunks'
            mv = any(b'GL_OVR_multiview' in r for r in raws)
            print(f'{key:32} {name}  [{state}]{"  GL_OVR_multiview code" if mv else ""}')
        elif args.cmd == 'check' and single and table_state(raws[0]) != 'ok':
            print(f'{key} {name}: subprogram table BROKEN')
        elif args.cmd == 'grep':
            hits = [w for w in args.words if any(w.encode() in r for r in raws)]
            if hits:
                print(f'{key:32} {name}  {hits}')
        elif args.cmd == 'dump' and key == args.shader:
            args.outdir.mkdir(parents=True, exist_ok=True)
            for ci, raw in enumerate(raws):
                for si, src in enumerate(sources(raw)):
                    f = args.outdir / f'{key.replace("#", "_")}_{ci}_{si}.glsl'
                    f.write_bytes(src)
                    print(f'wrote {f} ({len(src)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
