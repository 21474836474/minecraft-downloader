"""高层安装流程：游戏实例、模组、整合包、光影、材质、数据包。

内容提供方（prov）可以是 Modrinth 或 CurseForge，两者接口已对齐。
安装模组时按 jar 内 modId 做"已有模组"拦截（replace / skip）。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import config, loaders, mods as mods_util
from .mojang import Mojang
from .net import Downloader

KIND_LABEL = {
    "mod": "模组",
    "modpack": "整合包",
    "shader": "光影包",
    "resourcepack": "材质包",
    "datapack": "数据包",
}

LOADER_BY_KEY = dict(fabric="Fabric", forge="Forge", neoforge="NeoForge")


def _mr_loader(loader: str) -> str:
    return {"Fabric": "fabric", "Forge": "forge", "NeoForge": "neoforge",
            "Quilt": "quilt"}.get(loader, "")


def _policy(on_conflict: Optional[str]) -> str:
    if on_conflict in ("replace", "skip"):
        return on_conflict
    try:
        return config.load_config().get("on_conflict") or "replace"
    except Exception:  # noqa: BLE001
        return "replace"


def _stage_dir(target: str) -> str:
    d = os.path.join(target, ".mcdl-staging")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def _same_file(path: str, sha1: str = "", size: Optional[int] = None) -> bool:
    if not os.path.exists(path):
        return False
    try:
        if size is not None and os.path.getsize(path) != size:
            return False
        if sha1:
            return mods_util_hash(path) == sha1.lower()
        return True
    except OSError:
        return False


def mods_util_hash(path: str) -> str:
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------- 游戏本体 ----------------
def install_game(dl: Downloader, mj: Mojang, base_dir: str, name: str, mc: str,
                 loader: str = "原版", loader_version: str = "",
                 include_assets: bool = True,
                 log: Callable[[str], None] = print) -> config.Instance:
    loader = loader or "原版"
    log(f"=== 开始安装实例：{name} / {mc} / {loader} ===")
    inst_path = config.create_instance_dir(base_dir, name, mc, loader, loader_version)

    mj.install(inst_path, mc, include_assets=include_assets)

    version_id = mc
    if loader == "Fabric":
        version_id = loaders.install_fabric(dl, inst_path, mc, loader_version or None, log)
    elif loader in ("Forge", "NeoForge"):
        kind = "forge" if loader == "Forge" else "neoforge"
        if not loader_version:
            vers = (loaders.forge_versions(dl, mc) if loader == "Forge"
                    else loaders.neoforge_versions(dl, mc))
            if not vers:
                raise loaders.InstallerError(f"{mc} 没有可用的 {loader} 版本")
            loader_version = vers[0]
            log(f"自动选择 {loader} 版本：{loader_version}")
        version_id = loaders.install_forge_like(dl, inst_path, mc, loader_version, kind, log=log)
    elif loader != "原版":
        raise loaders.InstallerError(f"不支持的加载器：{loader}")

    config.write_instance_meta(inst_path, name, mc, loader, loader_version, version_id)
    log(f"=== 实例安装完成：{inst_path} ===")
    return config.load_instance(inst_path)


def _has_game(inst_path: str, mc: str) -> bool:
    return bool(mc) and os.path.exists(os.path.join(inst_path, "versions", mc, mc + ".jar"))


def ensure_game_and_loader(dl: Downloader, mj: Mojang, inst_path: str, mc: str,
                           loader_name: str = "", loader_ver: str = "",
                           include_assets: bool = True,
                           log: Callable[[str], None] = print) -> str:
    """确保实例内有指定 MC 版本与加载器，返回版本 id。"""
    if not _has_game(inst_path, mc):
        log(f"  安装游戏本体 {mc}")
        mj.install(inst_path, mc, include_assets=include_assets)
    elif loader_name and loader_name != "原版":
        log(f"  已有游戏本体 {mc}")

    vid = mc
    if loader_name == "Fabric":
        vid = loaders.install_fabric(dl, inst_path, mc, loader_ver or None, log)
    elif loader_name in ("Forge", "NeoForge"):
        kind = "forge" if loader_name == "Forge" else "neoforge"
        if not loader_ver:
            vers = (loaders.forge_versions(dl, mc) if loader_name == "Forge"
                    else loaders.neoforge_versions(dl, mc))
            if not vers:
                raise loaders.InstallerError(f"{mc} 没有可用的 {loader_name} 版本")
            loader_ver = vers[0]
        vid = loaders.install_forge_like(dl, inst_path, mc, loader_ver, kind, log=log)
    elif loader_name not in ("", "原版"):
        raise loaders.InstallerError(f"不支持的加载器：{loader_name}")

    config.write_instance_meta(inst_path, os.path.basename(inst_path), mc,
                               loader_name or "原版", loader_ver, vid)
    return vid


# ---------------- 内容安装 ----------------
def install_content(dl: Downloader, prov, inst_path: str, kind: str, project_id: str,
                    version_id: str = "", with_deps: bool = True, world: str = "",
                    log: Callable[[str], None] = print,
                    on_conflict: Optional[str] = None) -> list[str]:
    """安装模组/光影/材质/数据包，自动补齐必需前置，并对已有同款做拦截。"""
    inst = config.load_instance(inst_path)
    mc = inst.mc_version if inst else ""
    loader = _mr_loader(inst.loader if inst else "")
    label = KIND_LABEL.get(kind, kind)
    policy = _policy(on_conflict)
    log(f"=== 安装{label}（冲突策略：{'替换' if policy == 'replace' else '跳过'}） ===")

    plan: list[dict] = []
    seen: set[str] = set()

    def collect(pid: str, vid: str, depth: int = 0, forced_kind: str = ""):
        if pid in seen:
            return
        seen.add(pid)
        try:
            proj = prov.project(pid)
        except Exception:  # noqa: BLE001
            proj = {}
        title = proj.get("title") or pid
        this_kind = forced_kind or proj.get("project_type") or kind
        this_kind = {"modpack": "mod"}.get(this_kind, this_kind)
        ver = prov.version(vid, pid) if vid else prov.best_version(pid, mc, loader)
        if not ver:
            log(f"  {title}: 找不到适配 {mc}/{loader} 的版本")
            return
        f = prov.version_file(ver)
        if not f:
            log(f"  {title}: 该版本没有可下载文件")
            return
        target = config.pick_target_dir(inst_path, this_kind,
                                        world if this_kind == kind else "")
        log("  " + "  " * depth + f"{title} {ver.get('version_number')}"
            + ("  [前置]" if depth else ""))
        plan.append({
            "kind": this_kind,
            "target": target,
            "filename": f["filename"],
            "url": f.get("urls") or f.get("url"),
            "sha1": (f.get("hashes") or {}).get("sha1"),
            "size": f.get("size"),
        })
        if not with_deps:
            return
        for dep in ver.get("dependencies") or []:
            if dep.get("dependency_type") != "required":
                continue
            dpid = dep.get("project_id")
            if not dpid:
                continue
            dvid = dep.get("version_id") or ""
            if not dvid:
                dv = prov.best_version(dpid, mc, loader)
                dvid = dv["id"] if dv else ""
            if dvid:
                collect(dpid, dvid, depth + 1)

    collect(project_id, version_id)

    # 按目标目录分组 → 暂存 → 冲突拦截 → 落盘
    groups: dict[tuple[str, str], list[dict]] = {}
    for it in plan:
        groups.setdefault((it["target"], it["kind"]), []).append(it)

    installed: list[str] = []
    all_replaced: list[str] = []
    all_skipped: list[str] = []
    for (target, k), items in groups.items():
        stage = _stage_dir(target)
        todo = []
        for it in items:
            dest = os.path.join(target, it["filename"])
            if _same_file(dest, it["sha1"] or "", it["size"]):
                log(f"  已安装（校验一致，跳过下载）：{it['filename']}")
                all_skipped.append(it["filename"])
                continue
            todo.append({"url": it["url"], "dest": os.path.join(stage, it["filename"]),
                         "sha1": it["sha1"], "size": it["size"],
                         "label": it["filename"]})
        if todo:
            dl.download_many(todo, f"下载{label}")
        files = [os.path.join(stage, f) for f in os.listdir(stage)] if os.path.isdir(stage) else []
        got, rep, skip = mods_util.commit_staged(files, target, k, policy, log)
        installed += got
        all_replaced += rep
        all_skipped += skip
        shutil.rmtree(stage, ignore_errors=True)

    log(f"=== {label}安装完成：新增/更新 {len(installed)} 个，"
        f"替换旧版本 {len(all_replaced)} 个，跳过 {len(all_skipped)} 个 ===")
    return installed


# ---------------- 整合包 ----------------
def _extract_dir(z: zipfile.ZipFile, prefix: str, inst_path: str) -> int:
    n = 0
    for name in z.namelist():
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = name[len(prefix):]
        dest = os.path.join(inst_path, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with z.open(name) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        n += 1
    return n


def _loader_from_cf_id(loader_id: str) -> tuple[str, str]:
    """forge-47.2.0 / neoforge-21.1.209 / fabric-0.16.5 → (名称, 版本)"""
    low = (loader_id or "").lower()
    for key, name in (("neoforge", "NeoForge"), ("forge", "Forge"),
                      ("fabric", "Fabric"), ("quilt", "Quilt")):
        if low.startswith(key):
            return name, loader_id[len(key) + 1:] if "-" in loader_id else ""
    return "", ""


def _install_pack_files(dl: Downloader, items: list[dict], mods_target: str,
                        inst_path: str, policy: str,
                        log: Callable[[str], None]) -> tuple[int, int, int]:
    """下载整合包文件，mods/ 下的走暂存+冲突拦截，其余直接覆盖。"""
    others = [it for it in items if it["dest_dir"] != mods_target]
    jars = [it for it in items if it["dest_dir"] == mods_target]
    if others:
        dl.download_many([{"url": it["url"], "dest": os.path.join(it["dest_dir"], it["filename"]),
                           "sha1": it["sha1"], "size": it["size"], "label": it["filename"]}
                          for it in others], "整合包其他文件")

    if not jars:
        return 0, 0, 0
    stage = _stage_dir(mods_target)
    todo = []
    skipped_same = 0
    for it in jars:
        dest = os.path.join(mods_target, it["filename"])
        if _same_file(dest, it["sha1"] or "", it["size"]):
            skipped_same += 1
            continue
        todo.append({"url": it["url"], "dest": os.path.join(stage, it["filename"]),
                     "sha1": it["sha1"], "size": it["size"], "label": it["filename"]})
    if todo:
        dl.download_many(todo, "整合包模组")
    files = [os.path.join(stage, f) for f in os.listdir(stage)] if os.path.isdir(stage) else []
    got, rep, skip = mods_util.commit_staged(files, mods_target, "mod", policy, log)
    shutil.rmtree(stage, ignore_errors=True)
    return len(got), len(rep), skip + skipped_same


def install_modpack(dl: Downloader, mj: Mojang, prov, inst_path: str, version: dict,
                    auto_loader: bool = True,
                    log: Callable[[str], None] = print,
                    on_conflict: Optional[str] = None) -> dict:
    """把整合包（.mrpack / CurseForge .zip）安装到当前实例目录。"""
    f = prov.version_file(version)
    if not f:
        raise RuntimeError("该整合包版本没有文件")
    name = version.get("name") or f["filename"]
    policy = _policy(on_conflict)
    log(f"=== 安装整合包：{name}（冲突策略：{'替换' if policy == 'replace' else '跳过'}） ===")
    tmpdir = tempfile.mkdtemp(prefix="mcdl-pack-")
    pack = os.path.join(tmpdir, f["filename"])
    src = f.get("urls") or f.get("url")
    dl.download(src, pack, (f.get("hashes") or {}).get("sha1"), f.get("size"), f["filename"])

    try:
        with zipfile.ZipFile(pack) as z:
            names = set(z.namelist())
            if "modrinth.index.json" in names:
                info = _install_mrpack(dl, mj, prov, inst_path, z, version, auto_loader,
                                       policy, log)
            elif "manifest.json" in names:
                info = _install_cf_pack(dl, mj, prov, inst_path, z, auto_loader, policy, log)
            elif any(n.startswith("mods/") for n in names):
                n = 0
                for n_ in names:
                    if n_.endswith("/"):
                        continue
                    dest = os.path.join(inst_path, n_.replace("/", os.sep))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(n_) as s2, open(dest, "wb") as d2:
                        shutil.copyfileobj(s2, d2)
                    n += 1
                info = {"name": name, "files": n, "path": inst_path}
                log(f"  已解压普通整合包 {n} 个文件")
            else:
                raise RuntimeError("无法识别整合包格式（缺少 modrinth.index.json / manifest.json）")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    log(f"=== 整合包安装完成：{info.get('name')} ===")
    return info


def _install_mrpack(dl, mj, prov, inst_path, z: zipfile.ZipFile, version, auto_loader,
                    policy, log) -> dict:
    index = json.loads(z.read("modrinth.index.json"))
    name = index.get("name") or version.get("name") or "modpack"
    deps = index.get("dependencies") or {}
    mc = deps.get("minecraft") or ""
    loader_name, loader_ver = "", ""
    for key, lname in (("fabric-loader", "Fabric"), ("neoforge", "NeoForge"), ("forge", "Forge")):
        if deps.get(key):
            loader_name, loader_ver = lname, deps[key]
            break
    log(f"  整合包信息：{name} / MC {mc} / {loader_name or '原版'} {loader_ver}")

    if auto_loader and mc:
        ensure_game_and_loader(dl, mj, inst_path, mc, loader_name, loader_ver, True, log)

    files = index.get("files") or []
    log(f"  整合包文件 {len(files)} 个")
    items = []
    for entry in files:
        path = entry.get("path") or ""
        if not path:
            continue
        env = entry.get("env") or {}
        if env.get("client") == "unsupported":
            continue
        urls = entry.get("downloads") or []
        if not urls:
            continue
        rel = path.replace("/", os.sep)
        items.append({
            "url": urls[0],
            "filename": os.path.basename(rel),
            "dest_dir": os.path.dirname(os.path.join(inst_path, rel)),
            "sha1": (entry.get("hashes") or {}).get("sha1"),
            "size": entry.get("fileSize"),
        })
    got, rep, skip = _install_pack_files(dl, items, os.path.join(inst_path, "mods"),
                                         inst_path, policy, log)
    log(f"  模组：新增 {got}，替换旧版本 {rep}，跳过 {skip}")
    n = _extract_dir(z, "overrides/", inst_path) + _extract_dir(z, "client-overrides/", inst_path)
    log(f"  已覆盖配置 {n} 个文件")
    return {"name": name, "files": got, "replaced": rep, "skipped": skip, "path": inst_path}


def _install_cf_pack(dl, mj, prov, inst_path, z: zipfile.ZipFile, auto_loader, policy, log) -> dict:
    manifest = json.loads(z.read("manifest.json"))
    mc_info = manifest.get("minecraft") or {}
    mc = mc_info.get("version") or ""
    name = manifest.get("name") or "modpack"
    loader_ids = [m.get("id") for m in (mc_info.get("modLoaders") or []) if m.get("id")]
    primary = next((m.get("id") for m in (mc_info.get("modLoaders") or [])
                    if m.get("primary")), loader_ids[0] if loader_ids else "")
    loader_name, loader_ver = _loader_from_cf_id(primary or "")
    log(f"  整合包信息：{name} / MC {mc} / {loader_name or '原版'} {loader_ver}")

    if auto_loader and mc:
        ensure_game_and_loader(dl, mj, inst_path, mc, loader_name, loader_ver, True, log)

    entries = manifest.get("files") or []
    log(f"  依赖文件 {len(entries)} 个，正在解析下载地址…")
    mods_dir = os.path.join(inst_path, "mods")
    os.makedirs(mods_dir, exist_ok=True)

    def resolve(entry) -> Optional[dict]:
        pid, fid = str(entry.get("projectID") or ""), str(entry.get("fileID") or "")
        if not pid or not fid:
            return None
        try:
            ver = prov.file_by_id(pid, fid)
        except Exception:  # noqa: BLE001
            try:
                ver = prov.best_version(pid, mc, "")
            except Exception:  # noqa: BLE001
                ver = None
        if not ver:
            return None
        f = prov.version_file(ver)
        if not f:
            return None
        return {"url": f.get("urls") or f["url"], "filename": f["filename"],
                "dest_dir": mods_dir,
                "sha1": (f.get("hashes") or {}).get("sha1"), "size": f.get("size")}

    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for it in ex.map(resolve, entries):
            if it:
                items.append(it)
    log(f"  可下载 {len(items)}/{len(entries)} 个文件")
    got, rep, skip = _install_pack_files(dl, items, mods_dir, inst_path, policy, log)
    log(f"  模组：新增 {got}，替换旧版本 {rep}，跳过 {skip}")
    n = _extract_dir(z, "overrides/", inst_path) + _extract_dir(z, "client-overrides/", inst_path)
    log(f"  已覆盖配置 {n} 个文件")
    return {"name": name, "files": got, "replaced": rep, "skipped": skip, "path": inst_path}
