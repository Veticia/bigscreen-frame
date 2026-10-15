#!/usr/bin/env python3
"""Build the fixed Bigscreen APK for Steam Frame.

Code patches (libil2cpp.so, fingerprint-verified):
  #1 EntitlementCheck$$Start @0x1080594 -> immediate success
  #2 BIG_APP_LOADER.get_RequiresOculusPlatform @0xb895cc -> false (PC login branch)
  #3 GetRoomCodeFromClipboard x3 -> ret;nop (void methods; Lepton has no clipboard)
  #4 SKIPPED on purpose: YUV binders stay intact so the fixed shader gets fed.

Asset patch (assets/bin/Data/data.unity3d, via UnityPy typetree):
  BlankPresentationScreen (pid 9) + PresentationScreen (pid 25):
    m_Shader -> 327 BigscreenVR/Unlit/Desktop (FileID 0, verified same CAB),
    m_ShaderKeywords -> "_EMISSION" (as native Desktop pid 12),
    m_LightmapFlags -> 1 (as Desktop),
    m_SavedProperties: keep _MainTex fallback, ADD null _YPlane/_UVPlane TexEnvs,
      merge Desktop float/color lists (notably _Color white: PresentationScreen
      ships _Color black, which multiplies any texture to black; _Width=0,
      _Brightness=1 required by the YUV shader).

Usage:
    python3 tools/build_bigscreen_fixed.py [src.apk] [dst.apk]
Defaults to Games/com.bigscreenvr.bigscreen/... -> Games/com.bigscreenvr.bigscreen-fixed/...
"""

import struct
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
except ImportError:
    Cs = None

SRC_DEFAULT = Path("Games/com.bigscreenvr.bigscreen/com.bigscreenvr.bigscreen.apk")
DST_DEFAULT = Path("Games/com.bigscreenvr.bigscreen-fixed/com.bigscreenvr.bigscreen-fixed.apk")
BUNDLE_MEMBER = "assets/bin/Data/data.unity3d"
IL2CPP_SUFFIX = "lib/arm64-v8a/libil2cpp.so"

PATCH1_ADDR = 0x1080594
PATCH1_EXPECTED = [0xAA1F03E0, 0x97FFDE76]
PATCH1_BL_TARGET = 0x1079F50
PATCH1_REPLACEMENT = [0xAA1303E0, 0x52800021, 0x1400003C]

PATCH2_ADDR = 0xB895CC
PATCH2_EXPECTED = [0xF81E0FF3, 0xA9017BFD]
PATCH2_REPLACEMENT = [0x52800000, 0xD65F03C0]

PATCH3 = [
    (0xBC85C4, [0xA9BE4FF4, 0xA9017BFD], "HomePage$$GetRoomCodeFromClipboard"),
    (0xE3F778, [0xF81D0FF5, 0xA9014FF4], "JoinRoom$$GetRoomCodeFromClipboard"),
    (0xD20078, [0xF81D0FF5, 0xA9014FF4], "SocialPage$$GetRoomCodeFromClipboard"),
]
PATCH3_REPLACEMENT = [0xD65F03C0, 0xD503201F]

TARGET_MATS = ("BlankPresentationScreen", "PresentationScreen")
NEW_SHADER_PID = 327


def patch_lib(data: bytearray) -> None:
    got = list(struct.unpack_from("<2I", data, PATCH1_ADDR))
    if got != PATCH1_EXPECTED:
        sys.exit(f"ABORT: patch1 site {[hex(w) for w in got]} != expected")
    nxt = struct.unpack_from("<I", data, PATCH1_ADDR + 8)[0]
    simm26 = nxt & 0x03FFFFFF
    simm = simm26 - (1 << 26) if (simm26 >> 25) & 1 else simm26
    if (nxt & 0xFC000000) != 0x94000000 or (PATCH1_ADDR + 8) + (simm << 2) != PATCH1_BL_TARGET:
        sys.exit("ABORT: patch1 BL target mismatch")
    print("fingerprint OK: patch1 site @0x1080594")

    got2 = list(struct.unpack_from("<2I", data, PATCH2_ADDR))
    if got2 != PATCH2_EXPECTED:
        sys.exit(f"ABORT: patch2 site {[hex(w) for w in got2]} != expected")
    print("fingerprint OK: patch2 site @0xb895cc")

    for addr, exp, label in PATCH3:
        got3 = list(struct.unpack_from("<2I", data, addr))
        if got3 != exp:
            sys.exit(f"ABORT: patch3 {label} {[hex(w) for w in got3]} != expected")
        print(f"fingerprint OK: {label} @0x{addr:x}")

    for i, w in enumerate(PATCH1_REPLACEMENT):
        struct.pack_into("<I", data, PATCH1_ADDR + i * 4, w)
    for i, w in enumerate(PATCH2_REPLACEMENT):
        struct.pack_into("<I", data, PATCH2_ADDR + i * 4, w)
    for addr, _e, _l in PATCH3:
        struct.pack_into("<I", data, addr, PATCH3_REPLACEMENT[0])
        struct.pack_into("<I", data, addr + 4, PATCH3_REPLACEMENT[1])

    if Cs is not None:
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        for insn in md.disasm(bytes(data[PATCH1_ADDR:PATCH1_ADDR + 12]), PATCH1_ADDR):
            print(f"  0x{insn.address:x}: {insn.mnemonic} {insn.op_str}")


def patch_bundle(bundle_bytes: bytes) -> bytes:
    import UnityPy

    tmpdir = Path("/tmp/opencode/fixed-bundle")
    tmpdir.mkdir(parents=True, exist_ok=True)
    src_tmp = tmpdir / "src.bundle"
    src_tmp.write_bytes(bundle_bytes)
    env = UnityPy.load(str(src_tmp))

    desktop_tree = None
    for obj in env.objects:
        if obj.type.name == "Material":
            try:
                t = obj.read_typetree()
            except Exception:
                continue
            if t.get("m_Name") == "Desktop":
                desktop_tree = t
                break
    if desktop_tree is None:
        sys.exit("ABORT: reference material 'Desktop' not found")
    if desktop_tree.get("m_Shader", {}).get("m_PathID") != NEW_SHADER_PID:
        sys.exit("ABORT: reference 'Desktop' is not on shader 327")

    ref_texenvs = list(desktop_tree["m_SavedProperties"]["m_TexEnvs"])
    ref_floats = list(desktop_tree["m_SavedProperties"]["m_Floats"])
    ref_colors = list(desktop_tree["m_SavedProperties"]["m_Colors"])
    ref_kw = desktop_tree.get("m_ShaderKeywords", "")
    ref_lm = desktop_tree.get("m_LightmapFlags", 1)
    print(f"reference Desktop: kw={ref_kw!r} lm={ref_lm} texenvs={[k for k, _ in ref_texenvs]} "
          f"floats={len(ref_floats)} colors={len(ref_colors)}")

    patched = []
    for obj in env.objects:
        if obj.type.name != "Material":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        name = tree.get("m_Name", "")
        if name not in TARGET_MATS:
            continue
        cur = tree.get("m_Shader", {}).get("m_PathID")
        if cur != 301:
            print(f"warn: {name!r} on shader {cur}, expected 301 — patching anyway")
        # shader pointer + keywords + lighting flags from Desktop
        tree["m_Shader"]["m_PathID"] = NEW_SHADER_PID
        tree["m_Shader"]["m_FileID"] = 0
        tree["m_ShaderKeywords"] = ref_kw
        tree["m_LightmapFlags"] = ref_lm
        # keep _MainTex fallback, add YUV planes + full float/color blocks
        have = {k for k, _ in tree["m_SavedProperties"]["m_TexEnvs"]}
        for k, v in ref_texenvs:
            if k not in have:
                tree["m_SavedProperties"]["m_TexEnvs"].append([k, dict(v)])
                have.add(k)
        have_f = {k for k, _ in tree["m_SavedProperties"]["m_Floats"]}
        for k, v in ref_floats:
            if k not in have_f:
                tree["m_SavedProperties"]["m_Floats"].append([k, v])
        have_c = {k for k, _ in tree["m_SavedProperties"]["m_Colors"]}
        merged = []
        for kk, vv in tree["m_SavedProperties"]["m_Colors"]:
            for rk, rv in ref_colors:
                if rk == kk:
                    vv = dict(rv)
                    break
            merged.append([kk, vv])
        for rk, rv in ref_colors:
            if rk not in have_c:
                merged.append([rk, dict(rv)])
        tree["m_SavedProperties"]["m_Colors"] = merged
        obj.data = bytearray(obj.save_typetree(tree))
        patched.append(name)
        print(f"patched material {name!r}: 301 -> 327 + YUV props + kw={ref_kw!r}")

    if sorted(patched) != sorted(TARGET_MATS):
        sys.exit(f"ABORT: patched {patched}, expected {list(TARGET_MATS)}")

    out_dir = tmpdir / "saved"
    out_dir.mkdir(parents=True, exist_ok=True)
    # clean previous saves
    for p in out_dir.iterdir():
        if p.is_file():
            p.unlink()
    env.save(pack="original", out_path=str(out_dir))
    cands = [p for p in out_dir.iterdir() if p.is_file()]
    if not cands:
        sys.exit("ABORT: UnityPy save produced no files")
    # the bundle file is the large one; json sidecars are tiny
    cands.sort(key=lambda p: p.stat().st_size, reverse=True)
    new_bytes = cands[0].read_bytes()
    print(f"saved bundle: {len(new_bytes):,} bytes (was {len(bundle_bytes):,} bytes)")

    # verify via fresh load
    import shutil
    vdir = tmpdir / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    vtmp = vdir / "v.bundle"
    vtmp.write_bytes(new_bytes)
    venv = UnityPy.load(str(vtmp))
    seen = {}
    for obj in venv.objects:
        if obj.type.name != "Material":
            continue
        try:
            t = obj.read_typetree()
        except Exception:
            continue
        if t.get("m_Name") in TARGET_MATS:
            seen[t["m_Name"]] = (
                t["m_Shader"]["m_PathID"],
                t.get("m_ShaderKeywords"),
                sorted(k for k, _ in t["m_SavedProperties"]["m_TexEnvs"]),
            )
    print(f"verify: {seen}")
    for n in TARGET_MATS:
        pid, kw, keys = seen.get(n, (None, None, []))
        assert pid == NEW_SHADER_PID, f"{n} shader {pid}"
        assert "_YPlane" in keys and "_UVPlane" in keys and "_MainTex" in keys, f"{n} texenvs {keys}"
        assert kw == ref_kw, f"{n} keywords {kw!r}"
    return new_bytes


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DST_DEFAULT
    with zipfile.ZipFile(src) as zin:
        members = zin.infolist()
        il_name = next(n for n in zin.namelist() if n.endswith(IL2CPP_SUFFIX))
        lib = bytearray(zin.read(il_name))
        bundle = zin.read(BUNDLE_MEMBER)
        rest = {i.filename: zin.read(i.filename) for i in members
                if i.filename not in (il_name, BUNDLE_MEMBER)}
    print(f"src bundle {len(bundle):,} bytes, lib {len(lib):,} bytes")
    patch_lib(lib)
    new_bundle = patch_bundle(bundle)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = dst.with_suffix(".tmp")
    with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in members:
            if info.filename == il_name:
                blob = bytes(lib)
            elif info.filename == BUNDLE_MEMBER:
                blob = new_bundle
            else:
                blob = rest[info.filename]
            zout.writestr(info, blob)
    tmp_out.rename(dst)
    # self-verify
    with zipfile.ZipFile(dst) as z:
        vlib = z.read(il_name)
    assert list(struct.unpack_from("<3I", vlib, PATCH1_ADDR)) == PATCH1_REPLACEMENT
    assert list(struct.unpack_from("<2I", vlib, PATCH2_ADDR)) == PATCH2_REPLACEMENT
    for addr, _e, label in PATCH3:
        assert list(struct.unpack_from("<2I", vlib, addr)) == PATCH3_REPLACEMENT, label
    # binders must be INTACT (no PATCH4)
    for addr, exp in [(0xD42598, 0xA9BB67FA), (0xDF44A4, 0xA9BD57F6), (0xCDEE28, 0xA9BD57F6)]:
        assert struct.unpack_from("<I", vlib, addr)[0] == exp, hex(addr)
    print(f"verified: {dst} (code patches 1-3, binders intact, materials fixed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
