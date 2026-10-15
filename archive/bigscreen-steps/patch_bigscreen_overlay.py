#!/usr/bin/env python3
"""Patch #6: disable the OVR overlay content path (PresentationOverlayController).

Evidence chain (from the 3fix/4tex sessions):
  - Bigscreen drives ALL on-screen content for peers/screens through
    `PresentationOverlayController$$ToggleOverlay` -> `OVROverlay$$EnableYUV`
    -> the OVRPlugin CompositorOpenXR_GLES quad layer (xrCreateSwapchain 1920x1088
    etc.). That path never receives usable pixel content on the Frame
    (black peer/public-room screens).
  - The mesh/Unity-material path (PresentationSection + PresentationScreen /
    BlankPresentationScreen, using the OES-sampling Desktop shader) is proven
    reachable: the ambient-light downsampler reads real pixel data from the same
    OES textures and the room glow follows content.
  - `ToggleOverlay` has exactly ONE caller
    (`Screens$$UpdatePresentationOculusOverlayForFormat`, itself fed per-frame by
    `Screens$$Update`). No other code path activates the overlay.

Patching the entry of `ToggleOverlay` to return immediately keeps Bigscreen off
the OVR compositor layer route and forces the app onto the mesh/material route
that we have reason to believe can actually render the content here.

Expected input: a 4tex-style build (patches #1-#3 + texture-parameter guard).
Refuses to touch anything else.

Usage:
    python3 tools/patch_bigscreen_overlay.py <in.apk> <out.apk>
"""

import struct
import sys
import zipfile
from pathlib import Path

TOGGLE_OVERLAY = 0xBFD1C4
PROLOGUE = [0xD102C3FF, 0x6D0423E9, 0xA9056FFC, 0xA90667FA, 0xA9075FF8, 0xA90857F6]
REPLACEMENT = [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F]  # ret; nop x3

GUARDS = [
    (0x1080594, [0xAA1303E0, 0x52800021, 0x1400003C], 'patch1 EntitlementCheck$$Start'),
    (0xB895CC, [0x52800000, 0xD65F03C0], 'patch2 RequiresOculusPlatform->false'),
    (0xBC85C4, [0xD65F03C0, 0xD503201F], 'patch3 GetRoomCodeFromClipboard no-op'),
    (0x1E25EFC, [0xD65F03C0, 0xD503201F], 'texparams set_wrapMode no-op'),
]


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_overlay.py <in.apk> <out.apk>')
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.exists():
        print(f'ABORT: input APK {src} does not exist.')
        return 1

    with zipfile.ZipFile(src) as zin:
        member = next(n for n in zin.namelist() if n.endswith('lib/arm64-v8a/libil2cpp.so'))
        data = bytearray(zin.read(member))

    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', data, addr))
        if got != expected:
            print(f'ABORT: {label} @0x{addr:x} does not match a 4tex-style build '
                  f'({[hex(w) for w in got]}).')
            return 1
    print('guard OK: input carries patches #1-#3 + texture parameter guard.')

    got = list(struct.unpack_from('<6I', data, TOGGLE_OVERLAY))
    if got != PROLOGUE:
        print(f'ABORT: ToggleOverlay @0x{TOGGLE_OVERLAY:x} prologue is '
              f'{[hex(w) for w in got]}, expected {[hex(w) for w in PROLOGUE]}.')
        return 1
    print('fingerprint OK: PresentationOverlayController$$ToggleOverlay @0x1bfd1c4.')

    for i, word in enumerate(REPLACEMENT):
        struct.pack_into('<I', data, TOGGLE_OVERLAY + i * 4, word)
    print(f'patched: ToggleOverlay @0x{TOGGLE_OVERLAY:x} -> ret (OVR overlay path disabled)')

    with zipfile.ZipFile(src) as zsrc:
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in zsrc.infolist():
                blob = data if info.filename == member else zsrc.read(info.filename)
                zout.writestr(info, blob)

    with zipfile.ZipFile(dst) as zin:
        backs = zin.read(member)
    got = list(struct.unpack_from('<4I', backs, TOGGLE_OVERLAY))
    if got != REPLACEMENT:
        print('ABORT: output verification failed for patch #6.')
        return 1
    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', backs, addr))
        if got != expected:
            print(f'ABORT: output does not keep {label} intact.')
            return 1
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    print('  ToggleOverlay:', ' '.join(hex(w) for w in struct.unpack_from('<4I', backs, TOGGLE_OVERLAY)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
