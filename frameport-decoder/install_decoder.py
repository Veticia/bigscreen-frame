#!/usr/bin/env python3
"""Install a decoder built by build_decoder.py on the Steam Frame, or roll back.

    python3 frameport-decoder/install_decoder.py work/decoder
    python3 frameport-decoder/install_decoder.py --rollback

FramePort keeps its shared video decoder on the Frame in
~/.local/share/frameport/video-codec/versions/<sha256 of manifest.json>, with
`current` pointing at the one in use. This adds the new build as another
version (a copy of the current one with the plugin and manifest replaced),
remembers the previous target in `current.before-main10` and switches
`current`. Lepton must not be running (games load the plugin at start).

FramePort keeps an installed decoder of the same or a newer revision, so it
won't replace this build (revision 9) with an older one; after a FramePort
update, check whether it ships revision 9 or newer itself.
"""

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import frame_ssh  # noqa: E402

STORE = frame_ssh.CODEC_STORE
BACKUP = f'{STORE}/current.before-main10'
NOT_RUNNING = ('if pgrep -f "Lepton/lepton start" >/dev/null; then\n'
               '    echo "error: Lepton is running; quit the game first"; exit 3\nfi\n')


def remote(target, key, script: str) -> int:
    result = frame_ssh.run(target, key, 'set -e\n' + script)
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    return result.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('build', type=pathlib.Path, nargs='?', help='output folder of build_decoder.py')
    ap.add_argument('--rollback', action='store_true', help='point `current` back to the version it had before')
    ap.add_argument('--frame', default=None, help='user@host of the Frame (default: from FramePort)')
    ap.add_argument('--key', type=pathlib.Path, default=None, help='SSH key (default: FramePort\'s keys)')
    args = ap.parse_args()
    target = args.frame or frame_ssh.default_target()

    if args.rollback:
        return remote(target, args.key, NOT_RUNNING + f'''
test -f {BACKUP} || {{ echo "error: no {BACKUP}: nothing to roll back"; exit 1; }}
old=$(cat {BACKUP}); test -d {STORE}/$old
ln -sfn "$old" {STORE}/current.new && mv -T {STORE}/current.new {STORE}/current && rm {BACKUP}
echo "current -> $(readlink {STORE}/current)"
''')

    if args.build is None:
        ap.error('give the build folder or --rollback')
    lib, manifest = args.build / 'libstagefrighthw.so', args.build / 'manifest.json'
    if not lib.is_file() or not manifest.is_file():
        print(f'error: {args.build} has no libstagefrighthw.so / manifest.json')
        return 2
    info = json.loads(manifest.read_text())
    if info['files']['libstagefrighthw.so'] != hashlib.sha256(lib.read_bytes()).hexdigest():
        print('error: libstagefrighthw.so does not match its manifest')
        return 1
    version = hashlib.sha256(manifest.read_bytes()).hexdigest()

    frame_ssh.copy_to(target, args.key, lib, '/tmp/frameport-main10.so')
    frame_ssh.copy_to(target, args.key, manifest, '/tmp/frameport-main10.manifest.json')
    return remote(target, args.key, NOT_RUNNING + f'''
trap 'rm -f /tmp/frameport-main10.so /tmp/frameport-main10.manifest.json' EXIT
old=$(readlink {STORE}/current)
if [ "$old" = "versions/{version}" ]; then echo "already installed: $old"; exit 0; fi
test -d "{STORE}/$old"
grep -q '"runtime_sha256": "{info["runtime_sha256"]}"' "{STORE}/$old/manifest.json" ||
    {{ echo "error: the Frame's decoder targets another Lepton runtime; rebuild for it"; exit 1; }}
new={STORE}/versions/{version}
rm -rf "$new.tmp"; cp -a "{STORE}/$old" "$new.tmp"
install -m 644 /tmp/frameport-main10.so "$new.tmp/libstagefrighthw.so"
install -m 644 /tmp/frameport-main10.manifest.json "$new.tmp/manifest.json"
rm -rf "$new"; mv "$new.tmp" "$new"
test -f {BACKUP} || echo "$old" > {BACKUP}
ln -sfn versions/{version} {STORE}/current.new && mv -T {STORE}/current.new {STORE}/current
echo "current -> $(readlink {STORE}/current) (was $old; undo with --rollback)"
''')


if __name__ == '__main__':
    sys.exit(main())
