#!/usr/bin/env python3
"""Patch #7: disable BigMediaRenderer's personal-screen OVR overlay updates.

Why: the personal tablet/table surface (e.g. the browser window) renders its
content as an OVRPlugin openGL quad layer via
`BigMediaRenderer$$UpdateRegularOverlay`:

    Dictionary<IntPtr, Texture2D> wrap-cache
    -> Texture2D$$CreateExternalTexture(nativeTex)
    -> OVROverlay$$OverrideOverlayTextureInfo
    -> OVROverlay$$SwitchTo2D/SBS/OU + BigLightingController$$SetGISampleTextureFormat

On the Frame (Mesa/Zink) the external OES wrapper never carries pixel data, so
this path displays gray content. This is a separate flow from
`PresentationOverlayController$$ToggleOverlay` (patch #6), so both need to be
neutralized for the app to fall back to any Unity-rendered route.

Patch: return immediately at the entry of `UpdateRegularOverlay` (ret).

Expected input: an 8ovr-style build (patches #1-#3 + texture-parameter guard +
patch #6).
"""

import struct
import sys
import zipfile
from pathlib import Path

UPDATE_REGULAR_OVERLAY = 0x149FD1C
PROLOGUE = [0xD10183FF, 0xA90167FA, 0xA9025FF8, 0xA90357F6, 0xA9044FF4, 0xA9057BFD, 0x910143FD]
REPLACEMENT = [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F]  # ret; nop x3

GUARDS = [
    (0x1080594, [0xAA1303E0, 0x52800021, 0x1400003C], 'patch1 EntitlementCheck$$Start'),
    (0xB895CC, [0x52800000, 0xD65F03C0], 'patch2 RequiresOculusPlatform->false'),
    (0xBC85C4, [0xD65F03C0, 0xD503201F], 'patch3 GetRoomCodeFromClipboard no-op'),
    (0x1E25EFC, [0xD65F03C0, 0xD503201F], 'texparams set_wrapMode no-op'),
    (0xBFD1C4, [0xD65F03C0, 0xD503201F], 'patch6 ToggleOverlay no-op'),
]


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_regularoverlay.py <in.apk> <out.apk>')
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
            print(f'ABORT: {label} @0x{addr:x} does not match an 8ovr-style build '
                  f'({[hex(w) for w in got]}).')
            return 1
    print('guard OK: input carries patches #1-#3, texparams, patch #6.')

    got = list(struct.unpack_from('<7I', data, UPDATE_REGULAR_OVERLAY))
    if got != PROLOGUE:
        print(f'ABORT: UpdateRegularOverlay @0x{UPDATE_REGULAR_OVERLAY:x} prologue is '
              f'{[hex(w) for w in got]}, expected {[hex(w) for w in PROLOGUE]}.')
        return 1
    print('fingerprint OK: BigMediaRenderer$$UpdateRegularOverlay @0x149fd1c.')

    for i, word in enumerate(REPLACEMENT):
        struct.pack_into('<I', data, UPDATE_REGULAR_OVERLAY + i * 4, word)
    print(f'patched: UpdateRegularOverlay @0x{UPDATE_REGULAR_OVERLAY:x} -> ret '
          '(personal-screen OVR overlay updates disabled)')

    with zipfile.ZipFile(src) as zsrc:
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in zsrc.infolist():
                blob = data if info.filename == member else zsrc.read(info.filename)
                zout.writestr(info, blob)

    with zipfile.ZipFile(dst) as zin:
        backs = zin.read(member)
    got = list(struct.unpack_from('<4I', backs, UPDATE_REGULAR_OVERLAY))
    if got != REPLACEMENT:
        print('ABORT: output verification failed for patch #7.')
        return 1
    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', backs, addr))
        if got != expected:
            print(f'ABORT: output does not keep {label} intact.')
            return 1
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    print('  UpdateRegularOverlay:', ' '.join(hex(w) for w in struct.unpack_from('<4I', backs, UPDATE_REGULAR_OVERLAY)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
