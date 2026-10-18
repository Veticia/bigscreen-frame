# Bigscreen Beta on the Steam Frame

Patches that make the Meta Quest build of **[Bigscreen Beta](https://www.bigscreenvr.com/)** work on the
**Valve Steam Frame**, installed with **[FramePort](https://frameport.app)**
([source](https://github.com/spoopyghosty0/frameport)).

This repository contains **no Bigscreen code or assets**. It is a set of
fingerprint-checked patch scripts: you supply your own copy of the free Quest APK,
the script turns it into a Frame-ready APK, and FramePort installs it.

## What works

Tested on a Steam Frame (Lepton, Mesa/Zink GLES) with Bigscreen Beta
`0.950.2.a276a2-beta-perma-rooms-beta` (versionCode 4688857):

| Feature | Status |
|---|---|
| Bigscreen account login (PC login flow, no Meta account needed) | ✅ |
| Private and public rooms | ✅ |
| Cloud browser / YouTube: floating window and the room's main screen | ✅ |
| Local TV channels on the main screen, mirror screens and ambient room light | ✅ |
| TV channel subtitles (CEA-608) | ✅ |
| Peer / room screen sharing, including side-by-side and over-under **3D** | ✅ |
| Local videos from a DLNA media server on the main screen, including over-under **3D** | ✅ |
| Screens correctly hidden behind avatars, hands and props (in-scene rendering) | ✅ |
| Switching TV channels / some streams | ⚠️ errors, cause under investigation, see [Known issues](#known-issues) |
| Movie rentals | ❓ untested |

## Quick start

You need Python 3.10+, about 3.5 GB of free disk space, a FramePort-set-up Steam Frame,
and the original Bigscreen Beta APK (free on the Meta store; see
[bigscreen/README.md](bigscreen/README.md#1-get-the-original-apk) for two ways to get it).

```bash
pip install -r requirements.txt
python3 bigscreen/build_bigscreen.py com.bigscreenvr.bigscreen.apk Games/com.bigscreenvr.bigscreen-frame/com.bigscreenvr.bigscreen.apk
```

Then install the output with FramePort. The full walkthrough, FramePort settings and
troubleshooting are in **[bigscreen/README.md](bigscreen/README.md)**.

The script only accepts the exact supported APK (sha256 `6cae9695…4a18a`). It checks every
patch site before changing anything and verifies the result. The output is
byte-identical to the build that was tested on the headset.

## Known issues

Problems seen on the headset. Their causes are not determined yet; the evidence collected so far
is in [docs/OPEN_ISSUES.md](docs/OPEN_ISSUES.md).

- Switching to another TV channel shows *"There was an error loading the content"*.
- In one session with such errors, joining an almost full public room froze the app.
- Some streams do not load; the log shows an *HTTP 403* response for them.
- In the browser, a controller click sometimes registers as click-and-drag.

## Repository layout

| Path | What |
|---|---|
| [`bigscreen/`](bigscreen/) | **The patcher.** `build_bigscreen.py` + the `bspatch` package (code and asset patches) |
| [`docs/PATCHES.md`](docs/PATCHES.md) | Every patch: symptom, root cause, fix, how it was verified |
| [`docs/DEVLOG.md`](docs/DEVLOG.md) | How the port got here, build by build, including the dead ends |
| [`docs/FRAMEPORT.md`](docs/FRAMEPORT.md) | FramePort setup and settings, how Bigscreen meets the Frame's compositor |
| [`docs/OPEN_ISSUES.md`](docs/OPEN_ISSUES.md) | Unresolved problems with their log evidence and leads |
| [`docs/REVERSING.md`](docs/REVERSING.md) | How to inspect the APK yourself (Il2CppDumper, disassembly, Unity assets) |
| [`dev/`](dev/) | Reverse-engineering helpers: symbolized disassembler, APK/asset differ, shader and scene tools |
| [`tools/`](tools/) | General Quest → Frame helpers: preflight check, APK extraction from a Quest, Meta store fetch |
| [`archive/`](archive/) | The original step-by-step patch scripts from the investigation (historical, not maintained) |
| [`tests/`](tests/) | `python3 -m unittest discover -s tests` (no Bigscreen files needed) |

## Contributing

Bug reports with a FramePort diagnostics zip (Bigscreen's `…` menu → *Report a problem*) or a
session log are the most useful thing. To work on a patch, start with
[docs/PATCHES.md](docs/PATCHES.md) and [docs/REVERSING.md](docs/REVERSING.md). Every patch keeps its
fingerprint (the original words it expects), and a change is done when
`dev/apk_diff.py` shows exactly the intended differences and the tests pass.

A new Bigscreen release moves every address, so supporting it means re-deriving each patch
site. The docs describe each site by method name, so it can be found again in the new build.

## Legal

Bigscreen Beta is free on the Meta store. Use these patches only on a copy you are
entitled to, on hardware you own. The repository distributes no Bigscreen files. Bigscreen
is a trademark of Bigscreen, Inc.; this project is not affiliated with Bigscreen, Valve,
Meta or FramePort.

Licensed under the **GNU GPL v3.0 or later** ([LICENSE](LICENSE)).
`tools/vendor/axrb/meta.mjs` is vendored from AXRB (GPL-3.0); see its README.
