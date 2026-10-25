"""程序入口。"""
import os
import sys


def main():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        os.chdir(base)
    from app.gui import run
    run()


if __name__ == "__main__":
    main()
