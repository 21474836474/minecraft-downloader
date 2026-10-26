"""创建 GitHub Release 并上传 exe。"""
import hashlib
import os
import subprocess

import requests

requests.packages.urllib3.disable_warnings()

OWNER, REPO = "21474836474", "minecraft-downloader"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "Minecraft下载器.exe")
ASSET = "MinecraftDownloader-v1.0.exe"


def token() -> str:
    out = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True).stdout
    return [ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("password=")][0]


def main():
    tok = token()
    h = {"User-Agent": "mcdl", "Accept": "application/vnd.github+json",
         "Authorization": "Bearer " + tok}
    sha = hashlib.sha256(open(EXE, "rb").read()).hexdigest()
    size = os.path.getsize(EXE)
    body = (
        "## Minecraft 下载器 v1.0\n\n"
        "**Windows 单文件 exe，双击即用，含图标，不提供游戏启动。**\n\n"
        "### 功能\n"
        "- 游戏实例：原版 / Fabric / Forge / NeoForge（自实现 install_profile，不依赖官方安装器联网）\n"
        "- 模组：自动递归下载必需前置\n"
        "- 整合包：Modrinth `.mrpack` + CurseForge zip，直接装进当前实例\n"
        "- 光影 / 材质 / 数据包\n"
        "- 设置：实例目录增删选、线程 1~64、限速 KB/s、镜像/官方源、CurseForge Key\n\n"
        "### 本次更新\n"
        "- 界面重制：开启 Per-Monitor DPI 感知，125%/150% 缩放下不再模糊（1920x1200 按物理像素渲染）\n"
        "- 字号/间距按系统缩放换算，列表斑马纹、窗口图标、配色重做\n\n"
        "### 文件\n"
        f"- `{ASSET}`（{size / 1048576:.2f} MB，程序内名称 Minecraft下载器.exe）\n"
        f"- SHA256: `{sha}`\n"
    )
    r = requests.post(API + "/releases", headers=h, timeout=60, verify=False, json={
        "tag_name": "v1.0", "target_commitish": "main", "name": "v1.0",
        "body": body, "draft": False, "prerelease": False})
    print("release:", r.status_code)
    if r.status_code == 422:
        rel = requests.get(API + "/releases/tags/v1.0", headers=h, timeout=60, verify=False).json()
    elif r.status_code >= 300:
        print(r.text[:400])
        return 1
    else:
        rel = r.json()
    rid = rel["id"]
    print("  url:", rel["html_url"])

    # 清理旧资产（含被 GitHub 去掉中文的 Minecraft.exe）
    rel = requests.get(f"{API}/releases/{rid}", headers=h, timeout=60, verify=False).json()
    for a in rel.get("assets", []):
        requests.delete(f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/{a['id']}",
                        headers=h, timeout=60, verify=False)
        print("  removed old asset:", a["name"])

    up = (f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{rid}/assets"
          f"?name={ASSET}")
    with open(EXE, "rb") as f:
        rr = requests.post(up, headers={"Authorization": "Bearer " + tok, "User-Agent": "mcdl",
                                        "Content-Type": "application/octet-stream"},
                           data=f, timeout=1800, verify=False)
    print("upload:", rr.status_code, rr.json().get("size"), rr.json().get("state"))
    print("download:", rr.json().get("browser_download_url"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
