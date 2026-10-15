# BigScreen on Steam Frame — reversing notes (phase B)

Build: `com.bigscreenvr.bigscreen` v0.950.2.a276a2-beta, versionCode 4688857
(the supported APK: sha256 `6cae96955397be35…`, 1 175 485 713 bytes, see
[bigscreen/README.md](../../bigscreen/README.md#1-get-the-original-apk)).

Everything here was produced by reading this exact APK: strings from
`global-metadata.dat`, method map from Il2CppDumper (`dump.cs` + `script.json`),
ARM64 disassembly of `libil2cpp.so` with capstone. IL2CPP metadata ids are
**offset by 6600** in script.json (e.g. TypeDefIndex 6624 → script entry 13224);
RVA values match the file offsets in the stripped .so exactly.

## Symptom (from the already-captured FramePort session log)

```
06:52:21.420  Unity: OCULUS PLATFORM INITIALIZATION SUCCESS  ← 45%→50% fine
06:52:21.433  Unity: Fetch user success        ← user fetch SUCCEEDED
06:52:21.434  Unity: An unknown error occurred. Request failed.
              ← the NEXT platform call never gets an answer → 55% forever
06:52→06:55   FrameBridge: pacing 72.2 fps     ← app alive, only login stalled
```

"Logging in: **55%**" = the **CHECK_OCULUS_ENTITLEMENT_PERCENT** step
(static float fields at 0x330…0x340 in `Constants`, TypeDefIndex 10986).

## The login ladder (all named in dump.cs)

`BIG_APP_LOADER` (namespace `Bigscreen`) drives it:
`Start → CheckInternetConnection → InitializeOculusPlatform → GetOculusUser →
AttemptLogin → OnLogInSuccess → ConnectToServerAndGoHome`.

Progress constants (static floats): `INIT_OCULUS_PLATFORM_PERCENT` 0x330,
`GET_OCULUS_USER_PERCENT` 0x334, **`CHECK_OCULUS_ENTITLEMENT_PERCENT` 0x338**,
`LOG_IN_PERCENT` 0x33C, `CONNECT_SERVER_PERCENT` 0x340.

A second, independent checker also exists:
`Oculus.Platform.Samples.EntitlementCheck : MonoBehaviour`
(start 0x10804C4, callback 0x1080968, handler 0x108068C) — the stock Meta sample
that calls `ovr_User_GetUserEntitlement` via `ovr_User_GetEntitlement()…`
(0x1080594 → 0x1077F70 `ovr_User_GetLoggedInUser`; entitlement request path
around 0x1077F70 calls the raw platform API).

## Verified native evidence (disassembly)

- `InitOculusPlatform` (BIG_APP_LOADER b__12_0) **succeeds** — the log text and
  the 45→50→55% progress agree.
- `b__14_0` (the `GetOculusUser` result callback) checks
  `IsError(Message)` (call 0x1346FB4 → bool), and on the failure branch
  (b89d00) calls `ovrplatformloader$$ovr_GetErrorMessage`, sets a Unity
  Debug message, then schedules something (`b 0xCFE85C`).
- `EntitlementCheck$$EntitlementCheckCallback` @0x1080968:
  - `mov x0, x1 (the Message)`; `bl 0x1346fb4` = `Message.get_IsError()`
  - `mvn w8, w0` then `and w1, w8, #1` → invert into a bool
  - `b 0x108068c` = `HandleEntitlementCheckResult(bool)`
- `HandleEntitlementCheckResult` on success (w20 bit0 set): Unity debug-log +
  invoke `UserPassedEntitlementCheck` (static at 0x2F5D000+8).  On failure
  (w20==0): logs + fault branch at 0x1080778: **if `this.exitAppOnFailure`
  (field 0x18) is nonzero, it calls `Application.Quit`-equivalent
  (0x1860E60) — the "Oculus user entitlement check failed. Exiting now."
  path.**

## What actually blocks on Frame

On Quest, `EntitlementCheckCallback` is invoked by the platform message pump
(`ovr_PopMessage` loop in the Unity `CallbackRunner`/`Oculus.Platform.Core`)
when the async platform call resolves. Meta's services don't exist on the
Frame, so `ovr_User_GetUserEntitlement` puts the request in queue and nothing
**ever** resolves. `EntitlementCheck$$Start` registers the callback and returns;
**nobody ever answers**; the loader's ladder stays at `CHECK_OCULUS_
ENTITLEMENT_PERCENT = 55%` forever. `Fetch user success` succeeded because that
particular call is answered locally by OVRPort's platform loader.

## The patch (three lines of AArch64)

Find the `bl 0x1077F70` inside `EntitlementCheck$$Start` at **0x1080594** —
that's `ovr_User_GetLoggedInUser`'s un-completed flow leading to the
entitlement request (actually `ovr_User_GetUserEntitlement`,
Platform unity wrapper `Oculus.Platform.User$$GetEntitlement`). After it
returns a request id, `Start` at 0x108059C calls 0x1079F50
(`ovrplatformloader$$Request?.` — likely `CallbackRunner`/`ovr_UnitySet
LaunchDetails` bookkeeping) and then constructs the CallbackRunner delegate.

**Minimal patch:** when that call returns (`0x108059C`), immediately return
**success** by calling `HandleEntitlementCheckResult(true)` and `ret`:

```
0x108059C:  mov w1, #1
0x10805A0:  b 0x108068C        ; HandleEntitlementCheckResult(true)
```

…or, MSP-style, call `UserPassedEntitlementCheck!.Invoke()` by simply
storing `this.exitAppOn_FAILURE=0`… simplest verified patch:

- At 0x1080594 (inside `EntitlementCheck$$Start`), replace the call to the
  entitlement request (5 bytes) with:
  - `mov w1, #1`  (arg1=true)
  - `b 0x108068C` (direct call to HandleEntitlementCheckResult)
- Verify first: read 4 bytes at 0x1080594 (should be `bl 0x1077F70`
  encoding `0x94000000 | (offset>>2)`) before writing; abort if not.

### Why that's safe

- `CheckInternetConnection` ran BEFORE this and the app already
  displayed "Fetching Oculus user" → the ladder's 50% came from `b__12_0`
  answered by OVRPort; the app *expects* an early-success world.
- If the check returns success, the sample code invokes
  `UserPassedEntitlementCheck` — which the loader waits on.
- No server bypass: nothing about Bigscreen's own account/network is touched;
  it just pretends the Meta-side entitlement call completed. The user still
  needs a **Bigscreen account login** for online rooms, exactly as on PC.

## Risk notes (see also BIGSCREEN.md)

- Any offset here is specific to **this build** (versionCode 4688857). Different
  builds ≠ same offsets. Fingerprint-verify before patching: the exact
  instruction at 0x1080594 and the following `bl` targets must match.
- Anti-tamper: none found (no `SignatureChecker`-style native checks listed for
  Bigscreen); the APK is re-signed by FramePort anyway. If a future build
  starts checking signatures, this patch(re)build fails loudly rather than
  silently.
- Online goal still needs the app's own login UI to work headless (Vuplex
  WebView + keyboard) — see `frame.unity_text_input` + `device.text_input_window`.

## Files to build the patch against

- the original APK (see [bigscreen/README.md](../../bigscreen/README.md#1-get-the-original-apk))
- Il2CppDumper output `dump.cs` / `script.json` (method map) and the APK's `libil2cpp.so`
  (disassembly source); see [REVERSING.md](../REVERSING.md)

The patches described here are `entitlement` and `pc-login` in
[bigscreen/bspatch/code_patches.py](../../bigscreen/bspatch/code_patches.py).
