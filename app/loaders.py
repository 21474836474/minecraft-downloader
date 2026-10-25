"""加载器安装：Fabric / Forge / NeoForge（纯 Python 实现，不依赖官方安装器）。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Callable, Optional

from .mojang import _rule_allows, _os_name
from .net import Downloader, mirror_url

FABRIC_META = "https://meta.fabricmc.net"
BMCL = "https://bmclapi2.bangbang93.com"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"
FORGE_MAVEN = "https://maven.minecraftforge.net"
MOJANG_LIBS = "https://libraries.minecraft.net"


class InstallerError(RuntimeError):
    pass


# ---------------- Java ----------------
def find_java(min_major: int = 8) -> Optional[str]:
    cands: list[str] = []
    env = os.environ.get("MCDL_JAVA") or os.environ.get("JAVA_HOME")
    if env:
        cands.append(os.path.join(env, "bin", "java.exe") if os.path.isdir(env) else env)
    exe = shutil.which("java")
    if exe:
        cands.append(exe)
    for root in (r"C:\Program Files\Java", r"C:\Program Files (x86)\Java",
                 r"C:\Program Files\Eclipse Adoptium", r"C:\Program Files\Microsoft",
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs\Eclipse Adoptium")):
        if os.path.isdir(root):
            for d in sorted(os.listdir(root), reverse=True):
                p = os.path.join(root, d, "bin", "java.exe")
                if os.path.exists(p):
                    cands.append(p)
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


def java_version(java: str) -> int:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=20).stderr
        m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
        if m:
            major = int(m.group(1))
            return 8 if major == 1 else major
    except Exception:  # noqa: BLE001
        pass
    return 0


# ---------------- Maven 工具 ----------------
def maven_path(name: str) -> str:
    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    fn = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext}"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{fn}"


def _coord_url(name: str, base: str) -> str:
    return base.rstrip("/") + "/" + maven_path(name)


def maven_main_class(jar: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(jar) as z:
            mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "ignore")
        for line in mf.splitlines():
            if line.lower().startswith("main-class:"):
                return line.split(":", 1)[1].strip()
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    return None


# ---------------- 通用库收集 ----------------
def collect_library_items(inst: str, libs: list[dict], default_base: str = MOJANG_LIBS) -> list[dict]:
    items = []
    libroot = os.path.join(inst, "libraries")
    for lib in libs:
        if not isinstance(lib, dict):
            continue
        if lib.get("rules") and not _rule_allows(lib):
            continue
        art = (lib.get("downloads") or {}).get("artifact")
        if art and art.get("path"):
            items.append({
                "url": art["url"],
                "dest": os.path.join(libroot, art["path"]),
                "sha1": art.get("sha1"),
                "size": art.get("size"),
                "label": os.path.basename(art["path"]),
                "optional": not art.get("url"),
            })
            continue
        name = lib.get("name")
        if not name:
            continue
        base = lib.get("url") or default_base
        path = maven_path(name)
        items.append({
            "url": base.rstrip("/") + "/" + path,
            "dest": os.path.join(libroot, path),
            "label": os.path.basename(path),
        })
        # 原生库 classifier
        natives = lib.get("natives") or {}
        key = natives.get(_os_name())
        if key:
            key = key.replace("${arch}", "64")
            cl = (lib.get("downloads") or {}).get("classifiers", {})
            cname = f"{parts_name(name, key)}"
            items.append({
                "url": base.rstrip("/") + "/" + maven_path(cname),
                "dest": os.path.join(libroot, maven_path(cname)),
                "label": os.path.basename(maven_path(cname)),
                "optional": True,
            })
    return items


def parts_name(name: str, classifier: str) -> str:
    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)
    return f"{name}:{classifier}@{ext}"


# ---------------- Fabric ----------------
def fabric_loader_versions(dl: Downloader, mc: str) -> list[str]:
    data = dl.get_json(f"{FABRIC_META}/v2/versions/loader/{mc}", timeout=30)
    return [x["loader"]["version"] for x in data if x.get("loader")]


def install_fabric(dl: Downloader, inst: str, mc: str, loader: Optional[str],
                   log: Callable[[str], None] = print) -> str:
    if not loader:
        vers = fabric_loader_versions(dl, mc)
        if not vers:
            raise InstallerError(f"{mc} 没有可用的 Fabric 加载器")
        loader = vers[0]
    log(f"Fabric {loader} / Minecraft {mc}")
    profile = dl.get_json(f"{FABRIC_META}/v2/versions/loader/{mc}/{loader}/profile/json", timeout=30)
    vid = profile.get("id") or f"fabric-loader-{loader}-{mc}"
    profile["id"] = vid
    profile["inheritsFrom"] = profile.get("inheritsFrom") or mc

    libs = profile.get("libraries") or []
    items = []
    libroot = os.path.join(inst, "libraries")
    for lib in libs:
        name = lib.get("name")
        if not name:
            continue
        path = maven_path(name)
        base = (lib.get("url") or "https://maven.fabricmc.net/").rstrip("/")
        items.append({"url": base + "/" + path, "dest": os.path.join(libroot, path),
                      "label": os.path.basename(path)})
    dl.download_many(items, "Fabric 依赖库")

    vdir = os.path.join(inst, "versions", vid)
    os.makedirs(vdir, exist_ok=True)
    with open(os.path.join(vdir, vid + ".json"), "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    log(f"Fabric 安装完成：{vid}")
    return vid


# ---------------- 版本列表 ----------------
def neoforge_versions(dl: Downloader, mc: str) -> list[str]:
    try:
        data = dl.get_json(f"{BMCL}/neoforge/list/{mc}", timeout=30)
        vers = [x.get("version") for x in data if x.get("version")]
    except Exception:  # noqa: BLE001
        data = dl.get_json(f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/maven-metadata.xml", timeout=30)
        vers = []
    uniq = sorted(set(vers), key=_version_key, reverse=True)
    return uniq


def forge_versions(dl: Downloader, mc: str) -> list[str]:
    try:
        data = dl.get_json(f"{BMCL}/forge/minecraft/{mc}", timeout=30)
        vers = [x.get("version") for x in data if x.get("version")]
    except Exception:  # noqa: BLE001
        vers = []
    return sorted(set(vers), key=_version_key, reverse=True)


def _version_key(v: str):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v or ""))


def _resolve_arg(arg: str, data_paths: dict[str, str], libroot: str, base: str,
                 dl: Downloader) -> str:
    """替换 {TOKEN}，并把 [group:artifact:version@ext] 解析成已下载的本地路径。"""
    out = re.sub(r"\{(\w+)\}", lambda m: str(data_paths.get(m.group(1), m.group(0))), arg)
    m = re.fullmatch(r"\[(.+)\]", out.strip())
    if m:
        coord = m.group(1)
        path = maven_path(coord)
        dest = os.path.join(libroot, path)
        if not os.path.exists(dest):
            dl.download(_coord_url(coord, base), dest, label=os.path.basename(path))
        return dest
    return out


def _installer_jar(dl: Downloader, kind: str, ver: str, cache: str) -> str:
    if kind == "neoforge":
        name = f"neoforge-{ver}-installer.jar"
        url = f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/{ver}/{name}"
    else:
        name = f"forge-{ver}-installer.jar"
        mc = ver.split("-")[0]
        url = f"{FORGE_MAVEN}/net/minecraftforge/forge/{ver}/{name}"
    dest = os.path.join(cache, "installers", name)
    dl.download(url, dest, label=name)
    return dest


# ---------------- Forge / NeoForge 手动安装 ----------------
def install_forge_like(dl: Downloader, inst: str, mc: str, loader_ver: str,
                       kind: str = "neoforge", java: Optional[str] = None,
                       log: Callable[[str], None] = print) -> str:
    """手动执行 install_profile：下载库 + 运行 processors，无需官方安装器联网。"""
    java = java or find_java()
    if kind == "neoforge":
        ver = loader_ver
    else:
        ver = loader_ver if loader_ver.startswith(mc) else f"{mc}-{loader_ver}"
    cache = os.path.join(os.path.dirname(inst), ".mcdl-cache")
    jar = _installer_jar(dl, kind, ver, cache)

    with zipfile.ZipFile(jar) as z:
        names = set(z.namelist())
        profile = json.loads(z.read("install_profile.json"))
        vj_name = "version.json" if "version.json" in names else None
        if vj_name:
            version_json = json.loads(z.read(vj_name))
        else:
            version_json = profile.get("versionInfo") or profile.get("json")
        if not version_json:
            raise InstallerError("安装包内未找到版本信息")

        vid = version_json.get("id") or f"{kind}-{ver}"
        if kind == "forge" and mc not in vid:
            vid = f"{mc}-forge-{ver}" if not ver.startswith(mc) else ver
        version_json["id"] = vid
        version_json.setdefault("inheritsFrom", version_json.get("inheritsFrom") or mc)

        # 1) 收集所有库
        items: list[dict] = []
        libroot = os.path.join(inst, "libraries")
        base = NEOFORGE_MAVEN if kind == "neoforge" else FORGE_MAVEN
        for lib in (profile.get("libraries") or []):
            art = (lib.get("downloads") or {}).get("artifact")
            if art and art.get("path"):
                items.append({"url": art["url"], "dest": os.path.join(libroot, art["path"]),
                              "sha1": art.get("sha1"), "size": art.get("size"),
                              "label": os.path.basename(art["path"])})
            elif lib.get("name"):
                p = maven_path(lib["name"])
                items.append({"url": _coord_url(lib["name"], lib.get("url") or base),
                              "dest": os.path.join(libroot, p), "label": os.path.basename(p)})
        items += collect_library_items(inst, version_json.get("libraries") or [], base)
        # 去重
        seen, uniq = set(), []
        for it in items:
            d = it["dest"].lower()
            if d in seen:
                continue
            seen.add(d)
            uniq.append(it)
        dl.download_many(uniq, "加载器依赖库")

        # 2) 处理数据表
        side = "client"
        tmp = tempfile.mkdtemp(prefix="mcdl-inst-")
        data_paths: dict[str, str] = {}
        for key, val in (profile.get("data") or {}).items():
            raw = (val or {}).get(side) or (val or {}).get("client") or ""
            if not raw:
                continue
            if raw.startswith("[") and raw.endswith("]"):
                coord = raw[1:-1]
                path = maven_path(coord)
                # 多数为 processors 的输出文件，只做路径映射
                data_paths[key] = os.path.join(libroot, path)
            elif raw.startswith("/"):
                rel = raw.lstrip("/")
                out = os.path.join(tmp, os.path.basename(rel))
                with z.open(rel) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                data_paths[key] = out
            else:
                data_paths[key] = raw.strip("'\"")
        data_paths["SIDE"] = side
        data_paths["ROOT"] = inst
        data_paths["INSTALLER"] = jar
        data_paths["MINECRAFT_JAR"] = os.path.join(inst, "versions", mc, mc + ".jar")

        # 预建输出目录（部分 processor 不会自己创建父目录）
        for val in list(data_paths.values()):
            parent = os.path.dirname(val)
            if parent and not os.path.splitext(val)[1]:
                try:
                    os.makedirs(val, exist_ok=True)
                except OSError:
                    pass
            elif parent:
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError:
                    pass

        # 3) 运行 processors
        processors = profile.get("processors") or []
        for i, proc in enumerate(processors):
            sides = proc.get("sides")
            if sides and side not in sides:
                continue
            cp = []
            for c in proc.get("classpath") or []:
                p = os.path.join(libroot, maven_path(c))
                if os.path.exists(p):
                    cp.append(p)
            jar_coord = proc.get("jar")
            if jar_coord:
                p = os.path.join(libroot, maven_path(jar_coord))
                if os.path.exists(p) and p not in cp:
                    cp.insert(0, p)
            if not cp:
                continue
            main = maven_main_class(cp[0])
            if not main:
                raise InstallerError(f"processor 缺少主类: {jar_coord}")
            args = [_resolve_arg(str(a), data_paths, libroot, base, dl)
                    for a in proc.get("args") or []]
            cmd = [java, "-cp", os.pathsep.join(cp), main] + args
            log(f"  处理步骤 {i + 1}/{len(processors)}: {main.split('.')[-1]}")
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if res.returncode != 0:
                tail = (res.stdout or "")[-800:] + (res.stderr or "")[-800:]
                raise InstallerError(f"处理器失败({main}): {tail}")

        # 4) 写入版本 json
        vdir = os.path.join(inst, "versions", vid)
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, vid + ".json"), "w", encoding="utf-8") as f:
            json.dump(version_json, f, ensure_ascii=False, indent=2)
        log(f"{kind.title() if kind!='neoforge' else 'NeoForge'} 安装完成：{vid}")
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            pass
        return vid
