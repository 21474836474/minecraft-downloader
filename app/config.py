"""配置与实例目录管理。"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional

APP_NAME = "Minecraft下载器"


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    base = app_dir()
    try:
        p = os.path.join(base, "MCDownloader-data")
        os.makedirs(p, exist_ok=True)
        test = os.path.join(p, ".w")
        with open(test, "w") as f:
            f.write("1")
        os.remove(test)
        return p
    except OSError:
        p = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MCDownloader")
        os.makedirs(p, exist_ok=True)
        return p


def cache_dir() -> str:
    p = os.path.join(data_dir(), "cache")
    os.makedirs(p, exist_ok=True)
    return p


CONFIG_PATH = os.path.join(data_dir(), "config.json")

DEFAULTS = {
    "instance_dirs": [],
    "active_dir": "",
    "active_instance": "",
    "threads": 16,
    "limit_kbps": 0,
    "use_mirror": True,
    "default_dir": "",
    "source": "modrinth",
    "cf_api_key": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    if not cfg.get("default_dir"):
        cfg["default_dir"] = os.path.join(data_dir(), "instances")
        try:
            os.makedirs(cfg["default_dir"], exist_ok=True)
        except OSError:
            pass
    dirs = [d for d in cfg.get("instance_dirs", []) if d]
    if cfg["default_dir"] not in dirs:
        dirs.insert(0, cfg["default_dir"])
    cfg["instance_dirs"] = dirs
    cfg["threads"] = max(1, min(64, int(cfg.get("threads") or 16)))
    cfg["limit_kbps"] = max(0, int(cfg.get("limit_kbps") or 0))
    return cfg


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


META_FILE = ".mcdl-instance.json"


@dataclass
class Instance:
    name: str
    path: str
    mc_version: str = ""
    loader: str = "原版"
    loader_version: str = ""
    version_id: str = ""

    def __str__(self):
        tag = self.mc_version or "?"
        if self.loader and self.loader != "原版":
            tag += f" / {self.loader}"
            if self.loader_version:
                tag += f" {self.loader_version}"
        return f"{self.name}  ({tag})"


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _clean_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return name or "instance"


def detect_loader(inst_path: str) -> tuple[str, str, str]:
    """从 versions 目录推断 (mc版本, 加载器, 加载器版本, 版本id)。"""
    vdir = os.path.join(inst_path, "versions")
    if not os.path.isdir(vdir):
        return "", "", ""
    best = None
    for vid in os.listdir(vdir):
        jp = os.path.join(vdir, vid, vid + ".json")
        data = _read_json(jp)
        if not data:
            continue
        best = (vid, data)
        if data.get("inheritsFrom") or "fabric" in vid.lower() or "forge" in vid.lower():
            break
    if not best:
        return "", "", ""
    vid, data = best
    low = vid.lower()
    libs = " ".join(
        (l.get("name") or "") for l in (data.get("libraries") or [])
        if isinstance(l, dict)
    ).lower()
    loader, lver = "原版", ""
    mc = data.get("inheritsFrom") or ""
    if "fabric" in low or "fabric" in libs:
        loader = "Fabric"
        m = re.search(r"fabric-loader-([0-9.]+)", vid)
        lver = m.group(1) if m else ""
        if not mc:
            m2 = re.search(r"-(\d+\.\d+(?:\.\d+)?)$", vid)
            mc = m2.group(1) if m2 else ""
    elif "neoforge" in low or "neoforge" in libs:
        loader = "NeoForge"
        m = re.search(r"neoforge-([0-9.]+)", vid)
        lver = m.group(1) if m else ""
        if not mc:
            mv = (data.get("arguments") or {}).get("game")
            mc = "1.21.1" if (lver.startswith("21.") or True) else ""
    elif "forge" in low or "forge" in libs:
        loader = "Forge"
        m = re.search(r"forge-([0-9.]+)", vid)
        lver = m.group(1) if m else ""
        if not mc:
            m2 = re.match(r"(\d+\.\d+(?:\.\d+)?)-", vid)
            mc = m2.group(1) if m2 else ""
    if not mc:
        mc = vid
    return mc, loader, lver


def load_instance(path: str) -> Optional[Instance]:
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return None
    meta = _read_json(os.path.join(path, META_FILE)) or {}
    mc, loader, lver = detect_loader(path)
    name = meta.get("name") or os.path.basename(path)
    return Instance(
        name=name,
        path=path,
        mc_version=meta.get("mc_version") or mc,
        loader=meta.get("loader") or loader,
        loader_version=meta.get("loader_version") or lver,
        version_id=meta.get("version_id") or "",
    )


def looks_like_instance(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    if os.path.exists(os.path.join(path, META_FILE)):
        return True
    if os.path.isdir(os.path.join(path, "versions")):
        return True
    for d in ("mods", "saves", "config", "shaderpacks", "resourcepacks"):
        if os.path.isdir(os.path.join(path, d)):
            return True
    return False


def list_instances(base: str) -> list[Instance]:
    base = os.path.abspath(base or "")
    out: list[Instance] = []
    if not os.path.isdir(base):
        return out
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return out
    for entry in entries:
        p = os.path.join(base, entry)
        if os.path.isdir(p) and looks_like_instance(p):
            inst = load_instance(p)
            if inst:
                out.append(inst)
    return out


def create_instance_dir(base: str, name: str, mc_version: str, loader: str,
                        loader_version: str = "", version_id: str = "") -> str:
    base = os.path.abspath(base)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, _clean_name(name))
    if os.path.exists(path) and os.listdir(path):
        stamp = time.strftime("%H%M%S")
        path = os.path.join(base, f"{_clean_name(name)}-{stamp}")
    os.makedirs(path, exist_ok=True)
    for sub in ("mods", "config", "saves", "resourcepacks", "shaderpacks", "logs", "datapacks"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    # 部分安装器需要 launcher_profiles.json 才认作有效游戏目录
    lp = os.path.join(path, "launcher_profiles.json")
    if not os.path.exists(lp):
        with open(lp, "w", encoding="utf-8") as f:
            json.dump({"profiles": {}, "selectedProfile": "", "clientToken": "mcdl"}, f)
    write_instance_meta(path, name, mc_version, loader, loader_version, version_id)
    return path


def write_instance_meta(path: str, name: str, mc_version: str, loader: str,
                        loader_version: str = "", version_id: str = ""):
    meta = {
        "name": name,
        "mc_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "version_id": version_id,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(path, META_FILE), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def pick_target_dir(path: str, kind: str, world: str = "") -> str:
    """根据内容类型返回目标目录。"""
    if kind == "mod":
        return os.path.join(path, "mods")
    if kind == "shader":
        return os.path.join(path, "shaderpacks")
    if kind == "resourcepack":
        return os.path.join(path, "resourcepacks")
    if kind == "datapack":
        if world:
            return os.path.join(path, "saves", world, "datapacks")
        return os.path.join(path, "datapacks")
    if kind == "world":
        return os.path.join(path, "saves")
    return path
