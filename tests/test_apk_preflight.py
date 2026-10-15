"""Tests for apk_preflight, using a real APK mutated into known shapes.

A genuine signed APK is downloaded once and then rewritten with different
lib/ layouts, so each test exercises the real androguard parse path instead of
a mocked one. Only lib/ entries change, so package/label/version stay real.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

BASE_URL = "https://f-droid.org/repo/org.fdroid.fdroid_1023051.apk"
BASE_APK = Path(__file__).resolve().parent / "_base.apk"

# Unity + Meta's VrApi, the shape of a typical Quest VR build.
VR_LIBS = [
    "lib/arm64-v8a/libunity.so",
    "lib/arm64-v8a/libOVRPlugin.so",
    "lib/arm64-v8a/libOculusXRPlugin.so",
    "lib/arm64-v8a/libvrapi.so",  # note: no "ovr" prefix - real Meta name
    "lib/arm64-v8a/libvulkan.so",
    "assets/bin/Data/globalgamemanagers",
]

# Bigscreen's real layout: Meta XR plugin, ffmpeg video stack, a WebView, and
# environment assets whose paths contain the letters "obb" (lo-BB-y).
BIGSCREEN_LIBS = [
    "lib/arm64-v8a/libil2cpp.so",
    "lib/arm64-v8a/libunity.so",
    "lib/arm64-v8a/libOVRPlugin.so",
    "lib/arm64-v8a/libOculusXRPlugin.so",
    "lib/arm64-v8a/libUnityOpenXR.so",
    "lib/arm64-v8a/libvrapi.so",
    "lib/arm64-v8a/libovrplatformloader.so",
    "lib/arm64-v8a/libffmpegJNI.so",
    "lib/arm64-v8a/libbig-media-player.so",
    "lib/arm64-v8a/libVuplexWebViewAndroid.so",
    "assets/assetbundles/Android/skyboxes/lobby_sky",
    "assets/assetbundles/Android/ui/backgrounds/lobby.manifest",
]

# The base APK is multi-ABI; to isolate one ABI, drop the rest.
ALL_BUT_ARM64 = {"armeabi-v7a", "x86", "x86_64"}
NO_NATIVE = {"arm64-v8a", "armeabi-v7a", "x86", "x86_64"}
# Keep only armeabi-v7a, so the APK has no arm64 directory at all.
ONLY_32BIT = {"arm64-v8a", "x86", "x86_64"}


def build_variant(dest: Path, libs: list[str], drop_abis: set[str]) -> Path:
    """Copy the base APK, keeping only wanted ABI dirs and adding `libs`."""
    with zipfile.ZipFile(BASE_APK) as zin, zipfile.ZipFile(
        dest, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            if item.filename.startswith("lib/"):
                abi = item.filename.split("/")[1]
                if abi in drop_abis:
                    continue
            zout.writestr(item, zin.read(item.filename))
        for lib in libs:
            zout.writestr(lib, b"\x7fELF" + b"\0" * 64)
    return dest


class PreflightTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        if not BASE_APK.exists():
            subprocess.run(
                ["curl", "-sSL", "-o", str(BASE_APK), BASE_URL], check=True
            )
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()
        if BASE_APK.exists():
            BASE_APK.unlink()

    def analyse(self, name: str, libs: list[str], drop: set[str] | None = None):
        import apk_preflight as pf

        apk = build_variant(self.dir / f"{name}.apk", libs, drop or set())
        return pf.analyse(apk)

    def codes(self, report) -> set[str]:
        return {f.code for f in report.findings}

    # --- the hard blocker ------------------------------------------------

    def test_32bit_only_is_blocked(self):
        r = self.analyse("legacy", ["lib/armeabi-v7a/libunity.so"], drop=ONLY_32BIT)
        self.assertIn("no-arm64", self.codes(r))
        self.assertEqual(r.verdict, "BLOCKED")
        self.assertTrue(r.blocked)
        self.assertEqual(r.abis, ["armeabi-v7a"])

    def test_blocker_outranks_warnings(self):
        # A 32-bit build that also trips every soft warning stays BLOCKED.
        r = self.analyse("legacy_warny", ["lib/armeabi-v7a/libunity.so", "lib/armeabi-v7a/libvulkan.so"], drop=ONLY_32BIT)
        self.assertEqual(r.verdict, "BLOCKED")

    def test_blocked_exits_nonzero(self):
        apk = build_variant(self.dir / "blocked.apk", ["lib/armeabi-v7a/libunity.so"], {"arm64-v8a", "x86", "x86_64"})
        proc = subprocess.run(
            [sys.executable, str(TOOLS / "apk_preflight.py"), str(apk), "-q"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    # --- a typical Quest VR build ---------------------------------------

    def test_quest_vr_build_shape(self):
        r = self.analyse("quest", VR_LIBS, drop=ALL_BUT_ARM64)
        self.assertEqual(r.abis, ["arm64-v8a"])
        self.assertEqual(r.engine, "Unity")
        self.assertIn("libvulkan.so", r.graphics)
        self.assertNotIn("no-arm64", self.codes(r))
        # VrApi conversion + Vulkan-on-Zink are both expected warnings.
        self.assertEqual(r.verdict, "NEEDS PATCHING")
        self.assertIn("xr-vrapi-only", self.codes(r))
        self.assertIn("vulkan", self.codes(r))

    def test_openxr_native_is_the_easy_path(self):
        r = self.analyse(
            "openxr",
            ["lib/arm64-v8a/libopenxr_loader.so", "lib/arm64-v8a/libunity.so"],
            drop=ALL_BUT_ARM64,
        )
        self.assertEqual(r.xr_api, "OpenXR (native)")
        self.assertIn("xr-openxr", self.codes(r))
        self.assertNotIn("vulkan", self.codes(r))

    def test_multi_abi_is_not_a_problem(self):
        r = self.analyse("multi", ["lib/arm64-v8a/libunity.so"])
        self.assertIn("arm64-v8a", r.abis)
        self.assertIn("multi-abi", self.codes(r))
        self.assertNotIn("no-arm64", self.codes(r))
        self.assertFalse(r.blocked)

    # --- regressions found on the real Bigscreen APK --------------------

    def test_bigscreen_shape(self):
        """The actual 0.950.2 layout, arm64-only, Meta XR, no OBB."""
        r = self.analyse("bigscreen", BIGSCREEN_LIBS, drop=ALL_BUT_ARM64)
        self.assertEqual(r.abis, ["arm64-v8a"])
        self.assertFalse(r.blocked)
        self.assertEqual(r.engine, "Unity")
        # libvrapi.so must be recognised: libovrapi.so does not exist.
        self.assertEqual(r.xr_api, "VrApi (Meta, direct)")
        self.assertIn("xr-vrapi-only", self.codes(r))

    def test_lobby_assets_are_not_obb(self):
        """"lo-BB-y" contains "obb" - that must not look like game data."""
        r = self.analyse("lobby", BIGSCREEN_LIBS, drop=ALL_BUT_ARM64)
        self.assertFalse(r.needs_game_data)
        self.assertNotIn("game-data", self.codes(r))

    def test_real_obb_files_are_still_detected(self):
        r = self.analyse(
            "obb",
            ["lib/arm64-v8a/libunity.so", "assets/main.203.com.example.obb"],
            drop=ALL_BUT_ARM64,
        )
        self.assertTrue(r.needs_game_data)
        self.assertIn("game-data", self.codes(r))

    def test_libovrapi_does_not_exist(self):
        """Guard the name that caused a real false negative."""
        r = self.analyse(
            "bogus", ["lib/arm64-v8a/libovrapi.so"], drop=ALL_BUT_ARM64
        )
        self.assertNotIn("xr-vrapi-only", self.codes(r))

    def test_unity_openxr_plugin_notices_the_conversion_hint(self):
        r = self.analyse(
            "unityopenxr",
            ["lib/arm64-v8a/libvrapi.so", "lib/arm64-v8a/libUnityOpenXR.so"],
            drop=ALL_BUT_ARM64,
        )
        self.assertEqual(r.xr_api, "VrApi (Meta, direct)")
        hint = next(f for f in r.findings if f.code == "xr-vrapi-only").fix
        self.assertIn("Unity's OpenXR plugin", hint)

    # --- metadata --------------------------------------------------------

    def test_real_metadata_is_read(self):
        r = self.analyse("meta", [], drop=NO_NATIVE)
        self.assertEqual(r.package, "org.fdroid.fdroid")
        self.assertEqual(r.label, "F-Droid")
        self.assertTrue(r.version_code.isdigit())

    def test_no_native_libs_is_flagged(self):
        r = self.analyse("nolibs", [], drop=NO_NATIVE)
        self.assertIn("no-native-libs", self.codes(r))

    def test_verdict_portable_when_nothing_is_wrong(self):
        r = self.analyse(
            "clean",
            ["lib/arm64-v8a/libopenxr_loader.so"],
            drop=ALL_BUT_ARM64,
        )
        self.assertEqual(r.verdict, "PORTABLE")

    def test_json_is_serialisable(self):
        import json

        r = self.analyse("json", VR_LIBS, drop=ALL_BUT_ARM64)
        d = json.loads(json.dumps(r.to_dict()))
        self.assertEqual(d["verdict"], "NEEDS PATCHING")
        self.assertEqual(d["engine"], "Unity")
        self.assertEqual(d["graphics"], ["libvulkan.so"])
        self.assertEqual(d["abis"], ["arm64-v8a"])
        # VrApi conversion and Vulkan-on-Zink are the only complaints here.
        self.assertEqual(sorted({f["severity"] for f in d["findings"]}), ["WARN"])


if __name__ == "__main__":
    unittest.main(verbosity=2)