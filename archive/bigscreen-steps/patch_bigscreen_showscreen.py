#!/usr/bin/env python3
"""Patch #14: keep BigMediaRenderer's in-scene main screen active while content is shown.

Why: BigContentController.Reset and HideScreen deactivate `regularScreen` (the
main-screen mesh); ShowScreen only reactivates `regularOverlay`, because on
Quest the overlay showed the picture and the mesh stayed hidden. Media that
goes through Reset/HideScreen -> ShowScreen (local TV channels) therefore left
the main screen black / missing, while YouTube (StartExternalMediaSource, no
reset) kept the mesh's default active state (tested on 22tv: TV plays, the
regular texture updates, main screen still black).

Patch: the patch #9 stub in UpdateRegularOverlay (0x149fd1c, which only runs
while regularOverlay's GameObject is active) also calls
`BigMediaRenderer.regularScreen.SetActive(true)` (field +0x20) after disabling
the OVROverlay component. HideScreen/Reset still hide the screen: they also
deactivate regularOverlay, which stops the stub.

Expected input: an APK carrying the patch #9 stub (patch_bigscreen_meshscreen),
e.g. 22tv.

Usage:
    python3 tools/patch_bigscreen_showscreen.py <in.apk> <out.apk>
"""

import pathlib
import struct
import sys
import zipfile

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
except ImportError:
    sys.exit('capstone is required:  pip install capstone')

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import patch_bigscreen_meshscreen as meshscreen  # noqa: E402

BASE = meshscreen.UPDATE_REGULAR_OVERLAY
GAMEOBJECT_SET_ACTIVE = 0x186E980
SET_ACTIVE_PROLOGUE = [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD]
OLD_LEN = len(meshscreen.EXPECTED_ASM)


def stub_v2() -> list[int]:
    v1 = meshscreen.stub_words(BASE)
    head = v1[:13]                      # prologue .. bl Behaviour$$set_enabled
    extra = [
        0xF9401260,                     # ldr x0, [x19, #0x20]   regularScreen
        0xB4000080,                     # cbz x0, +0x10
        0x52800021,                     # mov w1, #1
        0xAA1F03E2,                     # mov x2, xzr
        None,                           # bl GameObject$$SetActive
    ]
    tail = v1[13:-1]                    # restore args, epilogue
    words = head + extra + tail + [None]
    words[17] = meshscreen.b_imm(BASE + 17 * 4, GAMEOBJECT_SET_ACTIVE, True)
    words[-1] = meshscreen.b_imm(BASE + (len(words) - 1) * 4, meshscreen.UPDATE_REGULAR_SCREEN, False)
    return words


def expected_v2() -> list[str]:
    e = meshscreen.EXPECTED_ASM
    return (e[:13] + ['ldr x0, [x19, #0x20]', f'cbz x0, #{BASE + 18 * 4:#x}', 'mov w1, #1', 'mov x2, xzr',
                      f'bl #{GAMEOBJECT_SET_ACTIVE:#x}'] + e[13:])


def disasm(data, n):
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    return [f'{i.mnemonic} {i.op_str}' for i in md.disasm(bytes(data[BASE:BASE + 4 * n]), BASE)]


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_showscreen.py <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    with zipfile.ZipFile(src) as zsrc:
        lib_name = next(n for n in zsrc.namelist() if n.endswith(meshscreen.LIB_SUFFIX))
        lib = bytearray(zsrc.read(lib_name))
    if not meshscreen.check_stub(lib):
        print('ABORT: input does not carry the patch #9 stub.')
        return 1
    if meshscreen.words(lib, GAMEOBJECT_SET_ACTIVE, 4) != SET_ACTIVE_PROLOGUE:
        print('ABORT: GameObject$$SetActive prologue mismatch.')
        return 1
    print('fingerprint OK: patch #9 stub present; GameObject$$SetActive @0x186e980.')
    words = stub_v2()
    for i, w in enumerate(words):
        struct.pack_into('<I', lib, BASE + 4 * i, w)
    exp = expected_v2()
    got = disasm(lib, len(exp))
    if got != exp:
        for g, e in zip(got, exp):
            if g != e:
                print(f'  MISMATCH: got {g!r}, expected {e!r}')
        print('ABORT: stub v2 disassembly mismatch.')
        return 1
    print(f'patched: stub v2 ({len(words)} instructions) also activates regularScreen (disassembly verified).')
    with zipfile.ZipFile(src) as zsrc, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zsrc.infolist():
            zout.writestr(info, bytes(lib) if info.filename == lib_name else zsrc.read(info.filename))
    with zipfile.ZipFile(dst) as zin:
        if disasm(bytearray(zin.read(lib_name)), len(exp)) != exp:
            print('ABORT: output verification failed.')
            return 1
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
