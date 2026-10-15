#!/usr/bin/env python3
"""Static preflight check: can this Quest APK run on the Steam Frame?

FramePort's own analysis stage answers "what does this APK need". This answers
the question you want *before* you point FramePort at a headset: "is this worth
the trip, and which failure mode am I walking into".

Usage:
    apk_preflight.py GAME.apk [GAME.apk ...] [-o report.json] [--quiet]

Exit status is 0 if every APK is portable, 1 if any is blocked, 2 on error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from pathlib import Path

try:
    from androguard.core.apk import APK
except ImportError:  # pragma: no cover - dependency hint
    sys.exit("androguard is required:  pip install androguard")

# androguard logs every resource chunk at DEBUG, which buries the report in
# thousands of lines. Drop its handlers before it is imported.
try:
    from loguru import logger as _loguru

    _loguru.remove()
except ImportError:  # pragma: no cover - androguard always pulls loguru in
    pass

# The Frame is an ARM64 Snapdragon with no AArch32 support at all.
FRAME_ABIS = {"arm64-v8a"}
DEAD_ABIS = {"armeabi-v7a", "armeabi", "x86", "x86_64", "mips", "mips64"}

# Signatures of Meta-specific platform coupling.
META_PERMISSION_PREFIXES = ("com.oculus.", "com.meta.", "oculus.")

# Native library names are case-sensitive and the "ovr" prefix is not universal:
# Meta's XR API ships as libvrapi.so, not libovrapi.so. Unity titles also carry
# the Meta XR plugin, the classic OVRPlugin, and the platform loader.
META_LIBRARIES = (
    "libvrapi.so",
    "libOVRPlugin.so",
    "libOculusXRPlugin.so",
    "libovrclient.so",
    "libovrloader.so",
    "libovrplatformloader.so",
    "libOVRLipSync.so",
)
OPENXR_LIBRARIES = ("libopenxr_loader.so", "libUnityOpenXR.so")

# Real OBB / expansion payloads only. Never a bare "obb" substring: that matches
# "lo-BB-y", and Bigscreen ships assets/assetbundles/.../lobby_sky.
OBB_PATTERN = re.compile(r"(\.obb$|^obb/|/obb/)", re.IGNORECASE)

ENGINE_LIBRARIES = {
    "libunity.so": "Unity",
    "libUE4.so": "Unreal Engine 4",
    "libUnreal.so": "Unreal Engine 5",
    "libgodot_android.so": "Godot",
}

GRAPHICS_LIBRARIES = ("libvulkan.so", "libGLESv2.so", "libGLESv3.so")


class Severity(IntEnum):
    BLOCKER = 3  # will not run on the Frame, and no patch can fix it
    WARN = 2  # will probably need a patch; may lose features
    INFO = 1  # worth knowing


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    fix: str | None = None


@dataclass
class Report:
    apk: str
    size_mb: float
    package: str | None = None
    label: str | None = None
    version_code: str | None = None
    abis: list[str] = field(default_factory=list)
    engine: str | None = None
    xr_api: str | None = None
    graphics: list[str] = field(default_factory=list)
    meta_permissions: list[str] = field(default_factory=list)
    needs_game_data: bool = False
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        sevs = {f.severity for f in self.findings}
        if Severity.BLOCKER in sevs:
            return "BLOCKED"
        if Severity.WARN in sevs:
            return "NEEDS PATCHING"
        return "PORTABLE"

    @property
    def blocked(self) -> bool:
        return Severity.BLOCKER in {f.severity for f in self.findings}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        d["findings"] = [
            {**asdict(f), "severity": f.severity.name} for f in self.findings
        ]
        return d


def abis_of(files: list[str]) -> list[str]:
    """Distinct ABI directory names under lib/."""
    return sorted(
        {
            name.split("/")[1]
            for name in files
            if name.startswith("lib/") and name.count("/") >= 2
        }
    )


def basenames_of(files: list[str]) -> set[str]:
    """Every entry's bare file name, so lib lookups ignore the ABI folder.

    Without this, ``"libovrapi.so" in apk.get_files()`` is never true: the
    real entry is ``lib/arm64-v8a/libovrapi.so``.
    """
    return {name.rsplit("/", 1)[-1] for name in files}


def check_abi(report: Report) -> None:
    """The single most common hard stop: the Frame has no 32-bit ARM."""
    if not report.abis:
        report.findings.append(
            Finding(
                Severity.WARN,
                "no-native-libs",
                "APK ships no native libraries.",
                "Typical of a managed 2D app; it may run in an Android window. "
                "A VR game should have lib/arm64-v8a - double-check this is the "
                "right APK.",
            )
        )
        return

    if not (FRAME_ABIS & set(report.abis)):
        dead = sorted(set(report.abis) & DEAD_ABIS)
        report.findings.append(
            Finding(
                Severity.BLOCKER,
                "no-arm64",
                f"No arm64-v8a build - only {', '.join(report.abis)}.",
                "The Steam Frame is ARM64-only and has no AArch32 support, so no "
                "patch can rescue this. Find a PC VR version of the title instead.",
            )
        )
        return

    if set(report.abis) & DEAD_ABIS:
        report.findings.append(
            Finding(
                Severity.INFO,
                "multi-abi",
                f"Multi-ABI APK ({', '.join(report.abis)}); the arm64-v8a "
                "directory is the one that will be used.",
            )
        )


def check_xr_api(bases: set[str], report: Report) -> None:
    """Direct VrApi needs OVRPort; native OpenXR is much closer to native."""
    has_vrapi = bool(bases & set(META_LIBRARIES))
    has_openxr = "libopenxr_loader.so" in bases

    if has_vrapi and has_openxr:
        report.xr_api = "VrApi + OpenXR loader"
        report.findings.append(
            Finding(
                Severity.INFO,
                "xr-vrapi",
                "Uses Meta's VrApi directly; OVRPort will convert it to OpenXR.",
            )
        )
    elif has_vrapi:
        report.xr_api = "VrApi (Meta, direct)"
        fix = (
            "OVRPort has to translate the whole XR layer. This is the normal "
            "case for Quest builds and FramePort handles it, but it is the "
            "step most likely to need patching."
        )
        if "libUnityOpenXR.so" in bases:
            fix += (
                " This build also ships Unity's OpenXR plugin, which can give "
                "OVRPort a cleaner conversion target than the Meta XR plugin."
            )
        report.findings.append(
            Finding(Severity.WARN, "xr-vrapi-only",
                    "Uses Meta's VrApi directly with no OpenXR loader.", fix)
        )
    elif has_openxr:
        report.xr_api = "OpenXR (native)"
        report.findings.append(
            Finding(
                Severity.INFO,
                "xr-openxr",
                "Already speaks OpenXR - the closest match to the Frame's runtime.",
            )
        )
    else:
        report.xr_api = "none detected"
        report.findings.append(
            Finding(
                Severity.WARN,
                "xr-none",
                "No OpenXR loader and no VrApi library found.",
                "May be a 2D app, or the XR path is in a split/asset file. "
                "FramePort's own analysis will say more once it scans the folder.",
            )
        )


def check_meta_coupling(apk: APK, report: Report) -> None:
    """Permissions and services that have no equivalent on the Frame."""
    perms = sorted(
        p
        for p in apk.get_permissions()
        if any(p.startswith(prefix) for prefix in META_PERMISSION_PREFIXES)
    )
    report.meta_permissions = perms

    if perms:
        report.findings.append(
            Finding(
                Severity.WARN,
                "meta-permissions",
                f"{len(perms)} Meta-specific permission(s): "
                f"{', '.join(perms[:4])}"
                + ("..." if len(perms) > 4 else ""),
                "The Frame has no Horizon services. Anything gated behind these "
                "(store purchases, entitlements, presence) will not work. "
                "Single-player content is usually unaffected.",
            )
        )


def check_game_data(files: list[str], report: Report) -> None:
    """OBB / expansion files are not in the APK and must be pulled separately."""
    needs = [n for n in files if OBB_PATTERN.search(n)]
    report.needs_game_data = bool(needs)

    if report.needs_game_data:
        report.findings.append(
            Finding(
                Severity.INFO,
                "game-data",
                "References separate game data (.obb / expansion assets).",
                "Pull the OBB folder too, or the game installs but stops at "
                "once. FramePort names the data folder after the package.",
            )
        )


def check_graphics(bases: set[str], report: Report) -> None:
    report.graphics = sorted(bases & set(GRAPHICS_LIBRARIES))
    if "libvulkan.so" in bases:
        report.findings.append(
            Finding(
                Severity.WARN,
                "vulkan",
                "Links Vulkan directly.",
                "The Frame renders through Mesa/Zink, and a few titles crash the "
                "Vulkan driver there (see HITMAN 3 VR, Espire 1 in FramePort's "
                "tested list). If it crashes on launch, try forcing OpenGL ES.",
            )
        )


def detect_engine(bases: set[str], files: list[str], report: Report) -> None:
    for lib, name in ENGINE_LIBRARIES.items():
        if lib in bases:
            report.engine = name
            return
    # Some Unity titles only leave the data layout behind.
    if any(f.startswith("assets/bin/Data/") for f in files):
        report.engine = "Unity (inferred)"
        return
    report.engine = "unknown"


def analyse(path: Path) -> Report:
    apk = APK(str(path))
    files = apk.get_files()
    bases = basenames_of(files)

    report = Report(
        apk=path.name,
        size_mb=round(path.stat().st_size / 1_048_576, 1),
    )
    report.package = apk.get_package()
    report.label = apk.get_app_name()
    report.version_code = str(apk.get_androidversion_code() or "?")
    report.abis = abis_of(files)
    detect_engine(bases, files, report)

    # Ordered: a blocker anywhere must win over softer advice.
    check_abi(report)
    check_xr_api(bases, report)
    check_meta_coupling(apk, report)
    check_game_data(files, report)
    check_graphics(bases, report)

    return report


VERDICT_STYLE = {
    "PORTABLE": "\033[32m",
    "NEEDS PATCHING": "\033[33m",
    "BLOCKED": "\033[31m",
}
SEV_STYLE = {
    Severity.BLOCKER: "\033[31m",
    Severity.WARN: "\033[33m",
    Severity.INFO: "\033[36m",
}
RESET = "\033[0m"


def print_report(r: Report, colour: bool) -> None:
    style = (lambda s: s) if not colour else (lambda s: s)
    tag = VERDICT_STYLE.get(r.verdict, "") if colour else ""
    end = RESET if colour else ""

    print(f"\n{r.apk}  ({r.size_mb} MB)")
    print(f"  {r.label or '?'}  {r.package or '?'}  v{r.version_code}")
    print(
        f"  ABI: {', '.join(r.abis) or 'none'}   engine: {r.engine}   XR: {r.xr_api}"
    )
    print(f"  => {tag}{r.verdict}{end}")

    for f in sorted(r.findings, key=lambda f: -f.severity):
        sev = f.severity.name.ljust(7)
        s = SEV_STYLE.get(f.severity, "") if colour else ""
        print(f"    [{s}{sev}{end}] {f.message}")
        if f.fix:
            for line in _wrap(f.fix, 74):
                print(f"              {line}")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("apks", nargs="+", type=Path, help="APK file(s) to check")
    ap.add_argument("-o", "--out", type=Path, help="write a JSON report here")
    ap.add_argument("-q", "--quiet", action="store_true", help="JSON only")
    ap.add_argument("--no-colour", action="store_true")
    args = ap.parse_args(argv)

    reports = []
    for path in args.apks:
        if not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            return 2
        try:
            reports.append(analyse(path))
        except Exception as exc:  # noqa: BLE001 - surface and keep going
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            return 2

    if not args.quiet:
        colour = sys.stdout.isatty() and not args.no_colour
        for r in reports:
            print_report(r, colour)

    if args.out:
        args.out.write_text(
            json.dumps([r.to_dict() for r in reports], indent=2) + "\n"
        )
        if not args.quiet:
            print(f"\nWrote {args.out}")

    return 1 if any(r.blocked for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())