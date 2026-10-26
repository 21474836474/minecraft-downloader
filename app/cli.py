"""命令行入口（无弹窗，便于自动化测试）。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import config, installer, loaders
from .curseforge import CurseForge
from .mojang import Mojang
from .modrinth import Modrinth, version_label
from .net import Downloader, Progress, human_size


def _mk(args, log):
    prog = Progress()
    dl = Downloader(threads=args.threads, limit_kbps=args.limit, use_mirror=not args.official,
                    logger=log, progress=prog)
    cf = CurseForge(dl, getattr(args, "cf_key", "") or "")
    return dl, prog, Mojang(dl, log), Modrinth(dl), cf


def _provider(args, mr, cf, kind: str = ""):
    if kind == "datapack":
        return mr
    return cf if getattr(args, "source", "modrinth") == "curseforge" else mr


def _run_with_progress(fn, dl, prog, log):
    last = [0.0]

    def tick(p: Progress):
        now = time.monotonic()
        if now - last[0] >= 1.0:
            last[0] = now
            log(f"  [{p.percent():5.1f}%] {p.summary()}")

    prog.on_update = tick
    try:
        return fn()
    finally:
        if prog.total:
            log(f"  完成：{prog.summary()}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mcdl", description="Minecraft 下载器")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="KB/s，0=不限速")
    ap.add_argument("--official", action="store_true", help="使用官方源（默认镜像加速）")
    ap.add_argument("--source", default="modrinth", choices=["modrinth", "curseforge"])
    ap.add_argument("--cf-key", default="", help="CurseForge API Key（留空用镜像）")
    ap.add_argument("--on-conflict", default="", choices=["", "replace", "skip"],
                    help="已存在同一模组时的处理：replace=替换（默认）/ skip=跳过")
    ap.add_argument("--quiet", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("versions")
    p.add_argument("--kind", default="vanilla")
    p.add_argument("--mc", default="")

    p = sub.add_parser("search")
    p.add_argument("--type", default="mod")
    p.add_argument("--query", default="")
    p.add_argument("--mc", default="")
    p.add_argument("--loader", default="")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("install-game")
    p.add_argument("--dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--mc", required=True)
    p.add_argument("--loader", default="原版")
    p.add_argument("--loader-version", default="")
    p.add_argument("--no-assets", action="store_true")

    p = sub.add_parser("install")
    p.add_argument("--kind", default="mod")
    p.add_argument("--instance", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--version", default="")
    p.add_argument("--world", default="")
    p.add_argument("--no-deps", action="store_true")

    p = sub.add_parser("install-modpack")
    p.add_argument("--instance", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--version", default="")
    p.add_argument("--no-auto-loader", action="store_true")

    args = ap.parse_args(argv)
    log = (lambda m: None) if args.quiet else (lambda m: print(m, flush=True))
    t0 = time.time()

    if args.cmd == "versions":
        dl, prog, mj, mr, cf = _mk(args, log)
        if args.kind == "vanilla":
            data = mj.visible_versions() if hasattr(mj, "visible_versions") else mj.versions()
            for v in data[:80]:
                print(f"{v['id']:<22} {v['type']:<10} {v['releaseTime'][:10]}")
        elif args.kind == "fabric":
            print("\n".join(loaders.fabric_loader_versions(dl, args.mc)[:40]))
        elif args.kind == "forge":
            print("\n".join(loaders.forge_versions(dl, args.mc)[:20]))
        elif args.kind == "neoforge":
            print("\n".join(loaders.neoforge_versions(dl, args.mc)[:40]))
        return 0

    if args.cmd == "search":
        dl, prog, mj, mr, cf = _mk(args, log)
        prov = _provider(args, mr, cf, args.type)
        res = prov.search(args.query, args.type, args.limit, 0, args.mc, args.loader)
        for h in res.get("hits", []):
            print(f"{h['slug']:<40} {h['title']:<40} 下载 {h['downloads']}")
        return 0

    if args.cmd == "install-game":
        dl, prog, mj, mr, cf = _mk(args, log)
        inst = _run_with_progress(
            lambda: installer.install_game(dl, mj, args.dir, args.name, args.mc,
                                           args.loader, args.loader_version,
                                           include_assets=not args.no_assets, log=log),
            dl, prog, log)
        print(json.dumps({"path": inst.path, "mc": inst.mc_version, "loader": inst.loader,
                          "version_id": inst.version_id}, ensure_ascii=False))
        print(f"耗时 {time.time() - t0:.1f}s")
        return 0

    if args.cmd == "install":
        dl, prog, mj, mr, cf = _mk(args, log)
        prov = _provider(args, mr, cf, args.kind)
        files = _run_with_progress(
            lambda: installer.install_content(dl, prov, args.instance, args.kind, args.project,
                                              args.version, not args.no_deps, args.world, log,
                                              args.on_conflict or None),
            dl, prog, log)
        print(json.dumps({"files": files}, ensure_ascii=False))
        print(f"耗时 {time.time() - t0:.1f}s")
        return 0

    if args.cmd == "install-modpack":
        dl, prog, mj, mr, cf = _mk(args, log)
        prov = _provider(args, mr, cf, "modpack")
        proj = prov.project(args.project)
        ver = prov.version(args.version, proj["id"]) if args.version else prov.best_version(
            proj["id"], "", "")
        if not ver:
            print("找不到版本")
            return 2
        log(f"整合包版本：{version_label(ver)}")
        info = _run_with_progress(
            lambda: installer.install_modpack(dl, mj, prov, args.instance, ver,
                                              not args.no_auto_loader, log,
                                              args.on_conflict or None),
            dl, prog, log)
        print(json.dumps(info, ensure_ascii=False))
        print(f"耗时 {time.time() - t0:.1f}s")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
