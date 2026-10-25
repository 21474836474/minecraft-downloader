"""CurseForge 渠道测试：模组安装 + 整合包安装（All the Mods 9 Sky）。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(ROOT, "_test")
LOGS = os.path.join(TEST, "logs")
PY = sys.executable

ATM9_SKY = "967745"      # All the Mods 9 - To the Sky - atm9sky
JEI = "238222"           # Just Enough Items


def cli(args, log_name, timeout=7200):
    os.makedirs(LOGS, exist_ok=True)
    path = os.path.join(LOGS, log_name)
    cmd = [PY, "-m", "app.cli"] + args
    t0 = time.time()
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return time.time() - t0, path


def main():
    inst_dir = os.path.join(TEST, "cf")
    os.makedirs(inst_dir, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else ""

    results = []

    if not only or only == "cf1":
        print("===== [cf1] CurseForge 模组：JEI → 1.21.1 NeoForge 实例", flush=True)
        target = os.path.join(TEST, "inst", "1.21.1-neoforge")
        dur, log = cli(["--source", "curseforge", "--threads", "16", "install", "--kind", "mod",
                        "--instance", target, "--project", JEI], "cf1.log")
        mods = os.path.join(target, "mods")
        jars = [f for f in os.listdir(mods) if f.endswith(".jar")] if os.path.isdir(mods) else []
        ok = any("jei" in j.lower() for j in jars)
        print(f"  {'PASS' if ok else 'FAIL'}  {dur:.1f}s  mods={jars[:5]}", flush=True)
        results.append(("cf1", ok))

    if not only or only == "cf2":
        print("===== [cf2] CurseForge 整合包：All the Mods 9 Sky", flush=True)
        inst = os.path.join(inst_dir, "atm9sky")
        dur, log = cli(["--source", "curseforge", "--threads", "32", "install-modpack",
                        "--instance", inst, "--project", ATM9_SKY], "cf2.log", timeout=10800)
        mods = os.path.join(inst, "mods")
        jars = [f for f in os.listdir(mods) if f.endswith(".jar")] if os.path.isdir(mods) else []
        has_forge = os.path.isdir(os.path.join(inst, "versions")) and any(
            "forge" in v for v in os.listdir(os.path.join(inst, "versions")))
        ok = len(jars) > 100 and has_forge
        print(f"  {'PASS' if ok else 'FAIL'}  {dur:.1f}s  模组 {len(jars)} 个，"
              f"Forge 版本={has_forge}", flush=True)
        if jars:
            print("  示例:", ", ".join(sorted(jars)[:5]))
        results.append(("cf2", ok))

    print("\n汇总：", ", ".join(f"{n}={'PASS' if ok else 'FAIL'}" for n, ok in results))
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
