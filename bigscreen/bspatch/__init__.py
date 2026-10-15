"""Patches that make Bigscreen Beta (Quest build) usable on the Valve Steam Frame.

`code_patches` rewrites libil2cpp.so, `asset_patches` edits data.unity3d; both
check fingerprints of the exact build they were written for before changing a
byte. `build_bigscreen.py` (one directory up) runs everything.
"""

SUPPORTED_APK_SHA256 = '6cae96955397be3522c238196635a061c5f9da465f032cbe34b817dddbb4a18a'
SUPPORTED_VERSION = '0.950.2.a276a2-beta-perma-rooms-beta (versionCode 4688857)'
PACKAGE = 'com.bigscreenvr.bigscreen'
