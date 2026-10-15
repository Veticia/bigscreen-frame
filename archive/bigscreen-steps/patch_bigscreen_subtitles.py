#!/usr/bin/env python3
"""Patch #15: subtitles without a usable duration stay up (4 s or until the next cue) instead of one frame.

Why: BigContentController.OnSubtitleCue shows the cue text and starts
SubtitleTimer(cue.Duration.TotalSeconds), which waits that long and clears the
text. CEA-608 captions (TV channels: `application/cea-608`) arrive without an
end time, so the duration is ~0 / unset and the caption is cleared on the next
frame (tested on 23show: subtitles flash for one frame). A new cue already stops
the previous timer, so a fallback duration is safe.

Patch: in <SubtitleTimer>d__103.MoveNext the conversion `fcvt s0, d8` (0xdb973c)
branches to a 6-instruction cave that computes
    s0 = (float)time > 0.125 ? (float)time : 4.0
and branches back to 0xdb9740 (WaitForSeconds ctor setup). NaN also takes the
fallback. The cave is at 0x149fd88: the unreachable remainder of the old
UpdateRegularOverlay body behind the patch #9/#14 stub (which ends in an
unconditional tail call at 0x149fd84). FP encodings come from
aarch64-linux-gnu-as.

Expected input: stub v2 build (patch_bigscreen_showscreen), e.g. 23show.

Usage:
    python3 tools/patch_bigscreen_subtitles.py <in.apk> <out.apk>
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
import patch_bigscreen_showscreen as showscreen  # noqa: E402

SITE = 0xDB973C
SITE_ORIG = 0x1E624100          # fcvt s0, d8
RESUME = 0xDB9740
CAVE = 0x149FD88
CAVE_ORIG = [0xF943E000, 0x97D86182, 0xF000D380, 0xF946B000, 0x97D8617F, 0xB000D4E0]
CAVE_FP = [0x1E624100,          # fcvt  s0, d8
           0x1E221001,          # fmov  s1, #4.0
           0x1E281002,          # fmov  s2, #0.125
           0x1E222000,          # fcmp  s0, s2
           0x1E21CC00]          # fcsel s0, s0, s1, gt
EXPECTED_CAVE = ['fcvt s0, d8', 'fmov s1, #4.00000000', 'fmov s2, #0.12500000', 'fcmp s0, s2',
                 'fcsel s0, s0, s1, gt', f'b #{RESUME:#x}']


def disasm(data, addr, n):
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    return [f'{i.mnemonic} {i.op_str}' for i in md.disasm(bytes(data[addr:addr + 4 * n]), addr)]


def check(lib) -> bool:
    # FP words are compared exactly (capstone prints fmov immediates locale-dependently, e.g. "#4,00000000");
    # mnemonics and both branches are checked by disassembly.
    cave = disasm(lib, CAVE, len(EXPECTED_CAVE))
    ok = meshscreen.words(lib, CAVE, len(CAVE_FP)) == CAVE_FP and disasm(lib, SITE, 1) == [f'b #{CAVE:#x}']
    for g, e in zip(cave, EXPECTED_CAVE):
        if g.split(' ')[0] != e.split(' ')[0] or (not g.startswith('fmov') and g != e):
            print(f'  MISMATCH: got {g!r}, expected {e!r}')
            ok = False
    return ok and len(cave) == len(EXPECTED_CAVE)


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: patch_bigscreen_subtitles.py <in.apk> <out.apk>')
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    with zipfile.ZipFile(src) as zsrc:
        lib_name = next(n for n in zsrc.namelist() if n.endswith(meshscreen.LIB_SUFFIX))
        lib = bytearray(zsrc.read(lib_name))

    exp_stub = showscreen.expected_v2()
    if showscreen.disasm(lib, len(exp_stub)) != exp_stub:
        print('ABORT: input does not carry stub v2 (cave would not be dead code).')
        return 1
    if meshscreen.words(lib, SITE, 1) != [SITE_ORIG] or meshscreen.words(lib, CAVE, len(CAVE_ORIG)) != CAVE_ORIG:
        print('ABORT: subtitle timer or cave bytes do not match the expected build.')
        return 1
    print('fingerprint OK: stub v2 present, SubtitleTimer fcvt @0xdb973c, cave @0x149fd88 untouched.')

    for i, w in enumerate(CAVE_FP):
        struct.pack_into('<I', lib, CAVE + 4 * i, w)
    struct.pack_into('<I', lib, CAVE + 4 * len(CAVE_FP),
                     meshscreen.b_imm(CAVE + 4 * len(CAVE_FP), RESUME, False))
    struct.pack_into('<I', lib, SITE, meshscreen.b_imm(SITE, CAVE, False))
    if not check(lib):
        print('ABORT: disassembly mismatch.')
        return 1
    print('patched: subtitle duration <= 0.125 s (or NaN) -> 4 s (disassembly verified).')

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
