"""Tests for bigscreen/bspatch that need no Bigscreen files.

Code patches run against a synthetic libil2cpp.so that only contains the
fingerprinted words; the expected results are the words of the build that was
tested on a Steam Frame (24subs, sha256 3cf780ee...). Shader rewrites run on
synthetic blobs with the same container layout and the same source landmarks.
"""

import pathlib
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'bigscreen'))

from bspatch import apk, arm64, code_patches, shader_blob  # noqa: E402

# Words at each patch site in the Frame-tested build.
TESTED_WORDS = {
    0xB895CC: [0x52800000, 0xD65F03C0],
    0xB98FD0: [0x3903427F],
    0xBC85C4: [0xD65F03C0, 0xD503201F],
    0xBFD1F0: [0x2A1F03F4],
    0xD20078: [0xD65F03C0, 0xD503201F],
    0xDB973C: [0x141B9993],
    0xE3F778: [0xD65F03C0, 0xD503201F],
    0x10372D8: [0x14000025],
    0x1080594: [0xAA1303E0, 0x52800021, 0x1400003C],
    0x149FD1C: [0xA9BD7BFD, 0x910003FD, 0xA90153F3, 0xA9025BF5, 0xAA0003F3, 0xAA0103F4, 0xAA0203F5, 0x2A0303F6,
                0xF9401660, 0xB4000080, 0x2A1F03E1, 0xAA1F03E2, 0x940F0F28, 0xF9401260, 0xB4000080, 0x52800021,
                0xAA1F03E2, 0x940F3B08, 0xAA1303E0, 0xAA1403E1, 0xAA1503E2, 0x2A1603E3, 0xAA1F03E4, 0xA9425BF5,
                0xA94153F3, 0xA8C37BFD, 0x14000200],
    0x149FD88: [0x1E624100, 0x1E221001, 0x1E281002, 0x1E222000, 0x1E21CC00, 0x17E46669],
    0x1E25EFC: [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F],
    0x1E25F8C: [0xD65F03C0, 0xD503201F, 0xD503201F, 0xD503201F],
}


def synthetic_lib() -> bytearray:
    lib = bytearray(0x1E26000)
    for p in code_patches.PATCHES:
        arm64.put(lib, p.addr, p.original)
    for addr, prologue, _ in code_patches.CALLEE_PROLOGUES:
        if prologue:
            arm64.put(lib, addr, prologue)
    return lib


class CodePatchTests(unittest.TestCase):
    def test_branch_encoding(self):
        self.assertEqual(arm64.branch(0x10372D8, 0x103736C), 0x14000025)
        self.assertEqual(arm64.branch(0xDB973C, 0x149FD88), 0x141B9993)
        self.assertEqual(arm64.branch(0x149FD9C, 0xDB9740), 0x17E46669)      # backwards
        self.assertEqual(arm64.branch(0x149FD4C, 0x18639EC, link=True), 0x940F0F28)
        with self.assertRaises(ValueError):
            arm64.branch(0, 2)

    def test_patches_reproduce_tested_build(self):
        lib = synthetic_lib()
        code_patches.apply(lib)
        self.assertEqual(code_patches.check_patched(lib), [])
        for addr, ws in TESTED_WORDS.items():
            self.assertEqual(arm64.words(lib, addr, len(ws)), ws, hex(addr))
        self.assertEqual(sum(len(p.new) for p in code_patches.PATCHES), sum(map(len, TESTED_WORDS.values())))

    def test_refuses_wrong_build(self):
        lib = synthetic_lib()
        arm64.put(lib, 0xBFD1F0, [0])
        with self.assertRaises(SystemExit):
            code_patches.apply(lib)
        lib = synthetic_lib()
        code_patches.apply(lib)
        with self.assertRaises(SystemExit):          # applying twice must fail, not double-patch
            code_patches.apply(lib)

    def test_patches_do_not_overlap(self):
        spans = sorted((p.addr, p.addr + 4 * len(p.original)) for p in code_patches.PATCHES)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start)


def make_blob(sources: list[bytes]) -> bytes:
    """Container shaped like Unity's: count, (offset, length, segment)*, subprograms with a GLSL length prefix."""
    subs = [shader_blob.SUBPROGRAM_MAGIC + b'\0' * 28 + struct.pack('<I', len(s)) + s + b'\0' * 12 for s in sources]
    head = 4 + 12 * len(subs)
    out, off = bytearray(struct.pack('<I', len(subs))), head
    for s in subs:
        out += struct.pack('<III', off, len(s), 0)
        off += len(s)
    return bytes(out) + b''.join(subs)


OESBLIT_SRC = (shader_blob.OESBLIT_PRELUDE + b'#define X 1\n'
               b'#ifdef VERTEX\nuniform   int _linearToSrgb;\nuniform   int _premultiply;\n'
               + shader_blob.UV_Y + b'\n#endif\n#ifdef FRAGMENT\nuniform   int _linearToSrgb;\n'
               b'uniform   int _premultiply;\n#endif\r\n      \n')

DESKTOP_SRC = (b'#version 300 es\n// ' + b'x' * 20 + b'\n#ifdef VERTEX\n#if defined(STEREO_MULTIVIEW_ON)\n'
               b'          ////////////////////////////////////////////////////////// SINGLE PASS STEREO\n'
               b'          layout(num_views = 2) in;\n          in highp vec3 in_POSITION0;\n'
               b'            vs_TEXCOORD0.y = vs_TEXCOORD0.y;\n#else\n'
               b'          ////////////////////////////////////////////////////////// MULTI PASS STEREO\n'
               b'          in highp vec3 in_POSITION0;\n          uvs = in_TEXCOORD0.xy;\n#endif\n#endif\n'
               b'#ifdef FRAGMENT\n'
               b'          ////////////////////////////////////////////////////////// SINGLE PASS STEREO\n'
               b'            // To account for color data loss on LCD in dark areas, we bump up the range and then '
               b'scale it down.\n'
               b'          ////////////////////////////////////////////////////////// MULTI PASS STEREO\n'
               b'            // To account for color data loss on LCD in dark areas, we bump up the range and then '
               b'scale it down.\n#endif\n')


class ShaderBlobTests(unittest.TestCase):
    def test_oesblit_rewrite_keeps_layout(self):
        raw = make_blob([OESBLIT_SRC, OESBLIT_SRC])
        out = shader_blob.map_sources(raw, shader_blob.fix_oesblit)
        self.assertEqual(len(out), len(raw))
        self.assertEqual(shader_blob.table(out), shader_blob.table(raw))
        for src in shader_blob.sources(out):
            self.assertEqual(src.count(b'uniform highp int '), 4)
            self.assertIn(shader_blob.UV_Y_FLIPPED, src)
            self.assertEqual(src.count(shader_blob.EXT_LINE), 1)
            self.assertIsNone(shader_blob.OESBLIT_INT_RX.search(src))

    def test_desktop_stereo_rewrite_keeps_layout(self):
        raw = make_blob([DESKTOP_SRC])
        out = shader_blob.map_sources(raw, shader_blob.fix_desktop_stereo)
        self.assertEqual(len(out), len(raw))
        src = shader_blob.sources(out)[0]
        self.assertIn(shader_blob.MP_UV_NEW, src)
        self.assertIn(shader_blob.MV_UV_NEW, src)
        self.assertIn(b'uniform int _3dMode;layout(num_views = 2) in;', src)
        self.assertIn(b'uniform int _3dMode;uniform int unity_StereoEyeIndex;in highp vec3 in_POSITION0;', src)
        self.assertNotIn(b'PASS STEREO', src)

    def test_broken_table_is_rejected(self):
        raw = bytearray(make_blob([OESBLIT_SRC]))
        struct.pack_into('<I', raw, 8, 1)                # wrong subprogram length
        self.assertFalse(shader_blob.table_ok(bytes(raw)))
        with self.assertRaises(SystemExit):
            shader_blob.map_sources(bytes(raw), shader_blob.fix_oesblit)

    def test_unexpected_source_is_rejected(self):
        with self.assertRaises(SystemExit):
            shader_blob.fix_oesblit(OESBLIT_SRC.replace(b'_premultiply', b'_other', 1))


class ApkTests(unittest.TestCase):
    def test_rewrite_keeps_other_members(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            src, dst, big = d / 'in.apk', d / 'out.apk', d / 'big.bin'
            with zipfile.ZipFile(src, 'w') as z:
                z.writestr('a.txt', b'a')
                z.writestr(zipfile.ZipInfo('assets/bin/Data/data.unity3d'), b'old')
                z.writestr('lib/arm64-v8a/libil2cpp.so', b'oldlib', compress_type=zipfile.ZIP_DEFLATED)
            big.write_bytes(b'n' * 100000)
            apk.rewrite(src, dst, {apk.BUNDLE: big, apk.LIB_IL2CPP: b'newlib'})
            with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
                self.assertEqual(a.namelist(), b.namelist())
                self.assertEqual(b.read('a.txt'), b'a')
                self.assertEqual(b.read(apk.BUNDLE), b'n' * 100000)
                self.assertEqual(b.read(apk.LIB_IL2CPP), b'newlib')
                for n in a.namelist():
                    self.assertEqual(a.getinfo(n).compress_type, b.getinfo(n).compress_type)


if __name__ == '__main__':
    unittest.main()
