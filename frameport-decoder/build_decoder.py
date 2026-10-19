#!/usr/bin/env python3
"""Build FramePort's video decoder plugin with the 10-bit HEVC patch.

    python3 frameport-decoder/build_decoder.py --ndk ~/android-ndk-r27c --out work/decoder

Fetches FramePort v1.2.1 from GitHub, checks the exact commit, applies
0001-hevc-decode-Main-10-in-software.patch and runs FramePort's own builder
(native/hevc/build.py: FFmpeg 7.1.1 + the plugin, Android NDK r27c). Lepton's
Android libraries to link against are copied from the Frame over SSH, or taken
from --lepton-root. The builder is deterministic: the result is compared with
the build tested on the headset. Install it with install_decoder.py.
"""

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import frame_ssh  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
PATCH = HERE / '0001-hevc-decode-Main-10-in-software.patch'
FRAMEPORT_REPO = 'https://github.com/spoopyghosty0/frameport.git'
FRAMEPORT_TAG = 'v1.2.1'
FRAMEPORT_COMMIT = 'f0eb7850fbc7d21a25478bb49020d68ecd0a271b'
# libstagefrighthw.so: FramePort 1.2.1 (revision 8), and this patch (revision 9) as tested on the headset
ORIGINAL_SHA256 = '141de01f0d40b97d4f9a720db5ab8ea442f6aa35db5272048899292884994a1c'
PATCHED_SHA256 = '4fdc1a1f4f86a5146d1c27cca35734b374db5c0f64e000d36aae806658c672f5'
LEPTON_ROOTFS = '/home/steamos/.local/share/Steam/steamapps/common/Lepton/images/rootfs'


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def builder(source: pathlib.Path, ndk: pathlib.Path, rootfs: pathlib.Path) -> pathlib.Path:
    # FramePort's bundled Python can leak PYTHONHOME/PYTHONPATH into shells; the builder needs the system one
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONHOME', 'PYTHONPATH')}
    subprocess.run([sys.executable, 'native/hevc/build.py', '--ndk', str(ndk), '--lepton-root', str(rootfs)],
                   cwd=source, env=env, check=True)
    return source / 'artifacts/hevc/libstagefrighthw.so'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ndk', type=pathlib.Path, required=True, help='Android NDK r27c (27.2.12479018), Linux')
    ap.add_argument('--out', type=pathlib.Path, required=True, help='where to put the built decoder files')
    ap.add_argument('--lepton-root', type=pathlib.Path,
                    help='Lepton rootfs with system/lib64 and vendor/lib64 (default: copy them from the Frame)')
    ap.add_argument('--frame', default=None, help='user@host of the Frame (default: from FramePort)')
    ap.add_argument('--key', type=pathlib.Path, default=None, help='SSH key (default: FramePort\'s keys)')
    ap.add_argument('--verify-toolchain', action='store_true',
                    help='first rebuild the unpatched decoder and require FramePort\'s exact binary')
    args = ap.parse_args()
    if not PATCH.is_file():
        print(f'error: {PATCH} missing')
        return 2

    with tempfile.TemporaryDirectory(prefix='frameport-decoder-') as tmp:
        tmp = pathlib.Path(tmp)
        rootfs = args.lepton_root
        if rootfs is None:
            target = args.frame or frame_ssh.default_target()
            log(f'copying Lepton system/lib64 and vendor/lib64 from {target} (about 230 MB) ...')
            rootfs = tmp / 'rootfs'
            for part in ('system', 'vendor'):
                (rootfs / part).mkdir(parents=True)
                frame_ssh.copy_from(target, args.key, f'{LEPTON_ROOTFS}/{part}/lib64', rootfs / part)

        source = tmp / 'frameport'
        log(f'fetching FramePort {FRAMEPORT_TAG} ...')
        subprocess.run(['git', 'clone', '-q', '--depth', '1', '--branch', FRAMEPORT_TAG, FRAMEPORT_REPO, str(source)],
                       check=True)
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=source, capture_output=True, text=True,
                              check=True).stdout.strip()
        if head != FRAMEPORT_COMMIT:
            print(f'error: {FRAMEPORT_TAG} is {head}, expected {FRAMEPORT_COMMIT}')
            return 1

        if args.verify_toolchain:
            log('building the unpatched decoder (toolchain check) ...')
            got = sha256(builder(source, args.ndk, rootfs))
            if got != ORIGINAL_SHA256:
                print(f'error: unpatched build is {got}, FramePort ships {ORIGINAL_SHA256}: toolchain differs')
                return 1
            log('unpatched build matches FramePort\'s binary')
            subprocess.run(['git', 'checkout', '-q', '--', 'artifacts'], cwd=source, check=True)

        log(f'applying {PATCH.name} and building ...')
        subprocess.run(['git', 'apply', str(PATCH)], cwd=source, check=True)
        got = sha256(builder(source, args.ndk, rootfs))

        args.out.mkdir(parents=True, exist_ok=True)
        for item in (source / 'artifacts/hevc').iterdir():
            shutil.copy2(item, args.out / item.name)
    log(f'done: {args.out}')
    print(f'libstagefrighthw.so sha256 {got}')
    if got != PATCHED_SHA256:
        print(f'warning: differs from the build tested on the headset ({PATCHED_SHA256})')
    print('Next: python3 frameport-decoder/install_decoder.py ' + str(args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
