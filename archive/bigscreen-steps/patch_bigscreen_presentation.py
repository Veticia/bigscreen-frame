#!/usr/bin/env python3
"""Patch #11: room/presentation screens drawn in-scene (ToggleOverlay forced to false), replacing patch #6.

Why: the environment's main screen is a PresentationScreen
(PresentationOverlayController: sectionRenderers, one OVROverlay, UnderlayCutout,
UnderlayMask). Patch #6 made `ToggleOverlay` a bare `ret`, which freezes the
prefab state: UnderlayCutout/UnderlayMask active (a see-through hole for a Quest
underlay) and nothing behind it, so the main screen is black/transparent
(tested on 19screen; a side screen fed by BigMediaRenderer works).

`ToggleOverlay(bool enableOverlay)` with false is Bigscreen's own non-Oculus
state: overlays disabled, section renderers enabled, cutout and mask inactive,
underlay global vectors zeroed. The sections then render with the material
PresentationSection.FormatTo* assigns (BigscreenVR/Unlit/Desktop, shader 327:
samplerExternalOES _YPlane/_UVPlane, STEREO_MULTIVIEW_ON variant), so local
video (SetTexture) and peer desktop YUV (SetRemoteTexture) can both display.

Patch: restore the original 4-word prologue at 0xbfd1c4 (undoing patch #6) and
replace `mov w20, w1` at 0xbfd1f0 with `mov w20, wzr` (argument ignored, always
false). Callers: Screens.Update, SetScreenFormat, OnPresentationChanged,
environment changes, capture mode.

Expected input: an APK carrying patch #6 (8ovr lineage, e.g. 19screen).

Usage:
    python3 tools/patch_bigscreen_presentation.py <in.apk> <out.apk>
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
TOGGLE = 0xBFD1C4
PATCH6 = [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F]
ORIGINAL = [0xD102C3FF, 0x6D0423E9, 0xA9056FFC, 0xA90667FA]
TAIL = [0xA9075FF8, 0xA90857F6, 0xA9094FF4, 0xA90A7BFD, 0x910283FD, 0xB0012A15, 0x394D02A8, 0x2A0103F4]
ARG_SITE = 0xBFD1F0
ARG_FALSE = 0x2A1F03F4  # mov w20, wzr
EXPECTED_ASM = ['sub sp, sp, #0xb0', 'stp d9, d8, [sp, #0x40]', 'stp x28, x27, [sp, #0x50]',
                'stp x26, x25, [sp, #0x60]', 'stp x24, x23, [sp, #0x70]', 'stp x22, x21, [sp, #0x80]',
                'stp x20, x19, [sp, #0x90]', 'stp x29, x30, [sp, #0xa0]', 'add x29, sp, #0xa0',
                'adrp x21, #0x313e000', 'ldrb w8, [x21, #0x340]', 'mov w20, wzr']


def words(data, addr, n):
    return list(struct.unpack_from(f'<{n}I', data, addr))


def check(data) -> bool:
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    got = [f'{i.mnemonic} {i.op_str}' for i in md.disasm(bytes(data[TOGGLE:TOGGLE + 48]), TOGGLE)]
    for g, e in zip(got, EXPECTED_ASM):
        if g != e:
            print(f'  MISMATCH: got {g!r}, expected {e!r}')
    return got == EXPECTED_ASM


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_presentation.py <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    with zipfile.ZipFile(src) as zsrc:
        lib_name = next(n for n in zsrc.namelist() if n.endswith(LIB_SUFFIX))
        lib = bytearray(zsrc.read(lib_name))

    if words(lib, TOGGLE, 4) != PATCH6 or words(lib, TOGGLE + 16, len(TAIL)) != TAIL:
        print(f'ABORT: ToggleOverlay @0x{TOGGLE:x} does not carry patch #6 over the expected body '
              f'({[hex(w) for w in words(lib, TOGGLE, 12)]}).')
        return 1
    print('fingerprint OK: ToggleOverlay carries patch #6; original body intact.')
    for i, w in enumerate(ORIGINAL):
        struct.pack_into('<I', lib, TOGGLE + 4 * i, w)
    struct.pack_into('<I', lib, ARG_SITE, ARG_FALSE)
    if not check(lib):
        print('ABORT: patched ToggleOverlay disassembly mismatch.')
        return 1
    print('patched: ToggleOverlay prologue restored, enableOverlay forced to false (disassembly verified).')

    with zipfile.ZipFile(src) as zsrc, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zsrc.infolist():
            zout.writestr(info, bytes(lib) if info.filename == lib_name else zsrc.read(info.filename))
    with zipfile.ZipFile(dst) as zin:
        if not check(bytearray(zin.read(lib_name))):
            print('ABORT: output verification failed.')
            return 1
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
