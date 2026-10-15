#!/usr/bin/env python3
"""Pull a Quest app off a Quest and lay it out for FramePort.

FramePort never downloads games - it only installs files you already have. This
gets those files off a Quest you own, into the exact folder layout FramePort
scans, and reports whether the app is even portable before you go further.

Usage:
    quest_extract.py --list                        # show installed VR apps
    quest_extract.py -p com.riftco.bigscreen        # extract one, into ./Games
    quest_extract.py -p com.riftco.bigscreen --data --wifi 192.168.1.50:5555

Requires: adb on PATH, Developer Mode on, and the Quest authorised on this PC.

The app does not need to be running, and does not need to be unlocked - this
only reads packages that are installed on the headset you own.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# FramePort wants: one folder per game, the APK inside, and game data in a
# subfolder named exactly after the package (or literally "obb").
OBB_REMOTE = "/sdcard/Android/obb/{pkg}"
DATA_REMOTE = "/sdcard/Android/data/{pkg}"

VR_HINT = re.compile(
    r"oculus|vr|quest|openxr", re.IGNORECASE
)


def run(args: list[str], *, binary: bool = False, timeout: int = 300):
    """adb wrapper. Returns CompletedProcess; never raises on device errors."""
    try:
        return subprocess.run(
            ["adb", *args],
            capture_output=True,
            text=not binary,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        sys.exit("adb is not on PATH. Install Android platform-tools.")


def adb_prefix(serial: str | None) -> list[str]:
    return ["-s", serial] if serial else []


def require_device(serial: str | None) -> str:
    out = run(["devices"] + adb_prefix(serial) + ["-l"], timeout=30)
    if out.returncode != 0:
        sys.exit(f"adb devices failed:\n{out.stderr}")
    lines = [ln for ln in out.stdout.splitlines()[1:] if ln.strip()]
    devices = [ln for ln in lines if ln.split()[1] == "device"]
    if not devices:
        sys.exit(
            "No authorised Quest found.\n"
            "  - plug it in with a USB-C cable, or: quest_extract.py --wifi <ip>:5555\n"
            "  - enable Developer Mode in the Meta app's device settings\n"
            "  - accept the 'Allow USB debugging' prompt on the headset\n\n"
            f"adb said:\n{out.stdout}{out.stderr}"
        )
    return out.stdout.splitlines()[1].split()[0]


def list_packages(serial: str) -> None:
    out = run(adb_prefix(serial) + ["shell", "pm", "list", "packages", "-3"], timeout=60)
    if out.returncode != 0:
        sys.exit(f"Could not list packages:\n{out.stderr}")
    pkgs = [
        ln.removeprefix("package:").strip()
        for ln in out.stdout.splitlines()
        if ln.startswith("package:")
    ]
    vr = [p for p in pkgs if VR_HINT.search(p)]
    rest = [p for p in pkgs if p not in set(vr)]

    def block(title: str, items: list[str]) -> None:
        if not items:
            return
        print(f"\n{title} ({len(items)})")
        for p in sorted(items):
            print(f"  {p}")

    block("Likely VR / XR apps", vr)
    block("Other third-party apps", rest)
    print(
        "\nExtract one with:  quest_extract.py -p <package> -o ./Games"
        "\nPull a full backup with:  quest_extract.py --backup -o ./Games"
    )


def apk_paths(serial: str, pkg: str) -> list[str]:
    """Remote paths of every APK for the package (1 = normal, >1 = split)."""
    out = run(adb_prefix(serial) + ["shell", "pm", "path", pkg], timeout=60)
    if out.returncode != 0 or "package:" not in out.stdout:
        return []
    return [
        ln.split(":", 1)[1].strip()
        for ln in out.stdout.splitlines()
        if ln.strip().startswith("package:")
    ]


def pull(serial: str, remote: str, local: Path) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    res = run(adb_prefix(serial) + ["pull", remote, str(local)], timeout=1800)
    if res.returncode == 0 and local.exists() and local.stat().st_size > 0:
        return True
    print(f"    ! could not pull {remote}", file=sys.stderr)
    if res.stderr.strip():
        print(f"      {res.stderr.strip()}", file=sys.stderr)
    return False


def human(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def extract(serial: str, pkg: str, out_root: Path, with_data: bool) -> int:
    paths = apk_paths(serial, pkg)
    if not paths:
        print(f"error: {pkg} is not installed on the headset.", file=sys.stderr)
        return 2

    dest = out_root / pkg
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    print(f"{pkg}")
    print(f"  -> {dest}")

    # --- the APK itself -------------------------------------------------
    base = Path(paths[0]).name
    apk_local = dest / f"{pkg}.apk"
    print(f"\n  APK ({len(paths)} file{'s' if len(paths) > 1 else ''}):")
    if not pull(serial, paths[0], apk_local):
        return 2
    print(f"    + {apk_local.name}  ({human(apk_local.stat().st_size)})")

    if len(paths) > 1:
        # Split APKs cannot be handed to FramePort as-is; the splits hold
        # config and the ARM64 libraries respectively.
        splits = dest / "_splits"
        splits.mkdir()
        for p in paths[1:]:
            pull(serial, p, splits / Path(p).name)
        print(
            f"    ! This app is a SPLIT APK ({len(paths)} parts). FramePort "
            "needs one merged APK.\n"
            f"      The parts are in {splits}. Merge them first:\n"
            f"        apktool d {splits} -o merged && apktool b merged -o {pkg}-merged.apk\n"
            "      then copy that APK into this folder and delete _splits/."
        )

    # --- OBB / expansion data ------------------------------------------
    obb_remote = OBB_REMOTE.format(pkg=pkg)
    listing = run(adb_prefix(serial) + ["shell", "ls", "-1", obb_remote], timeout=60)
    if listing.returncode == 0 and listing.stdout.strip():
        obb_dir = dest / pkg
        print("\n  Game data:")
        if pull(serial, f"{obb_remote}/.", obb_dir):
            files = sorted(obb_dir.rglob("*"))
            total = sum(f.stat().st_size for f in files if f.is_file())
            for f in files:
                if f.is_file():
                    print(f"    + {f.name}  ({human(f.stat().st_size)})")
            print(f"    = {len([f for f in files if f.is_file()])} files, {human(total)}")
    else:
        print("\n  Game data: none (no OBB folder)")

    # --- private app data ----------------------------------------------
    if with_data:
        data_remote = DATA_REMOTE.format(pkg=pkg)
        ls = run(adb_prefix(serial) + ["shell", "ls", "-1", data_remote], timeout=60)
        if ls.returncode == 0 and ls.stdout.strip():
            print("\n  App data:")
            pull(serial, f"{data_remote}/.", dest / pkg / "data")
        else:
            print("\n  App data: (none, app may never have written any)")

    print(
        f"\nDone. In FramePort: Add games -> Scan a folder -> pick "
        f"{out_root.resolve()}\n"
        "Check the game page for a 'WARNINGS' badge before installing."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--package", help="package name to extract")
    ap.add_argument("-o", "--out", type=Path, default=Path("Games"), help="output root (default ./Games)")
    ap.add_argument("-s", "--serial", help="adb serial, if several devices are attached")
    ap.add_argument("--wifi", metavar="IP:PORT", help="e.g. 192.168.1.50:5555 - adb connect first")
    ap.add_argument("--list", action="store_true", help="list installed third-party packages")
    ap.add_argument("--data", action="store_true", help="also pull Android/data/<pkg> (saves, config)")
    args = ap.parse_args(argv)

    if args.wifi:
        res = run(["connect", args.wifi], timeout=30)
        if res.returncode != 0 or "unable to connect" in (res.stdout + res.stderr):
            sys.exit(f"adb connect {args.wifi} failed:\n{res.stdout}{res.stderr}")

    serial = require_device(args.serial)

    if args.list:
        list_packages(serial)
        return 0

    if not args.package:
        ap.error("give -p <package>, or --list to see what is installed")

    return extract(serial, args.package, args.out, args.data)


if __name__ == "__main__":
    raise SystemExit(main())