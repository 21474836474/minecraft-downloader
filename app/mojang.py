"""Mojang 版本清单 / 原版实例安装。"""
from __future__ import annotations

import json
import os
import platform
import shutil
import time
from typing import Callable, Optional

from . import config
from .net import Downloader, Progress, human_size

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MANIFEST_TTL = 30 * 60


def link_or_copy(src: str, dst: str) -> bool:
    """优先硬链接（同盘秒完成），失败则复制。"""
    if os.path.exists(dst):
        try:
            if os.path.getsize(dst) == os.path.getsize(src):
                return True
        except OSError:
            pass
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.link(src, dst)
        return True
    except OSError:
        try:
            shutil.copyfile(src, dst)
            return True
        except OSError:
            return False


def _rule_allows(lib: dict) -> bool:
    rules = lib.get("rules")
    if not rules:
        return True
    allowed = False
    for rule in rules:
        action = rule.get("action")
        cond = rule.get("os") or {}
        match = True
        if cond:
            name = cond.get("name")
            if name and name != "windows":
                match = False
            arch = cond.get("arch")
            if arch and arch not in ("x86_64", "amd64", "x64"):
                match = False
            ver = cond.get("version")
            if ver:
                match = False  # 不启用依赖特定系统版本的反向规则
        feats = rule.get("features") or {}
        if feats:
            # 仅当所需特性为 false 的规则才匹配（如 quickPlay 关闭）
            if any(bool(v) for v in feats.values()):
                match = False
        if match:
            allowed = (action == "allow")
    return allowed


def _os_name() -> str:
    return {"Windows": "windows", "Darwin": "osx"}.get(platform.system(), "linux")


class Mojang:
    def __init__(self, dl: Downloader, log: Callable[[str], None] = print):
        self.dl = dl
        self.log = log
        self._manifest: Optional[dict] = None

    # ---------- 清单 ----------
    def manifest(self, force: bool = False) -> dict:
        cache = os.path.join(config.cache_dir(), "version_manifest_v2.json")
        if not force and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < MANIFEST_TTL:
            try:
                with open(cache, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        data = self.dl.get_json(MANIFEST_URL, timeout=40)
        try:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass
        self._manifest = data
        return data

    def versions(self) -> list[dict]:
        return list(self.manifest().get("versions", []))

    def latest(self) -> dict:
        return self.manifest().get("latest", {})

    def version_meta(self, vid: str) -> dict:
        cache = os.path.join(config.cache_dir(), "meta", vid + ".json")
        if os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        entry = next((v for v in self.versions() if v["id"] == vid), None)
        if not entry:
            raise RuntimeError(f"未找到版本 {vid}")
        data = self.dl.get_json(entry["url"], timeout=40)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return data

    # ---------- 安装 ----------
    def install(self, inst_dir: str, vid: str, include_assets: bool = True,
                include_natives: bool = True) -> dict:
        meta = self.version_meta(vid)
        os.makedirs(inst_dir, exist_ok=True)
        vdir = os.path.join(inst_dir, "versions", vid)
        os.makedirs(vdir, exist_ok=True)

        self.log(f"安装 {vid}：客户端主程序")
        self.dl.download(meta["downloads"]["client"]["url"],
                         os.path.join(vdir, vid + ".jar"),
                         meta["downloads"]["client"].get("sha1"),
                         meta["downloads"]["client"].get("size"),
                         f"{vid}.jar")

        with open(os.path.join(vdir, vid + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 依赖库
        self.log(f"安装 {vid}：依赖库")
        lib_items, native_items = self._library_items(inst_dir, meta)
        self.dl.download_many(lib_items, "依赖库")
        if include_natives and native_items:
            self.dl.download_many(native_items, "原生库")
            self._extract_natives(inst_dir, vid, meta)

        # 资源文件
        if include_assets:
            self._install_assets(inst_dir, vid, meta)

        return meta

    def _library_items(self, inst_dir: str, meta: dict):
        items, natives = [], []
        os_name = _os_name()
        libroot = os.path.join(inst_dir, "libraries")
        for lib in meta.get("libraries", []):
            if not isinstance(lib, dict) or not _rule_allows(lib):
                continue
            downloads = lib.get("downloads") or {}
            art = downloads.get("artifact")
            if art and art.get("path"):
                items.append({
                    "url": art["url"],
                    "dest": os.path.join(libroot, art["path"]),
                    "sha1": art.get("sha1"),
                    "size": art.get("size"),
                    "label": os.path.basename(art["path"]),
                })
            natives_map = lib.get("natives") or {}
            key = natives_map.get(os_name)
            if key:
                key = key.replace("${arch}", "64")
                cl = (downloads.get("classifiers") or {}).get(key)
                if cl and cl.get("path"):
                    natives.append({
                        "url": cl["url"],
                        "dest": os.path.join(libroot, cl["path"]),
                        "sha1": cl.get("sha1"),
                        "size": cl.get("size"),
                        "label": os.path.basename(cl["path"]),
                    })
        return items, natives

    def _extract_natives(self, inst_dir: str, vid: str, meta: dict):
        import zipfile
        os_name = _os_name()
        out = os.path.join(inst_dir, "natives", vid)
        os.makedirs(out, exist_ok=True)
        libroot = os.path.join(inst_dir, "libraries")
        for lib in meta.get("libraries", []):
            if not isinstance(lib, dict) or not _rule_allows(lib):
                continue
            key = (lib.get("natives") or {}).get(os_name)
            if not key:
                continue
            key = key.replace("${arch}", "64")
            cl = ((lib.get("downloads") or {}).get("classifiers") or {}).get(key)
            if not cl or not cl.get("path"):
                continue
            jar = os.path.join(libroot, cl["path"])
            if not os.path.exists(jar):
                continue
            try:
                with zipfile.ZipFile(jar) as z:
                    for name in z.namelist():
                        if name.startswith("META-INF/") or name.endswith("/"):
                            continue
                        z.extract(name, out)
            except (OSError, zipfile.BadZipFile):
                pass

    def _install_assets(self, inst_dir: str, vid: str, meta: dict):
        ai = meta.get("assetIndex")
        if not ai:
            return
        index_id = ai["id"]
        adir = os.path.join(inst_dir, "assets")
        idx_path = os.path.join(adir, "indexes", index_id + ".json")
        os.makedirs(os.path.dirname(idx_path), exist_ok=True)
        self.dl.download(ai["url"], idx_path, ai.get("sha1"), ai.get("size"), f"assets/{index_id}.json")
        with open(idx_path, "r", encoding="utf-8") as f:
            index = json.load(f)
        objects = index.get("objects") or {}
        self.log(f"安装 {vid}：资源文件 {len(objects)} 个 / {human_size(ai.get('totalSize', 0))}")

        cache = os.path.join(config.cache_dir(), "assets")
        items = []
        for rel, info in objects.items():
            h = info["hash"]
            dst = os.path.join(adir, "objects", h[:2], h)
            src = os.path.join(cache, h[:2], h)
            if os.path.exists(src) and os.path.getsize(src) == info.get("size", -1):
                link_or_copy(src, dst)
                continue
            items.append({
                "url": f"https://resources.download.minecraft.net/{h[:2]}/{h}",
                "dest": src,
                "sha1": h,
                "size": info.get("size"),
                "label": os.path.basename(rel),
            })
        if items:
            self.dl.download_many(items, "资源文件")
            for it in items:
                dst = os.path.join(adir, "objects", it["sha1"][:2], it["sha1"])
                if os.path.exists(it["dest"]):
                    link_or_copy(it["dest"], dst)

        # 旧版本虚拟资源目录
        if index.get("virtual") or index.get("map_to_resources"):
            base = os.path.join(adir, "virtual", index_id) if index.get("virtual") else os.path.join(inst_dir, "resources")
            for rel, info in objects.items():
                h = info["hash"]
                link_or_copy(os.path.join(adir, "objects", h[:2], h), os.path.join(base, rel))

        # 日志配置
        logging_cfg = (meta.get("logging") or {}).get("client")
        if logging_cfg and logging_cfg.get("file"):
            f = logging_cfg["file"]
            self.dl.download(f["url"], os.path.join(adir, "log_configs", f["id"]),
                             f.get("sha1"), f.get("size"), f["id"], optional=True)


def version_choices(manifest: dict, include_snapshots: bool = True,
                    include_old: bool = False) -> list[dict]:
    out = []
    for v in manifest.get("versions", []):
        t = v.get("type")
        if t == "release" or (include_snapshots and t == "snapshot") or (include_old and t in ("old_beta", "old_alpha")):
            out.append(v)
    return out
