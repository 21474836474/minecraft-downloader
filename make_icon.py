"""生成程序图标（草方块 + 下载箭头），输出 icon.ico / icon.png。"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

OUT = os.path.dirname(os.path.abspath(__file__))
SIZE = 256


def make(size: int = SIZE) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 16.0  # 以 16x16 网格绘制

    def rect(x, y, w, h, color):
        d.rectangle([x * s, y * s, (x + w) * s - 1, (y + h) * s - 1], fill=color)

    # 圆角底
    d.rounded_rectangle([1 * s, 1 * s, 15 * s, 15 * s], radius=2.2 * s, fill=(30, 32, 42, 255))

    # 草方块：上层草绿 + 下层泥土
    top, bottom = 3, 9
    rect(3, top, 10, 3, (95, 175, 74))
    rect(3, top, 10, 1, (122, 205, 96))
    rect(3, top + 3, 10, 4, (121, 85, 58))
    # 泥土颗粒
    for (x, y) in ((4, 10), (7, 11), (10, 10), (5, 12), (9, 12), (11, 11)):
        rect(x, y, 1, 1, (100, 70, 48))
    # 草叶边缘
    for x in (3, 5, 7, 9, 11):
        rect(x, top + 2, 1, 1, (85, 160, 66))

    # 下载箭头
    arrow = (240, 246, 255)
    rect(7, 2, 2, 3, arrow)
    for i, w in enumerate((5, 3, 1)):
        rect(8 - w // 2, 5 + i, w, 1, arrow)
    return img


def main():
    img = make()
    img.save(os.path.join(OUT, "icon.png"))
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(os.path.join(OUT, "icon.ico"), sizes=sizes)
    print("icon.ico / icon.png 已生成")


if __name__ == "__main__":
    main()
