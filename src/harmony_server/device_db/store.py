"""Read-only access layer over the vendored Harmony IR archive (schemaVersion 2).

Layout (from the archive's ``manifest.json``):

    index.json                          [{"n": name, "s": slug, "c": model count}]
    devices/<slug>/index.json           [{"f": file, "id": globalDeviceId, "m": model|null}]
    devices/<slug>/<file>               device record, ``codeset`` = relative path or null
    codesets/<xx>/<hash>.json           {"commands": [...]}
    protocols/index.json                [{"n": name, "f": file, "id", "carrierHz", ...}]
    protocols/<file>

Only the two top-level indexes are held in memory (~8k + ~700 entries). Everything else
is read on demand with aiofiles. All path inputs are resolved through an index, never
joined from user input, so traversal is impossible by construction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiofiles

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA = 2


class DeviceDBUnavailable(RuntimeError):
    pass


async def _read_json(path: Path) -> Any:
    async with aiofiles.open(path, encoding="utf-8") as fh:
        return json.loads(await fh.read())


class DeviceDB:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest: dict[str, Any] = {}
        self.manufacturers: list[dict[str, Any]] = []
        self._mfr_by_key: dict[str, dict[str, Any]] = {}
        self.protocols: list[dict[str, Any]] = []
        self._proto_by_key: dict[str, dict[str, Any]] = {}
        self._model_index_cache: dict[str, list[dict[str, Any]]] = {}
        self.available = False

    async def load(self) -> None:
        manifest = self.root / "manifest.json"
        if not manifest.is_file():
            log.warning("Device DB not found at %s - /api/devices will return 503", self.root)
            return
        self.manifest = await _read_json(manifest)
        schema = self.manifest.get("schemaVersion")
        if schema != SUPPORTED_SCHEMA:
            log.warning("Device DB schemaVersion %s (expected %s)", schema, SUPPORTED_SCHEMA)
        self.manufacturers = await _read_json(self.root / "index.json")
        for m in self.manufacturers:
            self._mfr_by_key[m["s"].lower()] = m
            self._mfr_by_key.setdefault(m["n"].lower(), m)
        proto_index = self.root / "protocols" / "index.json"
        if proto_index.is_file():
            self.protocols = await _read_json(proto_index)
            for p in self.protocols:
                self._proto_by_key[p["n"].lower()] = p
                self._proto_by_key.setdefault(p["f"].removesuffix(".json").lower(), p)
        self.available = True
        log.info(
            "Device DB loaded: %d manufacturers, %d protocols (generated %s)",
            len(self.manufacturers),
            len(self.protocols),
            self.manifest.get("generated"),
        )

    def _require(self) -> None:
        if not self.available:
            raise DeviceDBUnavailable("Device DB is not installed; see device-db/README.md")

    # ------------------------------------------------------------------ manufacturers
    def search_manufacturers(self, q: str | None, limit: int = 50) -> list[dict[str, Any]]:
        self._require()
        if not q:
            items = self.manufacturers
        else:
            needle = q.strip().lower()
            items = [m for m in self.manufacturers if needle in m["n"].lower()]
            # exact > prefix > substring, then by catalogue size
            items.sort(
                key=lambda m: (
                    m["n"].lower() != needle,
                    not m["n"].lower().startswith(needle),
                    -int(m.get("c", 0)),
                )
            )
        return [
            {"name": m["n"], "slug": m["s"], "modelCount": m.get("c", 0)} for m in items[:limit]
        ]

    def manufacturer(self, key: str) -> dict[str, Any] | None:
        self._require()
        return self._mfr_by_key.get(key.strip().lower())

    async def models(self, manufacturer: str) -> list[dict[str, Any]] | None:
        mfr = self.manufacturer(manufacturer)
        if mfr is None:
            return None
        slug = mfr["s"]
        if slug not in self._model_index_cache:
            path = self.root / "devices" / slug / "index.json"
            if not path.is_file():
                return []
            entries = await _read_json(path)
            if len(self._model_index_cache) > 256:
                self._model_index_cache.clear()
            self._model_index_cache[slug] = entries
        return self._model_index_cache[slug]

    async def device(
        self, manufacturer: str, model: str, inline_codeset: bool = True
    ) -> dict[str, Any] | None:
        entries = await self.models(manufacturer)
        if not entries:
            return None
        mfr = self.manufacturer(manufacturer)
        assert mfr is not None
        want = model.strip()
        want_l = want.lower()
        match = None
        for e in entries:
            m = (e.get("m") or "").strip()
            if (
                e["f"] == want
                or e["f"].removesuffix(".json") == want
                or m == want
                or str(e.get("id")) == want
            ):
                match = e
                break
        if match is None:
            match = next((e for e in entries if (e.get("m") or "").strip().lower() == want_l), None)
        if match is None:
            return None
        record: dict[str, Any] = await _read_json(self.root / "devices" / mfr["s"] / match["f"])
        if inline_codeset and record.get("codeset"):
            codeset = await self.codeset(record["codeset"])
            if codeset is not None:
                record["commands"] = codeset.get("commands", [])
        return record

    async def codeset(self, rel_path: str) -> dict[str, Any] | None:
        self._require()
        path = (self.root / rel_path).resolve()
        if not path.is_relative_to(self.root / "codesets") or not path.is_file():
            return None
        data: dict[str, Any] = await _read_json(path)
        return data

    # ------------------------------------------------------------------ protocols
    async def protocol(self, name: str) -> dict[str, Any] | None:
        self._require()
        entry = self._proto_by_key.get(name.strip().lower().removesuffix(".json"))
        if entry is None:
            entry = self._proto_by_key.get(name.strip().lower().replace("_", " "))
        if entry is None:
            return None
        data: dict[str, Any] = await _read_json(self.root / "protocols" / entry["f"])
        data.setdefault("name", entry["n"])
        return data
