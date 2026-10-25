"""自动化测试：按验收清单依次安装实例并校验产物（全程无弹窗）。

用法：
  python tests/run_tests.py            # 全部测试，结束后删除测试实例
  python tests/run_tests.py --keep     # 保留测试实例
  python tests/run_tests.py --only t7  # 只跑某一项
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.join(ROOT, "_test")
INST_DIR = os.path.join(TEST_DIR, "inst")
LOGS = os.path.join(TEST_DIR, "logs")
PY = sys.executable


def cli(args: list[str], log_name: str, timeout: int = 3600):
    os.makedirs(LOGS, exist_ok=True)
    log_path = os.path.join(LOGS, log_name)
    cmd = [PY, "-m", "app.cli"] + args
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8", errors="replace") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, time.time() - t0, log_path


def check_instance(path: str, mc: str, loader: str | None = None) -> tuple[bool, str]:
    if not os.path.isdir(path):
        return False, "实例目录不存在"
    vdir = os.path.join(path, "versions")
    if not os.path.isdir(vdir) or not os.listdir(vdir):
        return False, "缺少 versions"
    base_jar = os.path.join(vdir, mc, mc + ".jar")
    if not os.path.exists(base_jar):
        return False, f"缺少客户端 jar: {base_jar}"
    if loader and loader != "原版":
        meta = json.load(open(os.path.join(path, ".mcdl-instance.json"), encoding="utf-8"))
        if meta.get("loader") != loader:
            return False, f"加载器记录异常: {meta.get('loader')}"
        found = False
        for vid in os.listdir(vdir):
            j = os.path.join(vdir, vid, vid + ".json")
            if os.path.exists(j):
                data = json.load(open(j, encoding="utf-8"))
                if data.get("inheritsFrom") == mc:
                    found = True
                    break
        if not found:
            return False, f"未找到 {loader} 的版本 json"
    if os.path.isdir(os.path.join(path, "libraries")):
        n = sum(len(files) for _, _, files in os.walk(os.path.join(path, "libraries")))
        if n < 10:
            return False, f"依赖库过少: {n}"
    return True, "OK"


def count_files(path: str) -> int:
    return sum(len(f) for _, _, f in os.walk(path)) if os.path.isdir(path) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--threads", default="32")
    ap.add_argument("--no-assets", action="store_true", help="跳过 assets（快速验证）")
    args = ap.parse_args()

    os.makedirs(INST_DIR, exist_ok=True)
    common = ["--threads", args.threads]
    if not args.no_assets:
        pass

    t_neo = os.path.join(INST_DIR, "1.21.1-neoforge")
    t_fab = os.path.join(INST_DIR, "1.21.1-fabric")
    t_old = os.path.join(INST_DIR, "1.12.2-forge")
    t_pack = os.path.join(INST_DIR, "modpack-test")

    steps = [
        ("t1", "26.4-snapshot-2 原版",
         common + ["install-game", "--dir", INST_DIR, "--name", "26.4-snapshot-2", "--mc",
                   "26.4-snapshot-2", "--loader", "原版"],
         lambda: check_instance(os.path.join(INST_DIR, "26.4-snapshot-2"), "26.4-snapshot-2")),
        ("t2", "1.21.1 NeoForge",
         common + ["install-game", "--dir", INST_DIR, "--name", "1.21.1-neoforge", "--mc",
                   "1.21.1", "--loader", "NeoForge"],
         lambda: check_instance(t_neo, "1.21.1", "NeoForge")),
        ("t3", "1.21.1 Fabric",
         common + ["install-game", "--dir", INST_DIR, "--name", "1.21.1-fabric", "--mc",
                   "1.21.1", "--loader", "Fabric"],
         lambda: check_instance(t_fab, "1.21.1", "Fabric")),
        ("t4", "23w13a_or_b 原版",
         common + ["install-game", "--dir", INST_DIR, "--name", "23w13a_or_b", "--mc",
                   "23w13a_or_b", "--loader", "原版"],
         lambda: check_instance(os.path.join(INST_DIR, "23w13a_or_b"), "23w13a_or_b")),
        ("t5", "1.12.2 原版",
         common + ["install-game", "--dir", INST_DIR, "--name", "1.12.2-vanilla", "--mc",
                   "1.12.2", "--loader", "原版"],
         lambda: check_instance(os.path.join(INST_DIR, "1.12.2-vanilla"), "1.12.2")),
        ("t6", "1.12.2 Forge",
         common + ["install-game", "--dir", INST_DIR, "--name", "1.12.2-forge", "--mc",
                   "1.12.2", "--loader", "Forge"],
         lambda: check_instance(t_old, "1.12.2", "Forge")),
        ("t7", "Create 6.0.5 模组（自动前置）",
         common + ["install", "--kind", "mod", "--instance", t_neo, "--project", "create",
                   "--version", ""],
         lambda: check_mods(t_neo)),
        ("t8", "整合包安装",
         common + ["install-modpack", "--instance", t_pack, "--project", "", "--version", ""],
         lambda: check_mods(t_pack)),
    ]

    results = []
    for tid, name, cmd, verify in steps:
        if args.only and args.only != tid:
            continue
        print(f"\n===== [{tid}] {name} =====", flush=True)
        # 清理同名的历史测试实例，避免产生 -HHMMSS 后缀目录
        if "--name" in cmd:
            target = os.path.join(INST_DIR, cmd[cmd.index("--name") + 1])
            shutil.rmtree(target, ignore_errors=True)
        if tid == "t7":
            fixmod = find_create_version(cmd)
            cmd = fixmod
        if tid == "t8":
            cmd = pick_modpack(cmd)
            if not cmd:
                print("  跳过：未找到可用整合包")
                results.append((tid, name, "SKIP", 0, ""))
                continue
        code, dur, log = cli(cmd, f"{tid}.log")
        ok, msg = (verify() if code == 0 else (False, f"退出码 {code}"))
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {dur:.1f}s  {msg}  (日志: {os.path.relpath(log, ROOT)})", flush=True)
        if not ok:
            tail = open(log, encoding="utf-8", errors="replace").read()[-1200:]
            print("  ---- 日志尾部 ----\n" + tail)
        results.append((tid, name, status, dur, msg))

    print("\n================ 测试汇总 ================")
    for tid, name, status, dur, msg in results:
        print(f"{tid:<3} {name:<28} {status:<5} {dur:6.1f}s  {msg}")
    passed = sum(1 for r in results if r[2] == "PASS")
    print(f"通过 {passed}/{len(results)}")

    if not args.keep:
        print("清理测试实例…")
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    return 0 if passed == len(results) else 1


def check_mods(inst: str) -> tuple[bool, str]:
    mods = os.path.join(inst, "mods")
    if not os.path.isdir(mods):
        return False, "缺少 mods 目录"
    files = [f for f in os.listdir(mods) if f.endswith(".jar")]
    if not files:
        return False, "mods 目录为空"
    return True, f"{len(files)} 个模组: " + ", ".join(sorted(files)[:4])


def find_create_version(cmd: list[str]) -> list[str]:
    """挑选适配 1.21.1 的 Create 6.0.5 版本。"""
    import requests
    H = {"User-Agent": "mcdl/0.1"}
    vs = requests.get("https://api.modrinth.com/v2/project/create/version", headers=H,
                      timeout=30).json()
    pick = None
    for v in vs:
        if "1.21.1" in v["game_versions"] and "6.0.5" in v["version_number"]:
            pick = v
            break
    if not pick:
        for v in vs:
            if "1.21.1" in v["game_versions"]:
                pick = v
                break
    out = list(cmd)
    i = out.index("--version")
    out[i + 1] = pick["id"] if pick else ""
    print(f"  Create 版本：{pick['version_number'] if pick else '自动'}")
    return out


def pick_modpack(cmd: list[str]) -> list[str] | None:
    import requests
    H = {"User-Agent": "mcdl/0.1"}
    hits = requests.get("https://api.modrinth.com/v2/search",
                        params={"query": "skyblock", "limit": 1,
                                "facets": json.dumps([["project_type:modpack"]])},
                        headers=H, timeout=30).json().get("hits", [])
    if not hits:
        return None
    slug = hits[0]["slug"]
    out = list(cmd)
    i = out.index("--project")
    out[i + 1] = slug
    print(f"  整合包：{slug}")
    return out


if __name__ == "__main__":
    sys.exit(main())
