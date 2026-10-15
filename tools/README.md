# General Quest → Steam Frame tools

FramePort ([frameport.app](https://frameport.app)) does the porting. These tools handle what it
leaves to you: getting the APK and deciding whether it is worth porting.

```
Meta store ──quest_store_fetch.mjs──►  Games/<pkg>/  ──FramePort──►  Steam Frame
   (entitled app)                          │
   OR                             apk_preflight.py says whether it will run
   Quest (yours) ──quest_extract.py──►      │
```

FramePort does not download, share or unlock games; it only installs files you supply.
`quest_store_fetch.mjs` only fetches apps your Meta account is entitled to.

## `quest_store_fetch.mjs`: download an app from the Meta store

Plain Node.js (18+) client for Meta's store, built on AXRB's `meta.mjs`
([vendor/axrb/README.md](vendor/axrb/README.md): licence and the "undocumented API" caveat).

```bash
node tools/quest_store_fetch.mjs --login          # prints a sign-in URL
node tools/quest_store_fetch.mjs --callback "<oculus://...>"
node tools/quest_store_fetch.mjs --list           # your entitled Quest apps
node tools/quest_store_fetch.mjs --app 2497738113633933 --dry-run
node tools/quest_store_fetch.mjs --app 2497738113633933
```

Downloads resume via HTTP Range, are checked against Meta's reported size and hashed with
SHA-256. The token is stored `chmod 600` under `~/.config/steam_frame/` and never printed.

- **Meta returns 404, not 403,** for apps your account is not entitled to. For a free app, open it
  on meta.com and press **Get** first.
- **The sign-in challenge lasts 10 minutes.** `--login` saves it so `--callback` can come later;
  re-run `--login` if it expires.

## `quest_extract.py`: get the APK off a Quest

Pulls an installed app into the folder shape FramePort scans (one folder per game, the APK
inside, game data in a subfolder named exactly like the package). Handles split APKs and warns
when they need merging.

```bash
python3 tools/quest_extract.py --list
python3 tools/quest_extract.py -p com.bigscreenvr.bigscreen
python3 tools/quest_extract.py -p <pkg> --wifi 192.168.1.50:5555
```

Needs `adb`, developer mode on the headset and the accepted USB-debugging prompt.

## `apk_preflight.py`: is it worth porting?

Static APK analysis, no headset needed (needs `androguard`).

```bash
python3 tools/apk_preflight.py Games/*/*.apk
python3 tools/apk_preflight.py Games/*/*.apk -o preflight.json
```

Verdicts: `PORTABLE` / `NEEDS PATCHING` / `BLOCKED` (exit code 1 if any APK is blocked).
Findings are graded `BLOCKER` (cannot run, no patch can fix it), `WARN` (probably needs a
patch) and `INFO`. The most important blocker is `no-arm64`: the Frame is ARM64-only with no
AArch32 support, so a 32-bit-only Quest build cannot run.
