"""打包成品到 D:\\下载\\Minecraft下载器\\"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Minecraft下载器"
OUT = os.path.join(r"D:\下载", NAME)


def main():
    exe = os.path.join(HERE, "dist", NAME + ".exe")
    if not os.path.exists(exe) or "--rebuild" in sys.argv:
        print("构建 exe…")
        subprocess.run([sys.executable, os.path.join(HERE, "build_exe.py")], check=True)

    os.makedirs(OUT, exist_ok=True)
    shutil.copy2(exe, os.path.join(OUT, NAME + ".exe"))
    shutil.copy2(os.path.join(HERE, "README.md"), os.path.join(OUT, "使用说明.md"))
    shutil.copy2(os.path.join(HERE, "icon.ico"), os.path.join(OUT, "icon.ico"))
    print(f"已输出到：{OUT}")
    for f in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, f)
        print(f"  {f:<24} {os.path.getsize(p) / 1024 / 1024:6.2f} MB")
    return OUT


if __name__ == "__main__":
    main()
