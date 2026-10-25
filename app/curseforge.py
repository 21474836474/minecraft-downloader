"""CurseForge 渠道（通过 MCIM 镜像的 CurseForge API，无需 API Key）。

文件下载走 ForgeCDN 的 mediafilez / edge 双域名。
"""
from __future__ import annotations

import urllib.parse
import re
from typing import Optional

from .net import Downloader

CF_MIRROR = "https://mod.mcimirror.top/curseforge/v1"
CF_OFFICIAL = "https://api.curseforge.com/v1"
GAME_ID = 432

CLASS_IDS = {
    "mod": 6,
    "modpack": 4471,
    "resourcepack": 12,
    "shader": 6552,
}

LOADER_IDS = {
    "forge": 1,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}

LOADER_NAMES = {"Forge", "Fabric", "Quilt", "NeoForge", "LiteLoader", "Cauldron", "Rift"}

CDN_TEMPLATES = (
    "https://mediafilez.forgecdn.net/files/{a}/{b}/{name}",
    "https://mediafiles.forgecdn.net/files/{a}/{b}/{name}",
    "https://edge.forgecdn.net/files/{a}/{b}/{name}",
)


def cdn_urls(fid: int, filename: str) -> list[str]:
    a, b = fid // 1000, fid % 1000
    name = urllib.parse.quote(filename)
    return [t.format(a=a, b=b, name=name) for t in CDN_TEMPLATES]


class CurseForge:
    """把 CurseForge 数据转换成与 Modrinth 兼容的结构，方便复用安装流程。"""

    def __init__(self, dl: Downloader, api_key: str = ""):
        self.dl = dl
        self.api_key = (api_key or "").strip()
        self.base = CF_OFFICIAL if self.api_key else CF_MIRROR

    def _get(self, path: str, params=None, timeout: int = 40):
        headers = {"x-api-key": self.api_key} if self.api_key else None
        r = self.dl.get(self.base + path, params=params, headers=headers, timeout=timeout)
        return r.json()

    # ---------- 搜索 ----------
    def search(self, query: str, ptype: str = "mod", limit: int = 20, offset: int = 0,
               game_version: str = "", loader: str = "", sort: str = "relevance") -> dict:
        class_id = CLASS_IDS.get(ptype, 6)
        base = {
            "gameId": GAME_ID,
            "classId": class_id,
            "pageSize": max(limit, 20),
            "index": offset,
            "sortOrder": "desc",
        }
        if game_version:
            base["gameVersion"] = game_version
        if loader and loader in LOADER_IDS:
            base["modLoaderType"] = LOADER_IDS[loader]

        tokens = [t for t in re.split(r"[\s\-–—_]+", query or "") if len(t) >= 2]
        found: dict[int, dict] = {}
        for q in self._query_variants(query):
            params = dict(base)
            params["searchFilter"] = q
            try:
                for m in (self._get("/mods/search", params=params).get("data") or []):
                    found.setdefault(m["id"], m)
            except Exception:  # noqa: BLE001
                continue
            if len(found) >= limit * 3:
                break
        if not found and query and not game_version:
            try:
                for m in (self._get("/mods/search", params={"gameId": GAME_ID, "slug": query})
                          .get("data") or []):
                    found.setdefault(m["id"], m)
            except Exception:  # noqa: BLE001
                pass

        def score(m: dict) -> tuple:
            text = ((m.get("name") or "") + " " + (m.get("slug") or "")).lower()
            hits = sum(1 for t in tokens if t.lower() in text)
            return (hits, m.get("downloadCount") or 0)

        mods = sorted(found.values(), key=score, reverse=True)
        if tokens:
            strong = [m for m in mods if score(m)[0] > 0]
            mods = strong or mods
        mods = mods[:limit]
        hits = [{
            "project_id": str(m["id"]),
            "slug": m.get("slug") or str(m["id"]),
            "title": m.get("name") or "",
            "description": (m.get("summary") or "").strip(),
            "downloads": m.get("downloadCount") or 0,
            "author": ", ".join(a.get("name", "") for a in (m.get("authors") or [])),
            "source": "curseforge",
        } for m in mods]
        return {"hits": hits}

    @staticmethod
    def _query_variants(query: str) -> list[str]:
        q = (query or "").strip()
        if not q:
            return [""]
        out = [q]
        flat = re.sub(r"[\-–—_]+", " ", q)
        if flat != q:
            out.append(flat)
        nospace = re.sub(r"\s+", "", q)
        if nospace != q:
            out.append(nospace)
        parts = [t for t in re.split(r"[\s\-–—_]+", flat) if len(t) >= 3]
        out.extend(parts[:3])
        seen, uniq = set(), []
        for x in out:
            if x and x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq[:5]

    # ---------- 项目 / 文件 ----------
    def project(self, pid: str) -> dict:
        m = self._get(f"/mods/{pid}").get("data") or {}
        kind = {6: "mod", 12: "resourcepack", 6552: "shader", 4471: "modpack"}.get(
            m.get("classId"), "mod")
        return {"id": str(m.get("id")), "title": m.get("name") or str(pid),
                "slug": m.get("slug") or str(pid), "project_type": kind}

    def _file_to_version(self, f: dict) -> dict:
        gv = [x for x in (f.get("gameVersions") or []) if x not in LOADER_NAMES]
        loaders = [x.lower() for x in (f.get("gameVersions") or []) if x in LOADER_NAMES]
        sha1 = ""
        for h in f.get("hashes") or []:
            if h.get("algo") == 1:
                sha1 = h.get("value") or ""
        deps = []
        for d in f.get("dependencies") or []:
            if d.get("relationType") == 3:
                deps.append({"project_id": str(d.get("modId")), "dependency_type": "required"})
        return {
            "id": str(f["id"]),
            "name": f.get("displayName") or f.get("fileName"),
            "version_number": f.get("displayName") or f.get("fileName"),
            "game_versions": gv,
            "loaders": loaders,
            "dependencies": deps,
            "files": [{
                "filename": f.get("fileName"),
                "url": cdn_urls(f["id"], f.get("fileName") or "")[0],
                "urls": cdn_urls(f["id"], f.get("fileName") or ""),
                "hashes": {"sha1": sha1} if sha1 else {},
                "size": f.get("fileLength"),
                "primary": True,
            }],
        }

    def versions(self, pid: str, game_version: str = "", loader: str = "") -> list[dict]:
        params = {"pageSize": 50}
        if game_version:
            params["gameVersion"] = game_version
        if loader and loader in LOADER_IDS:
            params["modLoaderType"] = LOADER_IDS[loader]
        try:
            data = self._get(f"/mods/{pid}/files", params=params)
        except Exception:  # noqa: BLE001
            return []
        files = data.get("data") or []
        if not files and (game_version or loader):
            try:
                files = (self._get(f"/mods/{pid}/files", params={"pageSize": 50})
                         .get("data") or [])
            except Exception:  # noqa: BLE001
                files = []
        return [self._file_to_version(f) for f in files if f.get("isAvailable", True)]

    def version(self, vid: str, mod_id: Optional[str] = None) -> dict:
        if not mod_id:
            raise RuntimeError("CurseForge 需要同时提供 project 与 file id")
        f = self._get(f"/mods/{mod_id}/files/{vid}").get("data") or {}
        return self._file_to_version(f)

    def best_version(self, pid: str, game_version: str, loader: str) -> Optional[dict]:
        vers = self.versions(pid, game_version, loader)
        return vers[0] if vers else None

    def version_file(self, ver: dict) -> Optional[dict]:
        files = ver.get("files") or []
        return files[0] if files else None

    def file_by_id(self, mod_id: str, file_id: str) -> dict:
        return self.version(str(file_id), str(mod_id))
