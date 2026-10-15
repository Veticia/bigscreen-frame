# Vendored third-party source

## `meta.mjs`

Verbatim copy of `launcher/core/meta.mjs` from **Android XR Bridge (AXRB)**
<https://github.com/Android-XR-Bridge/AXRB> (branch `windows-android16`).

That file is in turn adapted from **RiftLift** `meta_auth.py`, which AXRB's
header comment credits as `GPL-3.0-or-later`. AXRB's own `launcher/LICENSE` is
GPL-3.0 (35149 bytes, the stock GPL-3.0 text).

Consequences for this repository:

- `tools/quest_store_fetch.mjs`, which imports this file, is **GPL-3.0**.
- The file is unmodified. Re-sync it with:

  ```bash
  curl -sSL -o tools/vendor/axrb/meta.mjs \
    https://raw.githubusercontent.com/Android-XR-Bridge/AXRB/windows-android16/launcher/core/meta.mjs
  ```

### What this file is

A client for Meta's **undocumented** Horizon store APIs. Per AXRB's own
documentation: *"Meta APIs used by community launchers are undocumented and may
change."* Any of the endpoints, document ids or the client token below may stop
working without notice.

It is scoped here to a **single free app** (Bigscreen Beta). No bulk download, no
entitlement bypass, no redistribution. Bigscreen is free on the Meta store, so no
purchase or DRM gate is involved.