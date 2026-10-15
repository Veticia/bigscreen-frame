#!/usr/bin/env python3
"""Patch BigScreen's Meta-side entitlement check to report success immediately.

Surgical, fingerprint-verified rewrite of three instructions in libil2cpp.so.
No server bypass, no account code touched: the ent-check callback is never
invoked on the Frame (no Horizon account services), so we short-circuit the
outgoing request and call the app's own success handler. Bigscreen's own
account login is still required, exactly like on PC.

Usage:
    python3 tools/patch_bigscreen.py <input.apk> <output.apk>
"""

import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

PATCH_ADDRESS = 0x1080594

# Fingerprint: three words that MUST be present or we abort.
EXPECTED = [
    (0x1080594, 0xAA1F03E0),
    (0x1080598, 0x97FFDE76),
    (0x108059C, None),  # read + record the actual value for this hash check below
]
NEXT_WORD = 0x108059C
NEXT_EXPECTED_BL_TARGET = 0x1079F50

# The three instructions to write:
#   mov x0, x19    (this)
#   mov w1, #1     (result = true)
#   b   0x108068C  (HandleEntitlementCheckResult)
REPLACEMENT = [0xAA1303E0, 0x52800021, 0x1400003C]

# Patch #2: force BIG_APP_LOADER.get_RequiresOculusPlatform() -> false.
# That diverts the login flow to the PC (non-Oculus) branch: no Meta platform
# init, Bigscreen's own account flow instead. One call site in the whole
# binary (<CheckInternetConnection>d__11 MoveNext @ 0xdae5dc).
PATCH2_ADDRESS = 0xb895cc
PATCH2_EXPECTED = [0xF81E0FF3, 0xA9017BFD]  # str x19,[sp,#-0x20]! ; stp x29,x30,[sp,#0x10]
PATCH2_REPLACEMENT = [0x52800000, 0xD65F03C0]  # mov w0,#0 ; ret

# Patch #3: make all three GetRoomCodeFromClipboard() variants no-ops.
# Lepton has no clipboard service ("No service published for: clipboard"),
# so reading the room code from the clipboard on opening the room screens
# throws NPE -> JNI GetStringUTFChars -> CheckJNI abort, killing the process.
# nop-op = just ret at method entry; invite codes can still be typed by hand.
# Each entry: (address, fingerprint words at that address, class label)
# (fingerprints below read from THIS build's actual bytes.)
PATCH3 = [
    (0xbc85c4, [0xA9BE4FF4, 0xA9017BFD], 'HomePage$$GetRoomCodeFromClipboard'),
    (0xe3f778, [0xF81D0FF5, 0xA9014FF4], 'JoinRoom$$GetRoomCodeFromClipboard'),
    (0xd20078, [0xF81D0FF5, 0xA9014FF4], 'SocialPage$$GetRoomCodeFromClipboard'),
]
PATCH3_REPLACEMENT = [0xD65F03C0, 0xD503201F]  # ret ; nop

# Patch #4: skip the YUV texture binding on peer/desktop screens.
# MonitorViewController.SetLocalDesktopTexture(Texture[]) @0xd42598: iterates the
# 3 YUV planes and Material.SetTexture(_YPlane/_UVPlane/…). Shaders without a
# _YPlane property → Unity keeps the material unbound → screen renders black
# while the decoder keeps producing frames. A safe no-op: return immediately;
# the (already-created) desktop texture surface path takes over rendering.
PATCH4 = [
    (0xd42598, [0xA9BB67FA, 0xA9015FF8, 0xA90257F6, 0xA9034FF4],
     'MonitorViewController$$SetLocalDesktopTexture'),
    (0xd42790, [0xF81A0FFB, 0xA90167FA, 0xA9025FF8, 0xA90357F6],
     'MonitorViewController$$PeerDesktopTextureCreated'),
    (0xbfe350, [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD],
     'PresentationSection$$SetRemoteTexture'),
    (0xbfe184, [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD],
     'PresentationSection$$SetTexture'),
    (0xdfdecc, [0xA9BA6FFC, 0xA90167FA, 0xA9025FF8, 0xA90357F6],
     'MonitorRenderer$$SetTexture'),
    (0xdf44a4, [0xA9BD57F6, 0xA9014FF4, 0xA9027BFD, 0x910083FD],
     'MirroredScreen$$GetDesktopTexture'),
    (0xcdee28, [0xA9BD57F6, 0xA9014FF4, 0xA9027BFD],
     'Bigscreen.Screens$$OnPeerDesktopTextureCreated'),
    (0xcdec04, [0xA9BD57F6, 0xA9014FF4, 0xA9027BFD],
     'Bigscreen.Screens$$OnLocalDesktopTextureCreated'),
]

# Reverted in this build (ret'ing these broke room lighting / environment):
#   Screens$$AssignLocalDesktopTextures          (feeds room screen assignment)
#   BigLightingController$$DownsampleDesktopTexture / DownsampleBigMediaPlayerTexture
#     (feeds the room's ambient lighting from the desktop texture — killing it
#      made room textures render dark, not just peers)
#   MirroredScreen$$SetAsDesktopTexture          (int property id setter)
# They are NOT YUV-material binders, so the patch doesn't help there. Leave them
# their original code.
# Fingerprint is the actual 4-word prologue of the function in THIS build.
PATCH4_EXPECTED = [0xA9BB67FA, 0xA9015FF8, 0xA90257F6, 0xA9034FF4]
PATCH4_REPLACEMENT = [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F]  # ret ; nop ×3


def main() -> int:
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != '--skip-yuv-bypass'):
        print('usage: patch_bigscreen.py <input.apk> <output.apk> [--skip-yuv-bypass]')
        return 2
    skip_yuv = '--skip-yuv-bypass' in sys.argv
    if skip_yuv:
        # With the shader fixed (patch_bigscreen_shader.py), the YUV binds WORK.
        # Leave the binder functions in their original form so they actually bind
        # the texture, instead of being ret'ed (which is only needed when the
        # shader lacks _YPlane and the bind would crash/warn).
        global PATCH4
        PATCH4 = []
        print('note: --skip-yuv-bypass active; binder no-ops will NOT be applied.')
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])

    with zipfile.ZipFile(src) as zin:
        member_il2cpp = next(n for n in zin.namelist()
                             if n.endswith('lib/arm64-v8a/libil2cpp.so'))
        data = bytearray(zin.read(member_il2cpp))

    # ---- verify the fingerprint before touching anything ---------------
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    for addr, expected in EXPECTED:
        if expected is None:
            continue
        actual = struct.unpack_from('<I', data, addr)[0]
        if actual != expected:
            print(f'ABORT: libil2cpp.so@0x{addr:x} is 0x{actual:08x}, expected 0x{expected:08x} '
                  '- not the build this patch was written against.')
            return 1
    # confirm the next word is a BL to expected target
    nxt = struct.unpack_from('<I', data, NEXT_WORD)[0]
    simm26 = nxt & 0x03FFFFFF
    simm = simm26 - (1 << 26) if (simm26 >> 25) & 1 else simm26
    if (nxt & 0xFC000000) != 0x94000000 or addr + (simm << 2) != NEXT_EXPECTED_BL_TARGET:
        print(f'ABORT: following instruction not as expected.')
        return 1
    print(f'fingerprint OK: expected 3-word site at 0x{addr:x} matches this build exactly.')

    # ---- verify patch #2 site ----------------------------------------
    got2 = list(struct.unpack_from('<2I', data, PATCH2_ADDRESS))
    if got2 != PATCH2_EXPECTED:
        print(f'ABORT: patch #2 site @0x{PATCH2_ADDRESS:x} is {[hex(w) for w in got2]}, '
              f'expected {[hex(w) for w in PATCH2_EXPECTED]} - not this build.')
        return 1
    print(f'fingerprint OK: patch #2 site @0x{PATCH2_ADDRESS:x} matches this build exactly.')

    # ---- verify patch #3 sites ---------------------------------------
    for addr, expected, label in PATCH3:
        got3 = list(struct.unpack_from('<2I', data, addr))
        if got3 != expected:
            print(f'ABORT: patch #3 site {label} @0x{addr:x} is '
                  f'{[hex(w) for w in got3]}, expected '
                  f'{[hex(w) for w in expected]} - not this build.')
            return 1
        print(f'fingerprint OK: {label} @0x{addr:x} matches.')

    for addr, expected, label in PATCH4:
        n = len(expected)
        got4 = list(struct.unpack_from(f'<{n}I', data, addr))
        if got4 != expected:
            print(f'ABORT: patch #4 site {label} @0x{addr:x} is '
                  f'{[hex(w) for w in got4]}, expected '
                  f'{[hex(w) for w in expected]} - not this build.')
            return 1
        print(f'fingerprint OK: {label} @0x{addr:x} matches.')

    # ---- write patch #1 ----------------------------------------------
    for i, word in enumerate(REPLACEMENT):
        struct.pack_into('<I', data, PATCH_ADDRESS + i*4, word)
    print('patched:')

    # ---- write patch #2 ----------------------------------------------
    for i, word in enumerate(PATCH2_REPLACEMENT):
        struct.pack_into('<I', data, PATCH2_ADDRESS + i*4, word)
    print('patch2 (RequiresOculusPlatform->false):')

    # ---- write patch #3 ----------------------------------------------
    for addr, _expected, label in PATCH3:
        struct.pack_into('<I', data, addr, PATCH3_REPLACEMENT[0])     # ret
        struct.pack_into('<I', data, addr + 4, PATCH3_REPLACEMENT[1]) # nop
        print(f'patch3 (no-op): {label} @0x{addr:x}')

    # ---- write patch #4 ----------------------------------------------
    for addr, _expected, label in PATCH4:
        struct.pack_into('<I', data, addr, PATCH4_REPLACEMENT[0])      # ret
        struct.pack_into('<I', data, addr + 4,  PATCH4_REPLACEMENT[1]) # nop
        struct.pack_into('<I', data, addr + 8,  PATCH4_REPLACEMENT[1]) # nop
        if len(PATCH4_REPLACEMENT) > 3:
            struct.pack_into('<I', data, addr + 12, PATCH4_REPLACEMENT[3])  # nop
        print(f'patch4 (YUV-bind skipped): {label} @0x{addr:x}')

    print('\npost-patch disassembly (site + neighbourhood):')
    for insn in md.disasm(data[PATCH_ADDRESS-8: PATCH_ADDRESS+28], PATCH_ADDRESS-8):
        print(f'  0x{insn.address:x}: {insn.mnemonic} {insn.op_str}')
    print('\npatch #2 site:')
    for insn in md.disasm(data[PATCH2_ADDRESS: PATCH2_ADDRESS+12], PATCH2_ADDRESS):
        print(f'  0x{insn.address:x}: {insn.mnemonic} {insn.op_str}')
    print('\npatch #3 sites:')
    for addr, _expected, label in PATCH3:
        for insn in md.disasm(data[addr:addr+8], addr):
            print(f'  0x{insn.address:x}: {insn.mnemonic} {insn.op_str}')
        print(f'    ({label})')
    print('\npatch #4 sites:')
    for addr, _expected, label in PATCH4:
        n = 4 if len(PATCH4_REPLACEMENT) > 3 else 3
        for insn in md.disasm(data[addr:addr+n*4], addr):
            print(f'  0x{insn.address:x}: {insn.mnemonic} {insn.op_str}')
        print(f'    ({label})')

    # ---- re-pack ------------------------------------------------------
    # Copy every entry by NAME, substituting the patched libil2cpp.so.
    # Verifying afterwards that the output really contains the new bytes is
    # part of the patcher, not a nice-to-have.
    with zipfile.ZipFile(src) as zsrc:
        members = zsrc.infolist()
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in members:
                blob = data if info.filename == member_il2cpp else zsrc.read(info.filename)
                zout.writestr(info, blob)

    # ---- self-verify --------------------------------------------------
    with zipfile.ZipFile(dst) as zout:
        patched = zout.read(member_il2cpp)
    words = struct.unpack_from('<3I', patched, PATCH_ADDRESS)
    words2 = struct.unpack_from('<2I', patched, PATCH2_ADDRESS)
    ok3 = all(
        list(struct.unpack_from('<2I', patched, addr)) == PATCH3_REPLACEMENT
        for addr, _e, _l in PATCH3
    )
    ok4 = all(
        list(struct.unpack_from('<4I' if len(PATCH4_REPLACEMENT) > 3 else '<3I',
                                patched, addr))
            == PATCH4_REPLACEMENT
        for addr, _e, _l in PATCH4
    )
    if list(words) != REPLACEMENT or list(words2) != PATCH2_REPLACEMENT or not ok3 or not ok4:
        print(f'ABORT: output {dst} does NOT contain the patches. Deleting it.')
        dst.unlink(missing_ok=True)
        return 1
    print(f'verified: output {dst} contains all patches '
          f'(1: ent-check, 2: requires-oculus-false, 3: clipboard no-op, 4: YUV-bind skip)')
    print(f'  site1 @0x{PATCH_ADDRESS:x}: {[hex(w) for w in words]}')
    print(f'  site2 @0x{PATCH2_ADDRESS:x}: {[hex(w) for w in words2]}')
    for addr, _e, label in PATCH3:
        w3 = struct.unpack_from('<2I', patched, addr)
        print(f'  site3 {label:45s} {[hex(w) for w in w3]}')
    for addr, _e, label in PATCH4:
        n = len(PATCH4_REPLACEMENT)
        w4 = struct.unpack_from(f'<{n}I', patched, addr)
        nm = 'site4-YUV' if 'PeerDesktop' not in label and 'OnPeer' not in label and 'OnLocal' not in label else 'site5-screens'
        print(f'  {nm}  {label[:42]:42s} {[hex(w) for w in w4]}')
    print(f'wrote {dst}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
