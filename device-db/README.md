# device-db/

Vendored snapshot of the Harmony IR archive (manifest `schemaVersion` 2, generated
2026-11-10): 7,908 manufacturers, 278,220 devices, 54,610 code sets, 684 protocols.

* Source: <https://github.com/pickysysadmin/logitech-harmony-ir-archive>, pinned to the
  commit in `ARCHIVE_COMMIT`.
* License: CC0-1.0 for the curation/Pronto/schema (see `LICENSE` once fetched). The
  underlying codes come from Logitech; their copyright status is unresolved.
* The archive keeps its layout at the repository root (`manifest.json`, `index.json`,
  `devices/`, `codesets/`, `protocols/`), not under `docs/api/v1/`.

The data (~2.3 GB on disk, ~340k files) is **not committed** to this repository. Fetch it:

```sh
scripts/fetch-device-db.sh
```

The server runs without it; `/api/devices/*` then returns 503.
