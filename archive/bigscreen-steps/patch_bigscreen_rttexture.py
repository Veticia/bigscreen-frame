#!/usr/bin/env python3
"""Patch #8: bind Bigscreen's personal-screen OVR overlay to a Unity RenderTexture.

Bigscreen renders the personal tablet (browser, movie) content for the OVR
compositor through:

    Dictionary<IntPtr, Texture2D> wrap-cache
    -> Texture2D$$CreateExternalTexture(nativeTex)
    -> OVROverlay$$OverrideOverlayTextureInfo

On the Frame the external OES wrapper is Unity-reviewable (proven: the ambient
downsampling path reads real pixels from the same wrapper) but the OVRPlugin
compositor layer path does not land content. This patch intercepts the wrap
cache-miss branch of `BigMediaRenderer$$UpdateRegularOverlay` (0x149fd1c) and,
after the original CreateExternalTexture call, builds a Unity-side RGBA32
RenderTarget, blits the OES wrapper into it (Graphics.Blit), reads the RT's
native pointer and binds OverrideOverlayTextureInfo to *the RT* instead of the
OES wrapper.

The compositor quad layer therefore binds a texture Unity fills every frame -
the same Blender mechanism that makes ambient light follow content - so the
display can finally show real content instead of gray.

Patch window: 0x14a0320..0x14a03e8  (the dict-set / ratio-write / overlay-bind
block after the CreateExternalTexture call). Helper:

    ldr  x8, [x20, #0x28]          ; x8 = OVROverlay object
    cbz  x8, exit
    mov  x0, x25                   ; width
    mov  x1, x22                   ; height
    mov  w2, wzr                   ; depthBuffer = 0
    mov  w3, #4                    ; format = RGBA32
    mov  x4, xzr                   ; methodInfo = null
    bl   RenderTexture$$GetTemporary
    cbz  x0, exit
    mov  x23, x0                   ; x23 = RT
    mov  x0, x24                   ; source = OES wrapper
    mov  x1, x23                   ; dest = RT
    mov  x2, xzr                   ; methodInfo = null
    bl   Graphics$$Blit(source,dest)
    mov  x0, x23                   ; RT
    mov  x1, xzr                   ; methodInfo = null
    bl   Texture$$GetNativeTexturePtr
    ldr  x8, [x20, #0x28]
    cbz  x8, exit
    mov  x1, x23                   ; texture = RT
    mov  x2, x0                    ; nativePtr of RT
    mov  w3, wzr                   ; node = 0
    mov  x0, x8                    ; overlay object
    mov  x4, xzr                   ; methodInfo = null
    bl   OVROverlay$$OverrideOverlayTextureInfo
    b    exit
exit (0x14a03e8): the original epilogue, untouched.

Expected input: an 8ovr-style build (patches #1-#3 + texparams + patch #6).
"""

import struct
import sys
import zipfile
from pathlib import Path

UPDATE_REGULAR_OVERLAY = 0x149FD1C
CREATE_EXT_CALL = 0x14A031C
HELPER_START = 0x14A0320
EPILOGUE = 0x14A03E8

FN_GETTEMPORARY = 0x164C814   # RenderTexture$$GetTemporary(w,h,depth,fmt)
FN_BLIT = 0x1870D00            # Graphics$$Blit(source, dest)
FN_GETNATIVEPTR = 0x1E26164    # Texture$$GetNativeTexturePtr
FN_OVERRIDE = 0x10AA6D4        # OVROverlay$$OverrideOverlayTextureInfo

PROLOGUE = [0xD10183FF, 0xA90167FA, 0xA9025FF8, 0xA90357F6, 0xA9044FF4, 0xA9057BFD, 0x910143FD]
# after CreateExternalTexture & before helper start
PRE_HELPER = [
    (0x14A0304, [0x321E03E2, 0x2A1903E0, 0x2A1603E1, 0xD2800003, 0xAA1F03E4, 0xAA1F03E5, 0xAA1F03E6]),
    (CREATE_EXT_CALL, None),  # verified dynamically below
]
EPILOGUE_CHECK = [0xA9457BFD, 0xA9444FF4, 0xA94357F6, 0xA9425FF8]

GUARDS = [
    (0x1080594, [0xAA1303E0, 0x52800021, 0x1400003C], 'patch1 EntitlementCheck$$Start'),
    (0xB895CC, [0x52800000, 0xD65F03C0], 'patch2 RequiresOculusPlatform->false'),
    (0xBC85C4, [0xD65F03C0, 0xD503201F], 'patch3 GetRoomCodeFromClipboard no-op'),
    (0x1E25EFC, [0xD65F03C0, 0xD503201F], 'texparams set_wrapMode no-op'),
    (0xBFD1C4, [0xD65F03C0, 0xD503201F], 'patch6 ToggleOverlay no-op'),
]


def enc_b(target, pc):
    imm = (target - pc) // 4
    assert -(1 << 25) <= imm < (1 << 25), f'b out of range {target:#x}->{pc:#x}'
    return (0b000101 << 26) | (imm & 0x3FFFFFF)

def enc_bl(target, pc):
    imm = (target - pc) // 4
    assert -(1 << 25) <= imm < (1 << 25), f'bl out of range {target:#x}->{pc:#x}'
    return (0b100101 << 26) | (imm & 0x3FFFFFF)

def enc_cbz(rt, target, pc, size64=True):
    imm = (target - pc) // 4
    assert -(1 << 18) <= imm < (1 << 18), f'cbz out of range'
    op = 0b10110100
    return (op << 24) | ((imm & 0x7FFFF) << 5) | rt

def enc_mov_x(rd, rn):
    """mov rd, rn (64-bit ORR alias) — same as enc_orr_x with rn pointing at the register."""
    return enc_orr_x(rd, rn)

def enc_orr_x(rd, rn):
    """mov rd, rn (64-bit): ORR rd, xzr, rn — verified base 0xAA000000."""
    return 0xAA000000 | (rn << 16) | (0x1F << 5) | rd

def enc_orr_w(rd, rn):
    """mov rd, rn (32-bit): ORR rd, wzr, rn — verified base 0x2A000000."""
    return 0x2A000000 | (rn << 16) | (0x1F << 5) | rd

def enc_ldr_x(rt, rn, imm12):
    assert 0 <= imm12 < 0x1000 and imm12 % 8 == 0, 'ldr x imm must be 8-byte aligned'
    return 0xF9400000 | ((imm12 // 8) << 10) | (rn << 5) | rt

def enc_movz_w(rd, imm16):
    assert 0 <= imm16 < 0x10000
    return 0x52800000 | (imm16 << 5) | rd

def build_helper():
    '''Return a list of (offset, word) covering the helper at HELPER_START.'''
    img = []

    def pc_of(i):
        return HELPER_START + 4 * i

    code = []
    # Helper layout (34 instructions, fits the 0x14a0320..0x14a03e8 window):
    # 0: mov x24, x0        (stash fresh OES wrapper)
    # 1: ldr x8, [x20,#0x80](dict obj)   — ambient-source cache (updateRegularOverlay's wrap cache)
    # 2: cbz x8, exit
    # 3: adrp x9, 0x2f12000 (class struct page)
    # 4: ldr x9, [x9,#0xd60]
    # 5: ldr x3, [x9]       (methodInfo for Dictionary<IntPtr,Texture2D>$$set_Item)
    # 6: mov x2, x24        (value)
    # 7: mov x0, x8         (dict)
    # 8: mov x1, x19        (key = nativePtr)
    # 9: bl Dictionary$$set_Item  (0x2028F2C)  — RESTORE ambient path
    #10: mov w0,w25
    #11: mov w1,w22
    #12: mov w2,wzr
    #13: movz w3,#4
    #14: mov x4,xzr
    #15: bl GetTemporary (0x164c814)
    #16: cbz x0, exit
    #17: mov x23,x0
    #18: mov x0,x24
    #19: mov x1,x23
    #20: mov x2,xzr
    #21: bl Graphics$$Blit(src,dst) (0x1870d00)
    #22: mov x0,x23
    #23: mov x1,xzr
    #24: bl GetNativeTexturePtr (0x1e26164)
    #25: ldr x8,[x20,#0x28]
    #26: cbz x8, exit
    #27: mov x1,x23
    #28: mov x2,x0
    #29: mov w3,wzr
    #30: mov x0,x8
    #31: mov x4,xzr
    #32: bl OverrideOverlayTextureInfo (0x10aa6d4)
    #33: b exit
    code = []
    code.append(0xAA0003F8)                    # 0x14a0320  mov x24, x0
    code.append(0xF9404288)                    # 0x14a0324  ldr x8, [x20,#0x80]   (copy of original)
    code.append(enc_cbz(8, EPILOGUE, HELPER_START + 4 * 2))         # cbz x8, epilogue
    code.append(0xD000D389)                    # 0x14a032c  adrp x9, 0x2f12000 (copy of original)
    code.append(0xF946B129)                    # 0x14a0330  ldr x9,[x9,#0xd60]  (copy of original)
    code.append(0xF9400123)                    # 0x14a0334  ldr x3, [x9]        (rn=x9, rd=x3)
    code.append(0xAA1803E2)                    # 0x14a0338  mov x2, x24
    code.append(0xAA0803E0)                    # 0x14a033c  mov x0, x8
    code.append(0xAA1303E1)                    # 0x14a0340  mov x1, x19
    code.append(enc_bl(0x2028F2C, HELPER_START + 4 * 9))            # bl set_Item
    code.append(0x2A1903E0)                    # 0x14a0348  mov w0, w25
    code.append(0x2A1603E1)                    # 0x14a034c  mov w1, w22
    code.append(0x2A1F03E2)                    # 0x14a0350  mov w2, wzr
    code.append(enc_movz_w(3, 4))              # 0x14a0354  mov w3, #4 (RGBA32)
    code.append(0xAA1F03E4)                    # 0x14a0358  mov x4, xzr
    code.append(enc_bl(FN_GETTEMPORARY, HELPER_START + 4 * 15))     # bl GetTemporary
    code.append(enc_cbz(0, EPILOGUE, HELPER_START + 4 * 16))        # cbz x0, epilogue
    code.append(0xAA0003F7)                    # 0x14a0364  mov x23, x0
    code.append(0xAA1803E0)                    # 0x14a0368  mov x0, x24
    code.append(0xAA1703E1)                    # 0x14a036c  mov x1, x23
    code.append(0xAA1F03E2)                    # 0x14a0370  mov x2, xzr
    code.append(enc_bl(FN_BLIT, HELPER_START + 4 * 21))             # bl Graphics$$Blit
    code.append(0xAA1703E0)                    # 0x14a0378  mov x0, x23
    code.append(0xAA1F03E1)                    # 0x14a037c  mov x1, xzr
    code.append(enc_bl(FN_GETNATIVEPTR, HELPER_START + 4 * 24))     # bl GetNativeTexturePtr
    code.append(0xF9401688)                    # 0x14a0384  ldr x8, [x20,#0x28]
    code.append(enc_cbz(8, EPILOGUE, HELPER_START + 4 * 26))        # cbz x8, epilogue
    code.append(0xAA1703E1)                    # 0x14a038c  mov x1, x23
    code.append(0xAA0003E2)                    # 0x14a0390  mov x2, x0
    code.append(0x2A1F03E3)                    # 0x14a0394  mov w3, wzr
    code.append(0xAA0803E0)                    # 0x14a0398  mov x0, x8
    code.append(0xAA1F03E4)                    # 0x14a039c  mov x4, xzr
    code.append(enc_bl(FN_OVERRIDE, HELPER_START + 4 * 32))         # bl OverrideOverlayTextureInfo
    code.append(enc_b(EPILOGUE, HELPER_START + 4 * 33))             # b epilogue

    for i, w in enumerate(code):
        img.append((HELPER_START + 4 * i, w))
    return img

def build_hitbranch():
    """Patch B: redirect the cache-hit path into the helper (so the wrapper bind
    is replaced every frame by our RT bind, keeping the ambient cache alive)."""
    # at 0x149fe74: b 0x14a0320
    return [(0x149FE74, enc_b(HELPER_START, 0x149FE74))]


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_rttexture.py <in.apk> <out.apk>')
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])

    with zipfile.ZipFile(src) as zin:
        member = next(n for n in zin.namelist() if n.endswith('lib/arm64-v8a/libil2cpp.so'))
        data = bytearray(zin.read(member))

    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', data, addr))
        if got != expected:
            print(f'ABORT: {label} @0x{addr:x} does not match an 8ovr build '
                  f'({[hex(w) for w in got]}).')
            return 1
    print('guard OK: input carries patches #1-#3, texparams, patch #6.')

    # fingerprint the block right before the helper (has to be the wrap-create path)
    got = [struct.unpack_from('<I', data, a)[0] for a, _ in PRE_HELPER]
    if got[0] != PRE_HELPER[0][1][0]:
        print(f'ABORT: expected mov w2, #4 @0x14a0320 got {hex(got[0])}.')
        return 1
    # fingerprint the epilogue we branch to
    got_ep = list(struct.unpack_from('<4I', data, EPILOGUE))
    if got_ep != EPILOGUE_CHECK:
        print(f'ABORT: unexpected epilogue @0x{EPILOGUE:x}: {[hex(w) for w in got_ep]}')
        return 1
    print('fingerprint OK: helper window and epilogue match.')

    # sanity: helper window is currently the dict-set / ratio / bind code
    words = list(struct.unpack_from('<34I', data, HELPER_START))
    print('helper-window first 4 words before patch:', [hex(w) for w in words[:4]])

    # write helper
    for off, word in build_helper():
        struct.pack_into('<I', data, off, word)
    # write hit-path branch to the helper (patch #8b)
    print(f'hit-path @0x149fe74 before patch: {struct.unpack_from("<I", data, 0x149FE74)[0]:#010x}')
    for off, word in build_hitbranch():
        struct.pack_into('<I', data, off, word)
    print(f'hit-path @0x149fe74 after patch: {struct.unpack_from("<I", data, 0x149FE74)[0]:#010x}')
    # NOP-fill the remainder up to the epilogue
    for a in range(HELPER_START + 34 * 4, EPILOGUE, 4):
        struct.pack_into('<I', data, a, 0xD503201F)
    print(f'patched: helper at 0x{HELPER_START:x} .. 0x{HELPER_START + 26*4:x} -> '
          f'{34} instructions, NOP-filled to 0x{EPILOGUE:x}')

    with zipfile.ZipFile(src) as zsrc:
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in zsrc.infolist():
                blob = data if info.filename == member else zsrc.read(info.filename)
                zout.writestr(info, blob)

    # ---- verify ----
    with zipfile.ZipFile(dst) as zin:
        backs = zin.read(member)
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    got = list(struct.unpack_from('<34I', backs, HELPER_START))
    want = [w for _, w in build_helper()]
    if got[:len(want)] != want:
        print('ABORT: verification of helper render failed.')
        return 1
    for addr, expected, label in GUARDS:
        got = list(struct.unpack_from(f'<{len(expected)}I', backs, addr))
        if got != expected:
            print(f'ABORT: output does not keep {label} intact.')
            return 1
    # show the disassembly of our helper
    print('disassembly check:')
    for ins in md.disasm(backs[HELPER_START:EPILOGUE], HELPER_START):
        print(f'  {ins.address:#x} {ins.mnemonic} {ins.op_str}')
    print(f'saved: {dst} ({dst.stat().st_size:,} bytes) - verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
