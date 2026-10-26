"""模组文件识别：读取 jar 内的 modId，用于"已有模组"冲突拦截。"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from typing import Iterable, Optional

try:
    import tomllib  # py3.11+
except Exception:  # noqa: BLE001
    tomllib = None  # type: ignore

_MOD_ID_RE = re.compile(r'^\s*modId\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_PROVIDES_RE = re.compile(r'^\s*provides\s*=\s*\[([^\]]*)\]', re.MULTILINE)


def _toml_ids(text: str) -> set[str]:
    ids: set[str] = set()
    if tomllib:
        try:
            data = tomllib.loads(text)
            for entry in data.get("mods") or []:
                mid = entry.get("modId")
                if mid:
                    ids.add(str(mid).lower())
                for p in entry.get("provides") or []:
                    ids.add(str(p).lower())
            return ids
        except Exception:  # noqa: BLE001
            pass
    ids.update(m.group(1).lower() for m in _MOD_ID_RE.finditer(text))
    for m in _PROVIDES_RE.finditer(text):
        for tok in m.group(1).split(","):
            tok = tok.strip().strip("\"'")
            if tok:
                ids.add(tok.lower())
    return ids


def read_mod_ids(jar: str) -> set[str]:
    """返回 jar 声明的模组 id 集合（读不到就返回空集）。"""
    if not zipfile.is_zipfile(jar):
        return set()
    ids: set[str] = set()
    try:
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
            for entry in ("fabric.mod.json", "quilt.mod.json"):
                if entry in names:
                    try:
                        data = json.loads(z.read(entry).decode("utf-8", "ignore"))
                    except ValueError:
                        continue
                    if entry.startswith("quilt"):
                        ql = data.get("quilt_loader") or {}
                        if ql.get("id"):
                            ids.add(str(ql["id"]).lower())
                        for p in ql.get("provides") or []:
                            ids.add(str(p).lower())
                    else:
                        if data.get("id"):
                            ids.add(str(data["id"]).lower())
                        for p in data.get("provides") or []:
                            ids.add(str(p).lower())
            for entry in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if entry in names:
                    ids |= _toml_ids(z.read(entry).decode("utf-8", "ignore"))
            if "mcmod.info" in names:
                try:
                    data = json.loads(z.read("mcmod.info").decode("utf-8", "ignore"))
                    if isinstance(data, dict):
                        data = data.get("modList") or []
                    for m in data or []:
                        if isinstance(m, dict) and m.get("modid"):
                            ids.add(str(m["modid"]).lower())
                except ValueError:
                    pass
    except (OSError, zipfile.BadZipFile):
        return set()
    return {i for i in ids if i and i not in ("minecraft", "forge", "neoforge", "fabricloader")}


def index_dir(path: str) -> dict[str, str]:
    """扫描目录内 .jar，建立 modId → 文件路径 索引。"""
    out: dict[str, str] = {}
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if not fn.lower().endswith((".jar", ".zip")):
            continue
        fp = os.path.join(path, fn)
        for mid in read_mod_ids(fp):
            out.setdefault(mid, fp)
    return out


def read_pack_ids(path: str) -> set[str]:
    """资源包/光影包没有 modId，用文件名（去版本号）当身份。"""
    base = os.path.basename(path).lower()
    base = re.sub(r"\.(zip|jar)$", "", base)
    base = re.sub(r"[-_ ]?v?\d+(\.\d+)+.*$", "", base)
    return {base} if base else set()


def commit_staged(staged: Iterable[str], target_dir: str, kind: str = "mod",
                  policy: str = "replace", log=print) -> tuple[list[str], list[str], list[str]]:
    """把暂存文件落到目标目录，按 modId 拦截重复。

    返回 (新装, 替换, 跳过)。policy: replace=删旧留新 / skip=保留旧文件。
    """
    os.makedirs(target_dir, exist_ok=True)
    use_ids = kind == "mod"
    index: dict[str, str] = index_dir(target_dir) if use_ids else {}
    name_index = {os.path.basename(p).lower(): p for p in
                  (os.path.join(target_dir, f) for f in os.listdir(target_dir))}
    installed, replaced, skipped = [], [], []

    for src in staged:
        fn = os.path.basename(src)
        dest = os.path.join(target_dir, fn)
        ids = read_mod_ids(src) if use_ids else read_pack_ids(src)
        conflicts: list[str] = []
        if use_ids:
            conflicts = sorted({index[i] for i in ids if i in index})
        else:
            old = name_index.get(fn.lower())
            if old:
                conflicts = [old]

        if conflicts and policy == "skip":
            log(f"  已存在同一{'模组' if use_ids else '文件'}，跳过：{fn}"
                f"（保留 {os.path.basename(conflicts[0])}）")
            skipped.append(fn)
            try:
                os.remove(src)
            except OSError:
                pass
            continue

        if os.path.exists(dest) and os.path.abspath(dest) not in conflicts:
            try:
                os.remove(dest)
            except OSError:
                pass
        shutil.move(src, dest)

        for old in conflicts:
            if os.path.abspath(old) == os.path.abspath(dest):
                continue
            try:
                os.remove(old)
            except OSError:
                pass
            for k, v in list(index.items()):
                if os.path.abspath(v) == os.path.abspath(old):
                    index.pop(k, None)
            log(f"  替换旧版本：{os.path.basename(old)} → {fn}")
            replaced.append(os.path.basename(old))

        for mid in ids:
            index[mid] = dest
        name_index[fn.lower()] = dest
        installed.append(fn)
    return installed, replaced, skipped
