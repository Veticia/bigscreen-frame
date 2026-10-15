#!/usr/bin/env python3
"""Patch #13: play "protected" media (TV channels) on the regular in-scene screen, not an Android-surface overlay.

Why: BigContentController.isProtectedExternalEnabled (+0xD0, not serialized) is
set to true by the constructor. For a protected media session (local TV
channels) StartProtectedMediaSession then hands the video to
protectedExternalOverlay (OVROverlay external surface) and ShowScreen activates
that overlay instead of regularOverlay. On the Frame the external-surface layer
never shows (the overlay chain is disabled by patches #9/#11 anyway), so the
main screen stays black / see-through while mirrors and ambient light show the
picture (tested on 21stereo: bedroom CRT black, airplane main screen missing).

With the flag false, Bigscreen's own other branches run:
StartProtectedMediaSession -> BigMediaPlayer.StartProtectedMediaSession (texture
playback), ShowScreen/HideScreen/ForceNonOverlayScreen -> regularOverlay, which
keeps the patch #9 in-scene screen updating.

Patch: BigContentController$$.ctor @0xb98fd0 `strb w8, [x19, #0xd0]` ->
`strb wzr, [x19, #0xd0]`.

Usage:
    python3 tools/patch_bigscreen_noprotected.py <in.apk> <out.apk>
"""

import pathlib
import struct
import sys
import zipfile

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
except ImportError:
    sys.exit('capstone is required:  pip install capstone')

LIB_SUFFIX = 'lib/arm64-v8a/libil2cpp.so'
SITE = 0xB98FD0
CONTEXT = 0xB98FCC
ORIG = [0x320003E8, 0x39034268]   # mov w8, #1 (orr form) ; strb w8, [x19, #0xd0]
NEW_STORE = 0x3903427F            # strb wzr, [x19, #0xd0]


def asm_at(data, addr):
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    ins = next(md.disasm(bytes(data[addr:addr + 4]), addr))
    return f'{ins.mnemonic} {ins.op_str}'


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_noprotected.py <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    with zipfile.ZipFile(src) as zsrc:
        lib_name = next(n for n in zsrc.namelist() if n.endswith(LIB_SUFFIX))
        lib = bytearray(zsrc.read(lib_name))
    got = list(struct.unpack_from('<2I', lib, CONTEXT))
    if got != ORIG:
        print(f'ABORT: BigContentController$$.ctor @0x{CONTEXT:x} is {[hex(w) for w in got]}.')
        return 1
    print('fingerprint OK: BigContentController$$.ctor isProtectedExternalEnabled = true.')
    struct.pack_into('<I', lib, SITE, NEW_STORE)
    if asm_at(lib, SITE) != 'strb wzr, [x19, #0xd0]':
        print('ABORT: disassembly mismatch.')
        return 1
    print('patched: isProtectedExternalEnabled initialised to false.')
    with zipfile.ZipFile(src) as zsrc, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zsrc.infolist():
            zout.writestr(info, bytes(lib) if info.filename == lib_name else zsrc.read(info.filename))
    with zipfile.ZipFile(dst) as zin:
        if asm_at(bytearray(zin.read(lib_name)), SITE) != 'strb wzr, [x19, #0xd0]':
            print('ABORT: output verification failed.')
            return 1
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
