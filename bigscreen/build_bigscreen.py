#!/usr/bin/env python3
"""Build a Steam Frame-ready Bigscreen Beta APK from the original Quest APK.

    python3 bigscreen/build_bigscreen.py <original.apk> <output.apk>

Applies every patch in bspatch/ (code + assets) in one pass, verifies each one,
and writes a new APK. Install the result with FramePort ("Add games" / "Newer
APK"); FramePort re-signs it. Only the exact supported build is accepted (see
--help); anything else aborts before a byte is changed.
"""

import argparse
import pathlib
import sys
import tempfile
import time
import zipfile
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bspatch import PACKAGE, SUPPORTED_APK_SHA256, SUPPORTED_VERSION, apk, asset_patches, code_patches  # noqa: E402

try:
    import UnityPy
except ImportError:
    sys.exit('Missing dependencies. Install them with:  pip install -r requirements.txt')


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def crc32_file(path: pathlib.Path) -> int:
    crc = 0
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            crc = zlib.crc32(chunk, crc)
    return crc


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[0],
        epilog=f'Supported input: {PACKAGE} {SUPPORTED_VERSION}, sha256 {SUPPORTED_APK_SHA256}')
    ap.add_argument('original', type=pathlib.Path, help='original (unmodified) Bigscreen Beta APK')
    ap.add_argument('output', type=pathlib.Path, help='where to write the patched APK')
    ap.add_argument('--tmp', type=pathlib.Path, default=None,
                    help='directory for temporary files (needs ~2.5 GB free; default: system temp)')
    ap.add_argument('--skip-hash-check', action='store_true',
                    help="don't require the exact supported APK hash (fingerprints are still checked)")
    args = ap.parse_args()

    if not args.original.is_file():
        print(f'error: {args.original} not found')
        return 2
    if args.output.resolve() == args.original.resolve():
        print('error: output must differ from the input')
        return 2

    log(f'checking {args.original.name} ...')
    digest = apk.sha256(args.original)
    if digest != SUPPORTED_APK_SHA256:
        msg = (f'input sha256 is {digest}\n  supported:      {SUPPORTED_APK_SHA256}\n'
               f'  ({PACKAGE} {SUPPORTED_VERSION}). Patches target exact code addresses; another build will not work.')
        if not args.skip_hash_check:
            print('error: ' + msg)
            return 1
        print('warning: ' + msg)

    # ---- code ----------------------------------------------------------------
    lib = bytearray(apk.read_member(args.original, apk.LIB_IL2CPP))
    code_patches.apply(lib)
    log(f'code: {len(code_patches.PATCHES)} patches applied and verified')

    with tempfile.TemporaryDirectory(prefix='bigscreen-frame-', dir=args.tmp) as tmp:
        tmp = pathlib.Path(tmp)
        bundle_in = tmp / 'data.unity3d'
        out_dir = tmp / 'out'
        out_dir.mkdir()

        # ---- assets ----------------------------------------------------------
        log('assets: loading data.unity3d (about 1 GB, takes a few minutes) ...')
        apk.extract_member(args.original, apk.BUNDLE, bundle_in)
        env = UnityPy.load(str(bundle_in))
        asset_patches.apply(env)
        log(f'assets: {len(asset_patches.ALL)} patches applied, saving ...')
        env.save(pack='original', out_path=str(out_dir) + '/')
        del env
        bundle_out = next(p for p in out_dir.iterdir() if p.is_file())
        bundle_in.unlink()

        log('assets: re-reading the saved bundle to verify ...')
        problems = asset_patches.verify(UnityPy.load(str(bundle_out)))
        if problems:
            print('ABORT: saved bundle does not verify: ' + ', '.join(problems))
            return 1

        # ---- APK -------------------------------------------------------------
        log(f'writing {args.output} ...')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        partial = args.output.with_name(args.output.name + '.partial')
        apk.rewrite(args.original, partial, {apk.LIB_IL2CPP: bytes(lib), apk.BUNDLE: bundle_out})
        bundle_crc = crc32_file(bundle_out)

    with zipfile.ZipFile(partial) as z:
        bad = z.testzip()
        ok_code = not code_patches.check_patched(z.read(apk.LIB_IL2CPP))
        ok_bundle = z.getinfo(apk.BUNDLE).CRC == bundle_crc
    if bad or not ok_code or not ok_bundle:
        print(f'ABORT: output verification failed (zip={bad}, code={ok_code}, bundle={ok_bundle}); kept {partial}')
        return 1
    partial.replace(args.output)
    log(f'done: {args.output} ({args.output.stat().st_size:,} bytes)')
    print(f'sha256 {apk.sha256(args.output)}')
    print('Next: install it on the Frame with FramePort (see bigscreen/README.md).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
