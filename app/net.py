"""网络层：镜像加速、多线程下载、限速、断点续传。"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

import requests
import urllib3

urllib3.disable_warnings()

UA = "MCDownloader/1.0 (by zhulingxi; +https://modrinth.com)"
CHUNK = 64 * 1024

# BMCLAPI 镜像前缀
MIRROR = "https://bmclapi2.bangbang93.com"

_REPLACEMENTS = [
    ("https://piston-meta.mojang.com", MIRROR),
    ("https://launchermeta.mojang.com", MIRROR),
    ("https://launcher.mojang.com", MIRROR),
    ("https://piston-data.mojang.com", MIRROR),
    ("https://resources.download.minecraft.net", MIRROR + "/assets"),
    ("https://libraries.minecraft.net", MIRROR + "/maven"),
    ("https://maven.neoforged.net/releases", MIRROR + "/maven"),
    ("https://maven.neoforged.net", MIRROR + "/maven"),
    ("https://maven.minecraftforge.net", MIRROR + "/maven"),
    ("https://files.minecraftforge.net/maven", MIRROR + "/maven"),
    ("https://maven.fabricmc.net", MIRROR + "/maven"),
    ("https://meta.fabricmc.net", MIRROR + "/fabric-meta"),
]


def mirror_url(url: str) -> str:
    for src, dst in _REPLACEMENTS:
        if url.startswith(src):
            return dst + url[len(src):]
    return url


_MAVEN_MARKERS = ("/releases/", "/maven2/", "/repository/public/", "/maven/", "/maven-public/")
MAVEN_FALLBACKS = (
    "https://maven.aliyun.com/repository/public/",
    "https://repo1.maven.org/maven2/",
)


def maven_relpath(url: str) -> Optional[str]:
    """从任意 maven 仓库地址里取出 artifact 相对路径。"""
    for host in ("libraries.minecraft.net/", "maven.fabricmc.net/",
                 "maven.neoforged.net/releases/", "maven.minecraftforge.net/",
                 "files.minecraftforge.net/maven/"):
        if host in url:
            return url.split(host, 1)[1]
    for marker in _MAVEN_MARKERS:
        if marker in url:
            return url.split(marker, 1)[1]
    return None


def sha1_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}GB"


class RateLimiter:
    """全局限速器（令牌桶简化版，按字节匀速放行）。"""

    def __init__(self, limit_kbps: int = 0):
        self.rate = max(0, int(limit_kbps or 0)) * 1024
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def set_limit(self, limit_kbps: int):
        with self._lock:
            self.rate = max(0, int(limit_kbps or 0)) * 1024

    def consume(self, nbytes: int):
        if not self.rate:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + nbytes / self.rate
            wait = self._next - now
        if wait > 0.002:
            time.sleep(wait)


class Progress:
    """线程安全的总体进度。"""

    def __init__(self, on_update: Optional[Callable[["Progress"], None]] = None):
        self.lock = threading.Lock()
        self.total = 0
        self.done = 0
        self.files_total = 0
        self.files_done = 0
        self.skipped = 0
        self.current = ""
        self.on_update = on_update
        self._last = 0.0
        self._t0 = time.monotonic()
        self._bytes_t0 = 0
        self.speed = 0.0
        self.failed = []

    def add_total(self, n: int, files: int = 1):
        with self.lock:
            self.total += n
            self.files_total += files
        self._tick(force=True)

    def advance(self, n: int, current: str = ""):
        with self.lock:
            self.done += n
            if current:
                self.current = current
        self._tick()

    def file_done(self, skipped: bool = False):
        with self.lock:
            self.files_done += 1
            if skipped:
                self.skipped += 1
        self._tick(force=True)

    def _tick(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last < 0.2:
            return
        self._last = now
        dt = now - self._t0
        if dt > 0.5:
            self.speed = (self.done - self._bytes_t0) / dt
            self._t0 = now
            self._bytes_t0 = self.done
        if self.on_update:
            try:
                self.on_update(self)
            except Exception:
                pass

    def percent(self) -> float:
        if not self.total:
            return 0.0
        return min(100.0, self.done * 100.0 / self.total)

    def summary(self) -> str:
        return (f"{human_size(self.done)} / {human_size(self.total)}  "
                f"{self.files_done}/{self.files_total} 个文件  "
                f"{human_size(self.speed)}/s")

    def reset(self):
        with self.lock:
            self.total = 0
            self.done = 0
            self.files_total = 0
            self.files_done = 0
            self.skipped = 0
            self.current = ""
            self.failed = []
        self._t0 = time.monotonic()
        self._bytes_t0 = 0
        self.speed = 0.0


class Downloader:
    """多线程下载器，支持镜像回退、限速、进度回调。"""

    def __init__(self, threads: int = 8, limit_kbps: int = 0,
                 use_mirror: bool = True, logger: Optional[Callable[[str], None]] = None,
                 progress: Optional[Progress] = None):
        self.threads = max(1, min(64, int(threads)))
        self.use_mirror = use_mirror
        self.limiter = RateLimiter(limit_kbps)
        self.log = logger or (lambda m: None)
        self.progress = progress or Progress()
        self._local = threading.local()
        self._lock = threading.Lock()
        self.mirror_failures = 0
        self.mirror_bad = False

    def _note_mirror(self, url: str, ok: bool):
        if "bmclapi" not in url:
            return
        with self._lock:
            if ok:
                self.mirror_failures = 0
                return
            self.mirror_failures += 1
            if self.mirror_failures >= 12 and not self.mirror_bad:
                self.mirror_bad = True
                self.log("镜像站响应异常，本次任务自动切换官方源")

    # ---------- 基础 ----------
    def _session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": UA})
            self._local.s = s
        return s

    def candidates(self, url) -> list[str]:
        if isinstance(url, (list, tuple)):
            seq = [u for u in url if u]
        else:
            seq = [url]
        out: list[str] = []
        for u in seq:
            m = mirror_url(u)
            if self.use_mirror:
                out.append(m)
                if m != u:
                    out.append(u)
            else:
                out.append(u)
                if m != u:
                    out.append(m)
            rel = maven_relpath(u)
            if rel:
                out.extend(base + rel for base in MAVEN_FALLBACKS)
        seen, uniq = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        if self.mirror_bad:
            uniq.sort(key=lambda x: 1 if "bmclapi" in x else 0)
        return uniq

    def get(self, url, timeout=60, params=None, headers=None) -> requests.Response:
        last = None
        tmo = (15, timeout) if isinstance(timeout, (int, float)) else timeout
        for u in self.candidates(url):
            verify = True
            for attempt in range(3):
                try:
                    r = self._session().get(u, timeout=tmo, params=params, headers=headers,
                                            verify=verify)
                    if r.status_code < 400:
                        return r
                    last = RuntimeError(f"HTTP {r.status_code} @ {u}")
                    if r.status_code in (401, 403, 404):
                        break
                except requests.exceptions.SSLError as e:
                    last = e
                    verify = False  # 镜像站证书链经常不全
                    continue
                except Exception as e:  # noqa: BLE001
                    last = e
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"请求失败: {url} ({last})")

    def get_json(self, url, timeout: int = 60, params=None):
        last = None
        for u in self.candidates(url):
            verify = True
            for _ in range(2):
                try:
                    r = self._session().get(u, timeout=(15, timeout), params=params, verify=verify)
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    return r.json()
                except requests.exceptions.SSLError as e:
                    last = e
                    verify = False
                except ValueError as e:  # 非 JSON（镜像错误页）
                    last = e
                    self._note_mirror(u, False)
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
                    break
        raise RuntimeError(f"获取 JSON 失败: {url} ({last})")

    def get_bytes(self, url: str, timeout: int = 120) -> bytes:
        return self.get(url, timeout=timeout).content

    # ---------- 单文件 ----------
    def download(self, url: str, dest: str, sha1: Optional[str] = None,
                 size: Optional[int] = None, label: str = "", optional: bool = False) -> bool:
        label = label or os.path.basename(dest)
        dest = os.path.abspath(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if os.path.exists(dest):
            try:
                ok = (size is None or os.path.getsize(dest) == size)
                if ok and sha1:
                    ok = sha1_file(dest) == sha1.lower()
                if ok:
                    self.progress.add_total(os.path.getsize(dest))
                    with self.progress.lock:
                        self.progress.done += os.path.getsize(dest)
                    self.progress.file_done(skipped=True)
                    return True
            except OSError:
                pass

        self.progress.add_total(size or 0)
        last_err = None
        verify = True
        for u in self.candidates(url):
            for attempt in range(2):
                try:
                    self._fetch_to(u, dest, sha1, size, label, verify=verify)
                    self._note_mirror(u, True)
                    return True
                except requests.exceptions.SSLError as e:
                    last_err = e
                    verify = False
                    continue
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    self._note_mirror(u, False)
                    self.log(f"  重试 {label}: {e}")
                    time.sleep(0.8 * (attempt + 1))
        if optional:
            self.log(f"  跳过（可选）: {label} - {last_err}")
            self.progress.file_done()
            return False
        self.progress.failed.append(label)
        raise RuntimeError(f"下载失败 {label}: {last_err}")

    def _fetch_to(self, url: str, dest: str, sha1: Optional[str], size: Optional[int],
                  label: str, verify: bool = True):
        tmp = dest + ".part"
        h = hashlib.sha1()
        got = 0
        t_start = time.monotonic()
        t_last = t_start
        self.progress.advance(0, label)
        with self._session().get(url, stream=True, timeout=(15, 90), verify=verify) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    now = time.monotonic()
                    if now - t_last > 1:
                        t_last = now
                    elapsed = now - t_start
                    # 低速保护：连接建立 25 秒后仍低于 20KB/s 直接换源重试
                    if elapsed > 25 and got / elapsed < 20 * 1024 and (size or 0) > 256 * 1024:
                        raise RuntimeError(f"下载过慢 {got / elapsed / 1024:.0f}KB/s")
                    self.limiter.consume(len(chunk))
                    self.progress.advance(len(chunk), label)
        if size is not None and got != size:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise RuntimeError(f"大小不符 {got}!={size}")
        if sha1 and h.hexdigest() != sha1.lower():
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise RuntimeError("SHA1 校验失败")
        os.replace(tmp, dest)
        self.progress.file_done()

    # ---------- 批量 ----------
    def download_many(self, items: Iterable[dict], desc: str = "") -> list[str]:
        """items: {url, dest, sha1, size, label, optional}"""
        items = list(items)
        if not items:
            return []
        if desc:
            self.log(f"{desc}（{len(items)} 个文件，{self.threads} 线程）")
        errors: list[str] = []

        def work(it):
            try:
                src = it.get("url") or it.get("urls")
                self.download(src, it["dest"], it.get("sha1"), it.get("size"),
                              it.get("label", ""), it.get("optional", False))
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                self.log("  " + str(e))

        if self.threads == 1 or len(items) <= 2:
            for it in items:
                work(it)
        else:
            with ThreadPoolExecutor(max_workers=min(self.threads, len(items))) as ex:
                futs = [ex.submit(work, it) for it in items]
                for f in as_completed(futs):
                    f.result()
        if errors:
            self.log(f"完成，其中 {len(errors)} 个失败")
        return errors

    def set_threads(self, n: int):
        self.threads = max(1, min(64, int(n)))

    def set_limit(self, kbps: int):
        self.limiter.set_limit(kbps)
