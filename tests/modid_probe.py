"""检查不同加载器 jar 的 modId 解析（fabric.mod.json / mods.toml / mcmod.info）。"""
import os
import tempfile

from app import mods
from app.curseforge import CurseForge
from app.net import Downloader

CASES = [
    ("238222", "1.12.2", "forge", "JEI 1.12.2 (mcmod.info)"),
    ("308769", "1.21.1", "fabric", "Mod Menu (fabric.mod.json)"),
    ("238222", "1.21.1", "neoforge", "JEI (neoforge.mods.toml)"),
]


def main():
    dl = Downloader(threads=4, logger=lambda m: None)
    cf = CurseForge(dl)
    tmp = tempfile.mkdtemp()
    for pid, mc, loader, label in CASES:
        vers = cf.versions(pid, mc, loader)
        if not vers:
            print(f"{label}: 无可用版本")
            continue
        f = cf.version_file(vers[0])
        dest = os.path.join(tmp, f["filename"])
        dl.download(f.get("urls") or f["url"], dest,
                    (f.get("hashes") or {}).get("sha1"), f.get("size"), f["filename"])
        ids = sorted(mods.read_mod_ids(dest))
        print(f"{label}: {f['filename']} -> {ids[:5]}")


if __name__ == "__main__":
    main()
