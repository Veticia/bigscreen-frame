#!/usr/bin/env python3
"""Compare two APKs of the same Unity app: zip members, libil2cpp.so word runs, and every data.unity3d object.

    apk_diff.py original.apk patched.apk            # members + code + assets
    apk_diff.py a.apk b.apk --no-assets             # skip the (slow) bundle comparison

Used to prove that a build changes exactly what it should, and that the
one-command build reproduces a tested build object for object.
"""

import argparse
import hashlib
import pathlib
import struct
import sys
import zipfile

LIB = 'lib/arm64-v8a/libil2cpp.so'
BUNDLE = 'assets/bin/Data/data.unity3d'


def word_runs(a: bytes, b: bytes):
    run = None
    for off in range(0, min(len(a), len(b)) - 3, 4):
        if a[off:off + 4] != b[off:off + 4]:
            if run and off == run[1] + 4:
                run[1] = off
            else:
                if run:
                    yield run
                run = [off, off]
    if run:
        yield run


def objects(z: zipfile.ZipFile) -> dict:
    import UnityPy
    env = UnityPy.load(z.read(BUNDLE))
    return {(o.assets_file.name, o.path_id): (o.type.name, hashlib.sha1(o.get_raw_data()).hexdigest())
            for o in env.objects}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('a', type=pathlib.Path)
    ap.add_argument('b', type=pathlib.Path)
    ap.add_argument('--no-assets', action='store_true')
    args = ap.parse_args()
    za, zb = zipfile.ZipFile(args.a), zipfile.ZipFile(args.b)

    names_a, names_b = za.namelist(), zb.namelist()
    print('member list identical:', names_a == names_b)
    changed = [n for n in names_a if n in names_b and za.getinfo(n).CRC != zb.getinfo(n).CRC]
    print('members with different content:', changed or 'none')
    for n in sorted(set(names_a) ^ set(names_b)):
        print('  only in', 'a' if n in names_a else 'b', n)

    if LIB in changed:
        la, lb = za.read(LIB), zb.read(LIB)
        runs = list(word_runs(la, lb))
        print(f'{LIB}: {sum((e - s) // 4 + 1 for s, e in runs)} words in {len(runs)} runs')
        for s, e in runs:
            n = (e - s) // 4 + 1
            print(f'  {s:#x} x{n}: ' + ' '.join(f'{w:08x}' for w in struct.unpack_from(f'<{min(n, 6)}I', lb, s))
                  + (' ...' if n > 6 else ''))

    if BUNDLE in changed and not args.no_assets:
        oa, ob = objects(za), objects(zb)
        print(f'{BUNDLE}: {len(oa)} / {len(ob)} objects')
        for k in sorted(set(oa) | set(ob)):
            if oa.get(k) != ob.get(k):
                print(f'  DIFF {k[0]}#{k[1]} {(oa.get(k) or ob.get(k))[0]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
