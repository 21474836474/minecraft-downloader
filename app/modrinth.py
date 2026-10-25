"""Modrinth API 客户端。"""
from __future__ import annotations

import json
from typing import Optional

from .net import Downloader

BASE = "https://api.modrinth.com/v2"

LOADER_MAP = {
    "Fabric": "fabric",
    "Forge": "forge",
    "NeoForge": "neoforge",
    "Quilt": "quilt",
    "原版": None,
}

PROJECT_TYPES = {
    "mod": "mod",
    "modpack": "modpack",
    "shader": "shader",
    "resourcepack": "resourcepack",
    "datapack": "datapack",
}


class Modrinth:
    def __init__(self, dl: Downloader):
        self.dl = dl

    def search(self, query: str, ptype: str = "mod", limit: int = 20, offset: int = 0,
               game_version: str = "", loader: str = "", sort: str = "relevance") -> dict:
        facets = [[f"project_type:{ptype}"]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        if loader:
            facets.append([f"categories:{loader}"])
        params = {
            "query": query or "",
            "limit": limit,
            "offset": offset,
            "index": sort,
            "facets": json.dumps(facets),
        }
        return self.dl.get_json(BASE + "/search", params=params, timeout=30)

    def project(self, pid: str) -> dict:
        return self.dl.get_json(f"{BASE}/project/{pid}", timeout=30)

    def versions(self, pid: str, game_version: str = "", loader: str = "") -> list[dict]:
        params = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        data = self.dl.get_json(f"{BASE}/project/{pid}/version", params=params, timeout=30)
        return data if isinstance(data, list) else []

    def version(self, vid: str, mod_id: Optional[str] = None) -> dict:
        return self.dl.get_json(f"{BASE}/version/{vid}", timeout=30)

    def version_file(self, ver: dict) -> Optional[dict]:
        files = ver.get("files") or []
        for f in files:
            if f.get("primary"):
                return f
        return files[0] if files else None

    def file_by_id(self, mod_id: str, file_id: str) -> dict:
        return self.version(file_id)

    def best_version(self, pid: str, game_version: str, loader: str) -> Optional[dict]:
        vers = self.versions(pid, game_version, loader)
        if not vers and game_version:
            vers = self.versions(pid, game_version, "")
        if not vers:
            vers = self.versions(pid)
        return vers[0] if vers else None


def version_label(ver: dict) -> str:
    name = ver.get("name") or ver.get("version_number") or ""
    gv = ",".join(ver.get("game_versions") or [])
    lo = ",".join(ver.get("loaders") or [])
    return f"{name}   [{gv} | {lo}]"
