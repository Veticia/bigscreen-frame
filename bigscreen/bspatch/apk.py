"""Read and rewrite APK members while keeping every other entry (order, metadata, compression) as is."""

import hashlib
import pathlib
import zipfile

LIB_IL2CPP = 'lib/arm64-v8a/libil2cpp.so'
BUNDLE = 'assets/bin/Data/data.unity3d'


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read_member(apk: pathlib.Path, name: str) -> bytes:
    with zipfile.ZipFile(apk) as z:
        return z.read(name)


def extract_member(apk: pathlib.Path, name: str, dst: pathlib.Path) -> None:
    with zipfile.ZipFile(apk) as z, z.open(name) as src, open(dst, 'wb') as out:
        while chunk := src.read(1 << 20):
            out.write(chunk)


def rewrite(src: pathlib.Path, dst: pathlib.Path, replacements: dict[str, pathlib.Path | bytes]) -> None:
    """Copy `src` to `dst`, replacing the named members (bytes, or a file to stream from)."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        missing = set(replacements) - set(zin.namelist())
        if missing:
            raise SystemExit(f'ABORT: {src} has no member(s) {sorted(missing)}')
        for info in zin.infolist():
            new = replacements.get(info.filename)
            if new is None:
                zout.writestr(info, zin.read(info.filename))
            elif isinstance(new, (bytes, bytearray)):
                zout.writestr(info, bytes(new))
            else:
                with zout.open(info, 'w') as out, open(new, 'rb') as f:
                    while chunk := f.read(1 << 20):
                        out.write(chunk)
