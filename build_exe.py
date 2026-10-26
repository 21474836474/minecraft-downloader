"""打包为单文件 exe（带图标、无控制台窗口）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Minecraft下载器"


def main():
    os.chdir(HERE)
    if not os.path.exists("icon.ico"):
        subprocess.run([sys.executable, "make_icon.py"], check=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", os.path.join(HERE, "icon.ico"),
        "--add-data", os.path.join(HERE, "icon.ico") + os.pathsep + ".",
        "--collect-submodules", "app",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL.ImageTk",
        "--exclude-module", "tkinter.test",
        os.path.join(HERE, "main.py"),
    ]
    print(" ".join(args))
    subprocess.run(args, check=True)

    exe = os.path.join(HERE, "dist", NAME + ".exe")
    size = os.path.getsize(exe) / 1024 / 1024
    print(f"生成：{exe}  ({size:.1f} MB)")
    return exe


if __name__ == "__main__":
    main()
