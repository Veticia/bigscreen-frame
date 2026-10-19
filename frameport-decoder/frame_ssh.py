"""SSH to a FramePort-paired Steam Frame with FramePort's own key (Linux paths)."""

import json
import pathlib
import subprocess

FRAMEPORT_DATA = pathlib.Path.home() / '.local/share/frameport'
CODEC_STORE = '/home/steamos/.local/share/frameport/video-codec'


def default_target() -> str:
    """user@host of the first Frame FramePort knows (frames.json), else steamos@steamdeck."""
    try:
        frame = json.loads((FRAMEPORT_DATA / 'frames.json').read_text())[0]
        return f'{frame.get("user", "steamos")}@{frame["host"]}'
    except (OSError, ValueError, KeyError, IndexError):
        return 'steamos@steamdeck'


def options(key: pathlib.Path | None) -> list[str]:
    keys = [key] if key else [FRAMEPORT_DATA / 'ssh' / name for name in ('id_ed25519', 'id_rsa')]
    opts = ['-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'PubkeyAcceptedAlgorithms=+ssh-rsa',
            '-o', 'ConnectTimeout=10']
    known = FRAMEPORT_DATA / 'ssh' / 'known_hosts'
    if known.exists():
        opts += ['-o', f'UserKnownHostsFile={known}']
    for k in keys:
        if k.exists():
            opts += ['-i', str(k)]
    return opts


def run(target: str, key, script: str) -> subprocess.CompletedProcess:
    """Run a shell script on the Frame (stdin), returning its output."""
    return subprocess.run(['ssh', *options(key), target, 'bash -s'], input=script, text=True, capture_output=True)


def copy_to(target: str, key, local: pathlib.Path, remote: str) -> None:
    subprocess.run(['scp', '-q', *options(key), str(local), f'{target}:{remote}'], check=True)


def copy_from(target: str, key, remote: str, local: pathlib.Path) -> None:
    ssh = ' '.join(['ssh', *options(key)])
    subprocess.run(['rsync', '-a', '-e', ssh, f'{target}:{remote}', str(local)], check=True)
