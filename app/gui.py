"""图形界面（tkinter）。"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import ctypes
from tkinter import filedialog, messagebox, ttk

from . import config, installer, loaders
from .curseforge import CurseForge
from .mojang import Mojang
from .modrinth import LOADER_MAP, Modrinth, version_label
from .net import Downloader, Progress, human_size

BG = "#15171e"
BG2 = "#1e212b"
BG3 = "#2a2f3d"
FG = "#e9edf5"
MUTED = "#8b93a7"
ACCENT = "#4da3ff"
OK = "#7ee0b8"

# 高 DPI：让窗口按物理像素渲染（否则 Windows 拉伸位图 → 模糊）
SCALE = 1.0


def enable_dpi() -> float:
    """开启 per-monitor DPI 感知，返回缩放倍率。"""
    global SCALE
    if sys.platform != "win32":
        SCALE = 1.0
        return SCALE
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:  # noqa: BLE001
        dpi = 96
    SCALE = max(1.0, dpi / 96.0)
    return SCALE


def px(n: float) -> int:
    """按系统缩放换算像素。"""
    return max(1, int(round(n * SCALE)))


FS = 10  # 基础字号（pt）
FONT = ("Microsoft YaHei UI", FS)
FONT_B = ("Microsoft YaHei UI", FS, "bold")
FONT_S = ("Microsoft YaHei UI", FS - 1)
FONT_T = ("Microsoft YaHei UI", FS + 5, "bold")


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)


# 模块导入即开启 DPI 感知（必须在创建任何 Tk 窗口前）
enable_dpi()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Minecraft 下载器  ——  实例 / 模组 / 整合包 / 光影 / 材质 / 数据包")
        try:
            self.tk.call("tk", "scaling", 96 * SCALE / 72.0)
        except tk.TclError:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(1200), sw - px(40)), min(px(800), sh - px(80))
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        self.minsize(min(px(980), sw - px(20)), min(px(700), sh - px(60)))
        self.configure(bg=BG)
        try:
            ico = resource_path("icon.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except tk.TclError:
            pass

        self.cfg = config.load_config()
        self.mr: Modrinth | None = None
        self.cf: CurseForge | None = None
        self.dl: Downloader | None = None
        self.mj: Mojang | None = None
        self.instances: list[config.Instance] = []
        self.current: config.Instance | None = None
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        self._style()
        self._build()
        self._reload_engines()
        self._reload_dirs()
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- 基础 ----------------
    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG2,
                        font=FONT, bordercolor="#333846", focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TRadiobutton", background=BG, foreground=FG)
        style.configure("TButton", background=BG3, foreground=FG,
                        padding=(px(12), px(5)), borderwidth=0, focusthickness=0)
        style.map("TButton",
                  background=[("pressed", "#1b5f9c"), ("active", "#3b6ea8"), ("disabled", "#232733")],
                  foreground=[("disabled", "#5b6272")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#0b1725",
                        font=FONT_B, padding=(px(16), px(6)), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("pressed", "#2f8be0"), ("active", "#7dc0ff")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG2, foreground=MUTED,
                        padding=(px(20), px(8)), font=FONT, borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", BG)], foreground=[("selected", ACCENT)],
                  expand=[("selected", (0, 0, 0, 0))])
        style.configure("Treeview", background=BG2, fieldbackground=BG2, foreground=FG,
                        rowheight=px(28), borderwidth=0, font=FONT)
        style.configure("Treeview.Heading", background="#2b3140", foreground=FG,
                        font=FONT_B, padding=(px(8), px(6)), relief="flat")
        style.map("Treeview.Heading", background=[("active", "#38404f")])
        style.map("Treeview", background=[("selected", "#2b5f92")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TEntry", fieldbackground=BG2, foreground=FG, insertcolor=FG,
                        padding=px(4), borderwidth=1)
        style.configure("TCombobox", fieldbackground=BG2, foreground=FG, background=BG3,
                        arrowcolor=FG, padding=px(3), borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", BG2)],
                  selectbackground=[("readonly", BG2)], selectforeground=[("readonly", FG)])
        style.configure("TSpinbox", fieldbackground=BG2, foreground=FG, arrowcolor=FG)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG2,
                        bordercolor=BG2, lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(8))
        style.configure("TLabelframe", background=BG, foreground=ACCENT, borderwidth=1,
                        relief="solid", bordercolor="#2b3140")
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=FONT_B)
        style.configure("TScrollbar", background=BG3, troughcolor=BG, arrowcolor=MUTED,
                        borderwidth=0, width=px(12))
        style.map("TScrollbar", background=[("active", "#3b4252")])
        style.configure("TSeparator", background="#2b3140")

    def _build(self):
        top = ttk.Frame(self, padding=(px(16), px(12), px(16), px(6)))
        top.pack(fill="x")
        ttk.Label(top, text="⚡ Minecraft 下载器", font=FONT_T, foreground=ACCENT).pack(side="left")
        self.lbl_top = ttk.Label(top, text="", foreground=MUTED, font=FONT_S)
        self.lbl_top.pack(side="right")

        bar = ttk.Frame(self, padding=(px(16), 0, px(16), px(8)))
        bar.pack(fill="x")
        ttk.Label(bar, text="实例目录").pack(side="left")
        self.cb_dir = ttk.Combobox(bar, width=34, state="readonly", values=[])
        self.cb_dir.pack(side="left", padx=(6, 4))
        self.cb_dir.bind("<<ComboboxSelected>>", lambda e: self._on_dir_change())
        ttk.Button(bar, text="添加目录…", command=self._add_dir).pack(side="left", padx=2)
        ttk.Button(bar, text="打开", command=self._open_dir).pack(side="left", padx=2)

        ttk.Label(bar, text="  当前实例").pack(side="left")
        self.cb_inst = ttk.Combobox(bar, width=30, state="readonly", values=[])
        self.cb_inst.pack(side="left", padx=6)
        self.cb_inst.bind("<<ComboboxSelected>>", lambda e: self._on_inst_change())
        ttk.Button(bar, text="刷新", command=self._reload_instances).pack(side="left", padx=2)

        ttk.Label(bar, text="  内容来源").pack(side="left")
        self.cb_source = ttk.Combobox(bar, width=14, state="readonly",
                                      values=["Modrinth", "CurseForge"])
        self.cb_source.pack(side="left", padx=6)
        self.cb_source.bind("<<ComboboxSelected>>", lambda e: self._on_source_change())

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=px(16), pady=(0, px(8)))
        self.tab_game = GameTab(self.nb, self)
        self.tab_mod = ProjectTab(self.nb, self, "mod")
        self.tab_pack = ModpackTab(self.nb, self)
        self.tab_shader = ProjectTab(self.nb, self, "shader")
        self.tab_rp = ProjectTab(self.nb, self, "resourcepack")
        self.tab_dp = ProjectTab(self.nb, self, "datapack")
        self.tab_set = SettingsTab(self.nb, self)
        for tab, name in ((self.tab_game, "游戏实例"), (self.tab_mod, "模组"),
                          (self.tab_pack, "整合包"), (self.tab_shader, "光影包"),
                          (self.tab_rp, "材质包"), (self.tab_dp, "数据包"),
                          (self.tab_set, "设置")):
            self.nb.add(tab, text=name)

        status = ttk.Frame(self, padding=(px(16), 0, px(16), px(6)))
        status.pack(fill="x")
        self.pb = ttk.Progressbar(status, mode="determinate", maximum=100, length=px(300))
        self.pb.pack(side="left")
        self.lbl_status = ttk.Label(status, text="就绪", foreground=MUTED, font=FONT_S)
        self.lbl_status.pack(side="left", padx=px(12))
        self.lbl_speed = ttk.Label(status, text="", foreground=OK, font=FONT_S)
        self.lbl_speed.pack(side="right")

        logf = ttk.LabelFrame(self, text=" 日志 ", padding=px(6))
        logf.pack(fill="both", padx=px(16), pady=(0, px(12)))
        self.log_box = tk.Text(logf, height=8, bg="#101218", fg="#cfd6e4", insertbackground=FG,
                               font=("Consolas", FS - 1), relief="flat", wrap="none",
                               padx=px(6), pady=px(4), borderwidth=0, highlightthickness=0)
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------------- 引擎 ----------------
    def _reload_engines(self):
        prog = Progress(on_update=lambda p: self.q.put(("prog", p.percent(), p.summary())))
        self.dl = Downloader(threads=self.cfg["threads"], limit_kbps=self.cfg["limit_kbps"],
                             use_mirror=self.cfg.get("use_mirror", True),
                             logger=lambda m: self.q.put(("log", m)), progress=prog)
        self.mj = Mojang(self.dl, lambda m: self.q.put(("log", m)))
        self.mr = Modrinth(self.dl)
        self.cf = CurseForge(self.dl, self.cfg.get("cf_api_key", ""))
        self.cb_source.set("CurseForge" if self.cfg.get("source") == "curseforge" else "Modrinth")

    def provider(self, kind: str = ""):
        if kind == "datapack":
            return self.mr  # CurseForge 没有独立的数据包分类
        if self.cfg.get("source") == "curseforge" and self.cf:
            return self.cf
        return self.mr

    def _on_source_change(self):
        self.cfg["source"] = "curseforge" if self.cb_source.get() == "CurseForge" else "modrinth"
        config.save_config(self.cfg)
        self.log(f"内容来源切换为 {self.cb_source.get()}")

    # ---------------- 目录 / 实例 ----------------
    def _reload_dirs(self):
        dirs = self.cfg.get("instance_dirs") or []
        self.cb_dir["values"] = dirs
        active = self.cfg.get("active_dir") or (dirs[0] if dirs else "")
        if active not in dirs and dirs:
            active = dirs[0]
        self.cfg["active_dir"] = active
        if active:
            self.cb_dir.set(active)
        self._reload_instances()

    def _reload_instances(self, keep: str = ""):
        base = self.cfg.get("active_dir") or ""
        self.instances = config.list_instances(base) if base else []
        names = [str(i) for i in self.instances]
        self.cb_inst["values"] = names
        target = keep or self.cfg.get("active_instance") or ""
        idx = 0
        for i, inst in enumerate(self.instances):
            if inst.path == target:
                idx = i
                break
        if names:
            self.cb_inst.current(idx)
            self.current = self.instances[idx]
            self.cfg["active_instance"] = self.current.path
            self.lbl_top.configure(text=f"{self.current.mc_version} · {self.current.loader}")
        else:
            self.cb_inst.set("")
            self.current = None
            self.lbl_top.configure(text="未选择实例")
        self._refresh_world_list()

    def _on_dir_change(self):
        self.cfg["active_dir"] = self.cb_dir.get()
        self.cfg["active_instance"] = ""
        config.save_config(self.cfg)
        self._reload_instances()

    def _on_inst_change(self):
        i = self.cb_inst.current()
        if 0 <= i < len(self.instances):
            self.current = self.instances[i]
            self.cfg["active_instance"] = self.current.path
            config.save_config(self.cfg)
            self.lbl_top.configure(text=f"{self.current.mc_version} · {self.current.loader}")
            self._refresh_world_list()

    def _refresh_world_list(self):
        self.tab_dp.refresh_worlds()

    def _add_dir(self):
        d = filedialog.askdirectory(title="选择实例目录（其下每个子文件夹视为一个实例）")
        if not d:
            return
        d = os.path.abspath(d)
        dirs = self.cfg["instance_dirs"]
        if d not in dirs:
            dirs.append(d)
        self.cfg["active_dir"] = d
        config.save_config(self.cfg)
        self._reload_dirs()

    def _open_dir(self):
        base = self.cfg.get("active_dir")
        if base and os.path.isdir(base):
            os.startfile(base)  # noqa: S606

    # ---------------- 后台任务 ----------------
    def run_bg(self, fn, done_text: str = "完成", reset_progress: bool = True):
        if self.busy:
            messagebox.showinfo("请稍等", "当前有任务正在进行")
            return
        self.busy = True
        self.pb["value"] = 0
        self.lbl_status.configure(text="开始…")
        if reset_progress and self.dl:
            self.dl.progress.reset()

        def wrap():
            ok = True
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                ok = False
                self.q.put(("log", f"[错误] {e}"))
            self.q.put(("done", ok, done_text))

        threading.Thread(target=wrap, daemon=True).start()

    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "prog":
                    self.pb["value"] = msg[1]
                    self.lbl_status.configure(text=f"下载中 {msg[1]:.1f}%")
                    self.lbl_speed.configure(text=msg[2])
                elif kind == "done":
                    ok, text = msg[1], msg[2]
                    self.busy = False
                    self.pb["value"] = 100 if ok else self.pb["value"]
                    self.lbl_status.configure(text=text if ok else "失败，详见日志")
                    self.lbl_speed.configure(text="")
                    self._reload_instances()
                elif kind == "call":
                    msg[1]()
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _append_log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {text}\n")
        self.log_box.see("end")
        while int(self.log_box.index("end-1c").split(".")[0]) > 800:
            self.log_box.delete("1.0", "2.0")

    def log(self, text: str):
        self.q.put(("log", text))

    def _on_close(self):
        try:
            config.save_config(self.cfg)
        except OSError:
            pass
        self.destroy()


class GameTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, padding=px(12))
        self.app = app
        self.versions: list[dict] = []
        self.filtered: list[dict] = []

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="类型").pack(side="left")
        self.cb_type = ttk.Combobox(row, values=["正式版", "含快照", "全部"], width=10, state="readonly")
        self.cb_type.current(1)
        self.cb_type.pack(side="left", padx=(4, 10))
        self.cb_type.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())
        ttk.Label(row, text="搜索版本").pack(side="left")
        self.e_search = ttk.Entry(row, width=24)
        self.e_search.pack(side="left", padx=4)
        self.e_search.bind("<KeyRelease>", lambda e: self.apply_filter())
        ttk.Button(row, text="刷新清单", command=self.load_versions).pack(side="left", padx=6)

        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, pady=8)
        self.lb = tk.Listbox(box, bg=BG2, fg=FG, selectbackground="#2f5f86", relief="flat",
                             font=("Consolas", 9), activestyle="none")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", lambda e: self._on_pick())

        form = ttk.LabelFrame(self, text="安装选项", padding=8)
        form.pack(fill="x")
        line = ttk.Frame(form)
        line.pack(fill="x", pady=2)
        ttk.Label(line, text="加载器").pack(side="left")
        self.loader_var = tk.StringVar(value="原版")
        for name in ("原版", "Fabric", "Forge", "NeoForge"):
            ttk.Radiobutton(line, text=name, value=name, variable=self.loader_var,
                            command=self.on_loader_change).pack(side="left", padx=2)
        ttk.Label(line, text="   加载器版本").pack(side="left")
        self.cb_lv = ttk.Combobox(line, width=22, values=[])
        self.cb_lv.pack(side="left", padx=4)

        line2 = ttk.Frame(form)
        line2.pack(fill="x", pady=4)
        ttk.Label(line2, text="实例名称").pack(side="left")
        self.e_name = ttk.Entry(line2, width=30)
        self.e_name.pack(side="left", padx=4)
        self.var_assets = tk.BooleanVar(value=True)
        ttk.Checkbutton(line2, text="下载资源文件（assets，推荐）",
                        variable=self.var_assets).pack(side="left", padx=12)
        ttk.Button(line2, text="下载并安装", style="Accent.TButton",
                   command=self.install).pack(side="right")

        self.lbl_hint = ttk.Label(self, text="提示：实例将创建在当前选中的实例目录下", foreground="#9aa")
        self.lbl_hint.pack(fill="x", pady=(6, 0))
        self.app.after(600, self.load_versions)

    def _fetch(self):
        self.versions = self.app.mj.versions()
        self.app.q.put(("call", self.apply_filter))

    def load_versions(self):
        self.app.run_bg(self._fetch, "版本清单已更新", reset_progress=False)

    def apply_filter(self):
        mode = self.cb_type.get()
        kw = self.e_search.get().strip().lower()
        out = []
        for v in self.versions:
            t = v.get("type")
            if mode == "正式版" and t != "release":
                continue
            if mode == "含快照" and t not in ("release", "snapshot"):
                continue
            if kw and kw not in v["id"].lower():
                continue
            out.append(v)
        self.filtered = out[:400]
        self.lb.delete(0, "end")
        for v in self.filtered:
            tag = {"release": "正式", "snapshot": "快照"}.get(v.get("type"), v.get("type", ""))
            self.lb.insert("end", f"  {v['id']:<24} {tag:<4} {v['releaseTime'][:10]}")
        if self.filtered:
            self.lb.selection_set(0)
            self._on_pick()

    def _on_pick(self):
        sel = self.lb.curselection()
        if not sel:
            return
        v = self.filtered[sel[0]]
        self.e_name.delete(0, "end")
        self.e_name.insert(0, v["id"])
        self.on_loader_change()

    def on_loader_change(self):
        if not self.cb_lv.winfo_exists():
            return
        loader = self.loader_var.get()
        sel = self.lb.curselection()
        if not sel or loader == "原版":
            self.cb_lv["values"] = []
            self.cb_lv.set("")
            return
        mc = self.filtered[sel[0]]["id"] if sel else ""

        def work():
            try:
                if loader == "Fabric":
                    vers = loaders.fabric_loader_versions(self.app.dl, mc)
                elif loader == "Forge":
                    vers = loaders.forge_versions(self.app.dl, mc)
                else:
                    vers = loaders.neoforge_versions(self.app.dl, mc)
            except Exception as e:  # noqa: BLE001
                self.app.log(f"获取加载器版本失败：{e}")
                vers = []

            def apply():
                self.cb_lv["values"] = vers[:60]
                if vers:
                    self.cb_lv.set(vers[0])
                else:
                    self.cb_lv.set("")
                    self.app.log(f"{mc} 没有可用的 {loader} 版本")
            self.app.q.put(("call", apply))

        threading.Thread(target=work, daemon=True).start()

    def install(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个游戏版本")
            return
        v = self.filtered[sel[0]]
        base = self.app.cfg.get("active_dir")
        if not base:
            messagebox.showinfo("提示", "请先添加实例目录")
            return
        name = self.e_name.get().strip() or v["id"]
        loader = self.loader_var.get()
        lv = self.cb_lv.get().strip() if loader != "原版" else ""
        mj, dl = self.app.mj, self.app.dl

        def work():
            installer.install_game(dl, mj, base, name, v["id"], loader, lv,
                                   include_assets=self.var_assets.get(), log=self.app.log)
            self.app.cfg["active_instance"] = os.path.join(base, name)
            config.save_config(self.app.cfg)

        self.app.run_bg(work, f"实例 {name} 安装完成")


class ProjectTab(ttk.Frame):
    """模组 / 光影 / 材质 / 数据包 通用搜索安装页。"""

    TITLES = {"mod": "模组", "shader": "光影包", "resourcepack": "材质包", "datapack": "数据包"}

    def __init__(self, parent, app: App, kind: str):
        super().__init__(parent, padding=px(12))
        self.app = app
        self.kind = kind
        self.hits: list[dict] = []
        self.versions: list[dict] = []
        title = self.TITLES.get(kind, kind)

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text=f"搜索{title}").pack(side="left")
        self.e_query = ttk.Entry(row, width=34)
        self.e_query.pack(side="left", padx=6)
        self.e_query.bind("<Return>", lambda e: self.search())
        ttk.Button(row, text="搜索", style="Accent.TButton", command=self.search).pack(side="left")
        self.lbl_for = ttk.Label(row, text="", foreground="#9aa")
        self.lbl_for.pack(side="left", padx=10)

        cols = ("title", "dl", "desc")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=12)
        for c, t, w in (("title", "名称", 240), ("dl", "下载量", 90), ("desc", "简介", 520)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "desc"))
        self.tree.tag_configure("odd", background=BG2)
        self.tree.tag_configure("even", background="#242938")
        self.tree.pack(fill="both", expand=True, pady=8)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.load_versions())

        bot = ttk.Frame(self)
        bot.pack(fill="x")
        ttk.Label(bot, text="版本").pack(side="left")
        self.cb_ver = ttk.Combobox(bot, width=52, values=[])
        self.cb_ver.pack(side="left", padx=6)
        self.var_deps = tk.BooleanVar(value=True)
        if kind == "mod":
            ttk.Checkbutton(bot, text="自动下载前置", variable=self.var_deps).pack(side="left", padx=8)
        self.lbl_world = ttk.Label(bot, text="  安装到存档")
        self.cb_world = ttk.Combobox(bot, width=22, values=["（实例根目录 datapacks）"])
        if kind == "datapack":
            self.lbl_world.pack(side="left")
            self.cb_world.pack(side="left", padx=4)
        ttk.Button(bot, text=f"安装到当前实例", style="Accent.TButton",
                   command=self.install).pack(side="right")
        self.lbl_info = ttk.Label(self, text="", foreground="#9aa")
        self.lbl_info.pack(fill="x", pady=(6, 0))

    # ---------- 搜索 ----------
    def search(self, page: int = 0):
        q = self.e_query.get().strip()
        inst = self.app.current
        mc = inst.mc_version if inst else ""
        loader = LOADER_MAP.get(inst.loader, "") if inst else ""
        if self.kind != "mod":
            loader = ""
        prov = self.app.provider(self.kind)
        self.lbl_for.configure(text=f"（已按 {mc or '全部版本'} 筛选）" if mc else "（未选择实例，显示全部）")

        def work():
            try:
                hits = prov.search(q, self.kind, 30, page * 30, mc, loader).get("hits", [])
            except Exception as e:  # noqa: BLE001
                self.app.log(f"搜索失败：{e}")
                hits = []
            self.app.q.put(("call", lambda: self.show(hits)))

        self.app.run_bg(work, "搜索完成", reset_progress=False)

    def show(self, hits: list[dict]):
        self.hits = hits
        self.tree.delete(*self.tree.get_children())
        for i, h in enumerate(hits):
            desc = (h.get("description") or "").replace("\n", " ")[:120]
            self.tree.insert("", "end", iid=h["project_id"], tags=("odd" if i % 2 else "even",),
                             values=(h.get("title"), f"{h.get('downloads', 0):,}", desc))
        self.lbl_info.configure(text=f"共 {len(hits)} 个结果")

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        pid = sel[0]
        return next((h for h in self.hits if h["project_id"] == pid), None)

    def load_versions(self):
        hit = self._selected()
        if not hit:
            return
        inst = self.app.current
        mc = inst.mc_version if inst else ""
        loader = LOADER_MAP.get(inst.loader, "") if inst else ""
        if self.kind != "mod":
            loader = ""
        pid = hit["project_id"]
        prov = self.app.provider(self.kind)

        def work():
            try:
                vers = prov.versions(pid, mc, loader)
                if not vers:
                    vers = prov.versions(pid, "", loader)
                if not vers:
                    vers = prov.versions(pid)
            except Exception as e:  # noqa: BLE001
                self.app.log(f"获取版本失败：{e}")
                vers = []

            def apply():
                self.versions = vers
                self.cb_ver["values"] = [version_label(v) for v in vers[:80]]
                if vers:
                    self.cb_ver.current(0)
            self.app.q.put(("call", apply))

        threading.Thread(target=work, daemon=True).start()

    def install(self):
        hit = self._selected()
        if not hit:
            messagebox.showinfo("提示", "请先搜索并选择一个项目")
            return
        if not self.app.current:
            messagebox.showinfo("提示", "请先在顶部选择要安装到的实例")
            return
        idx = self.cb_ver.current()
        vid = self.versions[idx]["id"] if 0 <= idx < len(self.versions) else ""
        world = ""
        if self.kind == "datapack":
            w = self.cb_world.get()
            world = "" if w.startswith("（") else w
        inst = self.app.current
        prov, dl = self.app.provider(self.kind), self.app.dl
        kind, deps = self.kind, self.var_deps.get()

        def work():
            installer.install_content(dl, prov, inst.path, kind, hit["project_id"], vid,
                                      with_deps=deps, world=world, log=self.app.log)

        self.app.run_bg(work, f"已安装到 {inst.name}")

    def refresh_worlds(self):
        worlds = ["（实例根目录 datapacks）"]
        if self.app.current:
            sd = os.path.join(self.app.current.path, "saves")
            if os.path.isdir(sd):
                worlds += [d for d in sorted(os.listdir(sd)) if os.path.isdir(os.path.join(sd, d))]
        self.cb_world["values"] = worlds
        if self.cb_world.get() not in worlds:
            self.cb_world.current(0)


class ModpackTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, padding=px(12))
        self.app = app
        self.hits: list[dict] = []
        self.versions: list[dict] = []

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="搜索整合包").pack(side="left")
        self.e_query = ttk.Entry(row, width=34)
        self.e_query.pack(side="left", padx=6)
        self.e_query.bind("<Return>", lambda e: self.search())
        ttk.Button(row, text="搜索", style="Accent.TButton", command=self.search).pack(side="left")

        cols = ("title", "dl", "desc")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=12)
        for c, t, w in (("title", "名称", 240), ("dl", "下载量", 90), ("desc", "简介", 520)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "desc"))
        self.tree.tag_configure("odd", background=BG2)
        self.tree.tag_configure("even", background="#242938")
        self.tree.pack(fill="both", expand=True, pady=8)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.load_versions())

        bot = ttk.Frame(self)
        bot.pack(fill="x")
        ttk.Label(bot, text="版本").pack(side="left")
        self.cb_ver = ttk.Combobox(bot, width=54, values=[])
        self.cb_ver.pack(side="left", padx=6)
        self.var_loader = tk.BooleanVar(value=True)
        ttk.Checkbutton(bot, text="实例缺少游戏/加载器时自动安装", variable=self.var_loader).pack(
            side="left", padx=8)
        ttk.Button(bot, text="安装到当前实例", style="Accent.TButton",
                   command=self.install).pack(side="right")
        ttk.Label(self, foreground="#9aa",
                  text="整合包将解压安装到当前选中实例文件夹（mods/config 等直接写入）。").pack(
            fill="x", pady=(6, 0))

    def search(self):
        q = self.e_query.get().strip()
        prov = self.app.provider("modpack")

        def work():
            try:
                hits = prov.search(q, "modpack", 30, 0).get("hits", [])
            except Exception as e:  # noqa: BLE001
                self.app.log(f"搜索失败：{e}")
                hits = []
            self.app.q.put(("call", lambda: self.show(hits)))

        self.app.run_bg(work, "搜索完成", reset_progress=False)

    def show(self, hits):
        self.hits = hits
        self.tree.delete(*self.tree.get_children())
        for i, h in enumerate(hits):
            self.tree.insert("", "end", iid=h["project_id"], tags=("odd" if i % 2 else "even",),
                             values=(h.get("title"), f"{h.get('downloads', 0):,}",
                                     (h.get("description") or "")[:120]))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((h for h in self.hits if h["project_id"] == sel[0]), None)

    def load_versions(self):
        hit = self._selected()
        if not hit:
            return
        inst = self.app.current
        mc = inst.mc_version if inst else ""
        prov = self.app.provider("modpack")

        def work():
            try:
                vers = prov.versions(hit["project_id"], mc, "")
                if not vers:
                    vers = prov.versions(hit["project_id"])
            except Exception as e:  # noqa: BLE001
                self.app.log(f"获取版本失败：{e}")
                vers = []

            def apply():
                self.versions = vers
                self.cb_ver["values"] = [version_label(v) for v in vers[:80]]
                if vers:
                    self.cb_ver.current(0)
            self.app.q.put(("call", apply))

        threading.Thread(target=work, daemon=True).start()

    def install(self):
        hit = self._selected()
        if not hit:
            messagebox.showinfo("提示", "请先搜索并选择一个整合包")
            return
        if not self.app.current:
            messagebox.showinfo("提示", "请先在顶部选择实例（整合包会安装到该实例目录）")
            return
        idx = self.cb_ver.current()
        if not (0 <= idx < len(self.versions)):
            messagebox.showinfo("提示", "请选择整合包版本")
            return
        ver = self.versions[idx]
        inst_path = self.app.current.path
        auto = self.var_loader.get()
        prov = self.app.provider("modpack")

        def work():
            info = installer.install_modpack(self.app.dl, self.app.mj, prov, inst_path,
                                             ver, auto, log=self.app.log)
            self.app.log(f"整合包 {info['name']} 安装到 {inst_path}")

        self.app.run_bg(work, f"整合包 {ver.get('name')} 安装完成")


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, padding=px(16))
        self.app = app
        ttk.Label(self, text="实例目录", font=FONT_B, foreground=ACCENT).pack(anchor="w")
        ttk.Label(self, text="添加后，目录下的每个子文件夹都会被视为一个游戏实例。",
                  foreground="#9aa").pack(anchor="w", pady=(0, 6))
        self.lb = tk.Listbox(self, height=6, bg=BG2, fg=FG, relief="flat",
                             selectbackground="#2f5f86", font=FONT)
        self.lb.pack(fill="x")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="添加目录…", command=self.add).pack(side="left")
        ttk.Button(row, text="移除选中", command=self.remove).pack(side="left", padx=6)
        ttk.Button(row, text="设为当前", command=self.use).pack(side="left")

        ttk.Separator(self).pack(fill="x", pady=12)
        grid = ttk.Frame(self)
        grid.pack(fill="x", anchor="w")
        ttk.Label(grid, text="下载线程数").grid(row=0, column=0, sticky="w", pady=4)
        self.sp_threads = ttk.Spinbox(grid, from_=1, to=64, width=8)
        self.sp_threads.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(grid, text="1 ~ 64，默认 16", foreground="#9aa").grid(row=0, column=2, sticky="w")

        ttk.Label(grid, text="限速 (KB/s)").grid(row=1, column=0, sticky="w", pady=4)
        self.e_limit = ttk.Entry(grid, width=10)
        self.e_limit.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(grid, text="整数，0 表示不限速", foreground="#9aa").grid(row=1, column=2, sticky="w")

        ttk.Label(grid, text="下载源").grid(row=2, column=0, sticky="w", pady=4)
        self.var_mirror = tk.BooleanVar(value=True)
        srcbox = ttk.Frame(grid)
        srcbox.grid(row=2, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Radiobutton(srcbox, text="镜像加速（BMCLAPI，国内推荐）", value=True,
                        variable=self.var_mirror).pack(side="left")
        ttk.Radiobutton(srcbox, text="官方源", value=False,
                        variable=self.var_mirror).pack(side="left", padx=10)

        ttk.Label(grid, text="CurseForge Key").grid(row=3, column=0, sticky="w", pady=4)
        self.e_cfkey = ttk.Entry(grid, width=44, show="•")
        self.e_cfkey.grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(grid, text="可选，留空使用国内镜像（无需 Key）",
                  foreground="#9aa").grid(row=3, column=2, sticky="w")

        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=14)
        ttk.Button(row2, text="保存设置", style="Accent.TButton", command=self.save).pack(side="left")
        self.lbl_saved = ttk.Label(row2, text="", foreground="#7fb")
        self.lbl_saved.pack(side="left", padx=10)

        ttk.Label(self, foreground="#9aa",
                  text="配置保存于：" + config.CONFIG_PATH).pack(anchor="w", side="bottom")
        self.reload()

    def reload(self):
        self.lb.delete(0, "end")
        for d in self.app.cfg.get("instance_dirs", []):
            self.lb.insert("end", d)
        self.sp_threads.delete(0, "end")
        self.sp_threads.insert(0, str(self.app.cfg.get("threads", 16)))
        self.e_limit.delete(0, "end")
        self.e_limit.insert(0, str(self.app.cfg.get("limit_kbps", 0)))
        self.var_mirror.set(bool(self.app.cfg.get("use_mirror", True)))
        self.e_cfkey.delete(0, "end")
        self.e_cfkey.insert(0, self.app.cfg.get("cf_api_key", ""))

    def add(self):
        d = filedialog.askdirectory(title="选择实例目录")
        if not d:
            return
        d = os.path.abspath(d)
        if d not in self.app.cfg["instance_dirs"]:
            self.app.cfg["instance_dirs"].append(d)
            config.save_config(self.app.cfg)
            self.reload()
            self.app._reload_dirs()

    def remove(self):
        sel = self.lb.curselection()
        if not sel:
            return
        d = self.lb.get(sel[0])
        if d == self.app.cfg.get("default_dir"):
            messagebox.showinfo("提示", "默认实例目录不能移除")
            return
        self.app.cfg["instance_dirs"].remove(d)
        if self.app.cfg.get("active_dir") == d:
            self.app.cfg["active_dir"] = self.app.cfg["instance_dirs"][0] if self.app.cfg["instance_dirs"] else ""
        config.save_config(self.app.cfg)
        self.reload()
        self.app._reload_dirs()

    def use(self):
        sel = self.lb.curselection()
        if not sel:
            return
        self.app.cfg["active_dir"] = self.lb.get(sel[0])
        self.app.cfg["active_instance"] = ""
        config.save_config(self.app.cfg)
        self.app._reload_dirs()

    def save(self):
        try:
            threads = int(self.sp_threads.get())
        except ValueError:
            threads = 16
        threads = max(1, min(64, threads))
        try:
            limit = int(self.e_limit.get() or 0)
        except ValueError:
            limit = 0
        limit = max(0, limit)
        self.app.cfg["threads"] = threads
        self.app.cfg["limit_kbps"] = limit
        self.app.cfg["use_mirror"] = bool(self.var_mirror.get())
        self.app.cfg["cf_api_key"] = self.e_cfkey.get().strip()
        config.save_config(self.app.cfg)
        self.app._reload_engines()
        self.lbl_saved.configure(text=f"已保存（{time.strftime('%H:%M:%S')}）")
        self.app.log(f"设置已保存：{threads} 线程，限速 {limit or '不限'} KB/s，"
                     f"{'镜像' if self.var_mirror.get() else '官方'}源")
        self.reload()


def run():
    enable_dpi()
    app = App()
    app.mainloop()
