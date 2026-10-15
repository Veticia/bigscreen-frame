#!/usr/bin/env python3
"""Patch #5: no-op Texture.set_wrapMode / set_filterMode (external-texture guard).

Why: the 3fix session log shows exactly two `OPENGL NATIVE PLUG-IN ERROR:
GL_INVALID_VALUE` events, and both land at the birth of an external OES texture
wrapper (browser texture init, then the peer-desktop video layer). One of
Bigscreen's own churn steps after `Texture2D.CreateExternalTexture` is:
Texture.set_wrapMode(tex, Clamp). This Unity engine build is the one where
Unity started applying texture parameters to externally created textures
(upstream issue 1358700). On Qualcomm's driver the parameter call against an
OES texture was tolerated (silent no-op); on Mesa it raises GL_INVALID_VALUE
and the texture wrapper ends up not rendering - samplerExternalOES code then
samples literally nothing, which matches "gray browser / black YUV planes"
while ambient-light downsampling still sees the real content.

Neutralizing the two setters is safe overall: legit textures simply keep the
default CLAMP/bilinear state that Unity textures have anyway; the only
regression possible is slightly different filtering/wrapping on UI images.

Expected input: a 3fix-style build (code patches #1-#3 already applied).
The tool verifies those fingerprints first and refuses to touch anything else.

Usage:
    python3 tools/patch_bigscreen_texparams.py <in.apk> <out.apk>
"""

import struct
import sys
import zipfile
from pathlib import Path

TEX_SET_WRAP = 0x1E25EFC
TEX_SET_FILTER = 0x1E25F8C

# Prologue actually present in this build's libil2cpp.so (str	x21,[sp,#-0x30]!;
# stp x20,x19; stp x29,x30; add x29,sp,#0x20).
PROLOGUE = [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD]
REPLACEMENT = [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F]  # ret; nop; nop; nop

# Fingerprints of the already-applied 3fix code patches (must be present).
GUARDS = [
    (0x1080594, [0xAA1303E0, 0x52800021, 0x1400003C], 'patch1 EntitlementCheck$$Start'),
    (0xB895CC, [0x52800000, 0xD65F03C0], 'patch2 RequiresOculusPlatform->false'),
    (0xBC85C4, [0xD65F03C0, 0xD503201F], 'patch3 GetRoomCodeFromClipboard no-op'),
]

SITES = [
    (TEX_SET_WRAP, 'Texture.set_wrapMode'),
    (TEX_SET_FILTER, 'Texture.set_filterMode'),
]


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_texparams.py <in.apk> <out.apk>')
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.exists():
        print(f'ABORT: input APK {src} does not exist.')
        return 1

    with zipfile.ZipFile(src) as zin:
        member = next(n for n in zin.namelist() if n.endswith('lib/arm64-v8a/libil2cpp.so'))
        data = bytearray(zin.read(member))

    # ---- guard: input must already carry the earlier code patches -------
    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', data, addr))
        if got != expected:
            print(f'ABORT: {label} @0x{addr:x} does not match a 3fix build '
                  f'({[hex(w) for w in got]}). Run patch_bigscreen.py '
                  '--skip-yuv-bypass first.')
            return 1
    print('guard OK: input carries patches #1-#3.')

    # ---- fingerprint the two texture-parameter sites ---------------------
    for addr, label in SITES:
        got = list(struct.unpack_from('<4I', data, addr))
        if got != PROLOGUE:
            print(f'ABORT: {label} @0x{addr:x} prologue is '
                  f'{[hex(w) for w in got]}, expected '
                  f'{[hex(w) for w in PROLOGUE]} - not this engine build.')
            return 1
        print(f'fingerprint OK: {label} @0x{addr:x}.')

    # ---- write the no-ops ------------------------------------------------
    for addr, label in SITES:
        for i, word in enumerate(REPLACEMENT):
            struct.pack_into('<I', data, addr + i * 4, word)
        print(f'patched: {label} @0x{addr:x} -> ret')

    # ---- re-pack ---------------------------------------------------------
    with zipfile.ZipFile(src) as zsrc:
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in zsrc.infolist():
                blob = data if info.filename == member else zsrc.read(info.filename)
                zout.writestr(info, blob)

    # ---- verify the output by re-reading it ------------------------------
    with zipfile.ZipFile(dst) as zin:
        backs = zin.read(member)
    for addr, label in SITES:
        got = list(struct.unpack_from('<4I', backs, addr))
        if got != REPLACEMENT:
            print(f'ABORT: output verification failed for {label}.')
            return 1
    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', backs, addr))
        if got != expected:
            print(f'ABORT: output does not keep {label} intact.')
            return 1
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')

    # Show final instruction bytes at both sites.
    for addr, label in SITES:
        words = struct.unpack_from('<4I', backs, addr)
        print(f'  {label}: ' + ' '.join(hex(w) for w in words))
    return 0


if __name__ == '__main__':
    sys.exit(main())
