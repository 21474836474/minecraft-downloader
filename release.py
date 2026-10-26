"""创建 GitHub Release 并上传 exe。

用法：python release.py v1.0.1 "更新说明"
"""
import hashlib
import os
import subprocess
import sys

import requests

requests.packages.urllib3.disable_warnings()

OWNER, REPO = "21474836474", "minecraft-downloader"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "Minecraft下载器.exe")

DEFAULT_NOTES = (
    "### 本次更新\n"
    "- 新增**已有模组拦截**：按 jar 内 modId（fabric.mod.json / mods.toml / neoforge.mods.toml / mcmod.info）识别同款模组\n"
    "- 冲突策略：替换旧版本（默认）/ 跳过，单装模组与整合包安装都生效\n"
    "- 修复不同版本模组并存导致进游戏崩溃的问题\n"
    "- 设置页新增「已有模组处理」；CLI 新增 `--on-conflict`\n"
)


def token() -> str:
    out = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True).stdout
    return [ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("password=")][0]


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v1.0.1"
    notes = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_NOTES
    asset = f"MinecraftDownloader-{tag}.exe"
    tok = token()
    h = {"User-Agent": "mcdl", "Accept": "application/vnd.github+json",
         "Authorization": "Bearer " + tok}

    sha = hashlib.sha256(open(EXE, "rb").read()).hexdigest()
    size = os.path.getsize(EXE)
    body = (f"## Minecraft 下载器 {tag}\n\n"
            "**Windows 单文件 exe，双击即用，含图标，不提供游戏启动。**\n\n"
            f"{notes}\n"
            "### 文件\n"
            f"- `{asset}`（{size / 1048576:.2f} MB，程序内名称 Minecraft下载器.exe）\n"
            f"- SHA256: `{sha}`\n")

    r = requests.post(API + "/releases", headers=h, timeout=60, verify=False, json={
        "tag_name": tag, "target_commitish": "main", "name": tag,
        "body": body, "draft": False, "prerelease": False})
    print("release:", r.status_code)
    if r.status_code == 422:
        rel = requests.get(f"{API}/releases/tags/{tag}", headers=h, timeout=60, verify=False).json()
    elif r.status_code >= 300:
        print(r.text[:400])
        return 1
    else:
        rel = r.json()
    rid = rel["id"]
    print("  url:", rel["html_url"])

    up = f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{rid}/assets?name={asset}"
    with open(EXE, "rb") as f:
        rr = requests.post(up, headers={"Authorization": "Bearer " + tok, "User-Agent": "mcdl",
                                        "Content-Type": "application/octet-stream"},
                           data=f, timeout=1800, verify=False)
    print("upload:", rr.status_code, rr.json().get("size"), rr.json().get("state"))
    print("download:", rr.json().get("browser_download_url"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
