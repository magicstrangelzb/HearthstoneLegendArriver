# -*- coding: utf-8 -*-
"""炉石存活检测：进程是否还在 + Power.log 是否还在更新。

背景（issue 反馈）：挂机期间炉石在换牌界面闪退，脚本完全没察觉，继续对失效
画面做 OCR 重试，白白空转近 3 小时，直到手动停止。原因是原来的“炉石是否可用”
只看窗口标题（`get_screen.test_hs_available`），而窗口句柄在进程崩溃后可能
残留/判断不到，对局中的 OCR 重试循环也不会退出。

本模块给出两个可注入、可测试的信号：

1. **进程信号（权威）**：系统进程列表里是否还有 ``Hearthstone.exe``。
   进程一旦消失（且本轮自动化期间确实见过它），就是闪退，立即判定退出。
2. **日志信号（辅助）**：最新 ``Power.log`` 的 mtime 距今多久。进程还在但游戏
   卡死（画面冻结）时进程信号不会报警，这时日志会停止增长。只在“对局中”参考
   该信号，因为匹配对手/选职业阶段日志本来就安静，用它会误判。

判定分两档：先告警（``fatal=False``，只提示），持续更久才判定退出
（``fatal=True`` → 调用方自动停止自动化并醒目告警）。

所有外部依赖（进程枚举、日志文件、时钟、配置）都可注入，便于单元测试。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

# 炉石主进程名（Windows 进程列表里的大小写不固定，统一小写比较）。
HEARTHSTONE_PROCESS_NAMES = frozenset({"hearthstone.exe", "hearthstone"})
# 进程/配置的缓存时长（秒）：主循环每 0.2s 就会问一次，没必要每次都枚举进程
# （枚举一次要遍历全部进程）或读一遍 ui_config.json。
_CACHE_SECONDS = 2.0

_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260


def list_process_names() -> Optional[set[str]]:
    """返回当前所有进程的可执行文件名（小写）；枚举失败返回 None。

    只用 ctypes 调 Windows API（CreateToolhelp32Snapshot），不引入新依赖。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * _MAX_PATH),
        ]

    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return None
    try:
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                      wintypes.DWORD]
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                             ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(PROCESSENTRY32W)]
        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    except Exception:
        return None
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid:
        return None

    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            if name:
                names.add(str(name).lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    except Exception:
        return None
    finally:
        try:
            kernel32.CloseHandle(snapshot)
        except Exception:
            pass
    return names


def hearthstone_process_running(
        process_lister: Callable[[], Optional[Iterable[str]]] = list_process_names,
        process_names: Iterable[str] = HEARTHSTONE_PROCESS_NAMES
) -> Optional[bool]:
    """炉石进程是否在运行；无法枚举进程时返回 None（表示“不知道”）。

    返回 None 时调用方必须当作“无法判定”，绝不能当成“已退出”——否则在没有
    权限枚举进程的机器上会一启动就把自动化停掉。
    """
    try:
        names = process_lister()
    except Exception:
        return None
    if names is None:
        return None
    wanted = {str(n).lower() for n in process_names}
    return any(str(n).lower() in wanted for n in names)


def _default_power_log_path() -> Optional[Path]:
    """当前应关注的最新 Power.log（跟随运行时可能被改写的日志目录）。"""
    try:
        import log_op
        from power_log import find_latest_power_log
        return find_latest_power_log(log_op.HEARTHSTONE_LOG_ROOT)
    except Exception:
        return None


def _default_clock() -> float:
    return time.monotonic()


def _default_settings() -> dict:
    try:
        from config import liveness_settings
        return liveness_settings()
    except Exception:
        return {"enabled": True, "process_grace_seconds": 6.0,
                "log_stale_warn_seconds": 120.0,
                "log_stale_stop_seconds": 300.0}


class HearthstoneLiveness:
    """炉石存活检测器（线程安全；Web 界面与状态机共用一个实例）。

    用法：::

        monitor = HearthstoneLiveness()
        event = monitor.check(in_game=True)
        if event and event["fatal"]:
            ...  # 醒目告警 + 自动停止

    ``sample()`` 只读取当前状态，供日志浮窗/网页显示（不产生告警、不改变
    “曾经见过进程”这类判定状态）。
    """

    def __init__(self,
                 process_checker: Optional[Callable[[], Optional[bool]]] = None,
                 log_path_provider: Optional[Callable[[], Optional[Path]]] = None,
                 settings_provider: Optional[Callable[[], dict]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 file_age: Optional[Callable[[Path], Optional[float]]] = None,
                 cache_seconds: float = _CACHE_SECONDS):
        self._process_checker = process_checker or hearthstone_process_running
        self._log_path_provider = log_path_provider or _default_power_log_path
        self._settings_provider = settings_provider or _default_settings
        self._clock = clock or _default_clock
        self._file_age = file_age or self._path_age
        self._cache_seconds = max(0.0, float(cache_seconds))
        self._lock = threading.RLock()
        # 本轮自动化期间是否见过炉石进程（没见过就没有“消失”可言）。
        self._saw_process = False
        self._missing_since: Optional[float] = None
        # 已上报过的告警：避免每 0.2s 刷屏；reset() 时清空。
        self._reported: set[str] = set()
        self._last_alert: Optional[str] = None
        self._cache_at = -1e9
        self._cache_process: Optional[bool] = None
        self._cache_settings_at = -1e9
        self._cache_settings: dict = dict(_default_settings())

    # ------------------------------------------------------------ 基础采样
    @staticmethod
    def _path_age(path: Path) -> Optional[float]:
        try:
            return max(0.0, time.time() - os.path.getmtime(path))
        except Exception:
            return None

    def reset(self) -> None:
        """新一轮自动化开始（或重新检测）时调用：忘记上一轮的判定。"""
        with self._lock:
            self._saw_process = False
            self._missing_since = None
            self._reported.clear()
            self._last_alert = None
            self._cache_process = None
            self._cache_at = -1e9

    def settings(self) -> dict:
        with self._lock:
            now = self._clock()
            if now - self._cache_settings_at < self._cache_seconds:
                return dict(self._cache_settings)
        try:
            cfg = dict(self._settings_provider() or {})
        except Exception:
            cfg = dict(_default_settings())
        with self._lock:
            self._cache_settings = cfg
            self._cache_settings_at = self._clock()
            return dict(cfg)

    def process_running(self) -> Optional[bool]:
        """炉石进程是否在运行（带缓存）；None = 无法判定。"""
        with self._lock:
            now = self._clock()
            if now - self._cache_at < self._cache_seconds:
                return self._cache_process
        try:
            running = self._process_checker()
        except Exception:
            running = None
        with self._lock:
            self._cache_process = running
            self._cache_at = self._clock()
            return running

    def log_age(self) -> Optional[float]:
        """最新 Power.log 距今多久没有更新（秒）；没有日志文件返回 None。"""
        try:
            path = self._log_path_provider()
        except Exception:
            path = None
        if not path:
            return None
        try:
            return self._file_age(Path(path))
        except Exception:
            return None

    def sample(self, in_game: bool = False) -> dict:
        """当前状态快照（供浮窗/网页显示，不产生任何告警副作用）。

        ``in_game`` 与 :meth:`check` 同义：只有对局中才会把 Power.log 停滞
        当成异常，否则主菜单/匹配阶段“日志本来就安静”会被误显示成无响应。
        """
        cfg = self.settings()
        running = self.process_running()
        age = self.log_age()
        with self._lock:
            saw = self._saw_process
            alert = self._last_alert
        if not cfg.get("enabled", True):
            status = "disabled"
        elif running is None:
            status = "unknown"
        elif not running:
            status = "gone" if saw else "idle"
        else:
            warn_at = float(cfg.get("log_stale_warn_seconds", 120.0))
            stop_at = float(cfg.get("log_stale_stop_seconds", 300.0))
            if age is None or not in_game:
                status = "ok"
            elif age >= stop_at:
                status = "stale"
            elif age >= warn_at:
                status = "warning"
            else:
                status = "ok"
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "status": status,
            "process": ("running" if running else
                        ("stopped" if running is False else "unknown")),
            "saw_process": saw,
            "in_game": bool(in_game),
            "log_age": age,
            "log_stale_warn_seconds": float(cfg.get("log_stale_warn_seconds", 120.0)),
            "log_stale_stop_seconds": float(cfg.get("log_stale_stop_seconds", 300.0)),
            "alert": alert,
        }

    # ------------------------------------------------------------ 判定
    def _event(self, kind: str, fatal: bool, message: str) -> Optional[dict]:
        """按 kind 去重：同一种情况只上报一次，避免主循环刷屏。"""
        with self._lock:
            if kind in self._reported:
                return None
            self._reported.add(kind)
            if fatal:
                self._last_alert = message
        return {"kind": kind, "fatal": bool(fatal), "message": message}

    def check(self, in_game: bool = False, detail: str = "") -> Optional[dict]:
        """检测一次；返回需要处理的事件（``None`` = 一切正常）。

        ``in_game`` 为 True 时才参考 Power.log 停滞信号。``detail`` 是调用方
        补充的上下文（例如最近一次自动化诊断），会拼进告警文案里，便于事后
        判断“到底卡在哪一步”。
        """
        cfg = self.settings()
        if not cfg.get("enabled", True):
            return None
        now = self._clock()
        running = self.process_running()
        with self._lock:
            if running:
                self._saw_process = True
                self._missing_since = None
            elif running is False and self._saw_process:
                if self._missing_since is None:
                    self._missing_since = now
                gone_for = now - self._missing_since
                grace = float(cfg.get("process_grace_seconds", 6.0))
                if gone_for >= grace:
                    message = (
                        f"炉石已退出：Hearthstone.exe 进程消失"
                        f"（已确认 {gone_for:.0f}s 未回来）")
                    return self._event("process_gone", True, message)

        if in_game:
            age = self.log_age()
            if age is not None:
                warn_at = float(cfg.get("log_stale_warn_seconds", 120.0))
                stop_at = float(cfg.get("log_stale_stop_seconds", 300.0))
                tail = f"（{detail}）" if detail else ""
                if age >= stop_at:
                    return self._event(
                        "log_stale_stop", True,
                        f"炉石疑似无响应：对局中 Power.log 已 {age:.0f}s 没有新内容"
                        f"（超过 {stop_at:.0f}s 阈值）{tail}")
                if age >= warn_at:
                    return self._event(
                        "log_stale_warn", False,
                        f"炉石疑似无响应：对局中 Power.log 已 {age:.0f}s 没有新内容"
                        f"（超过 {warn_at:.0f}s 告警阈值，达到 "
                        f"{stop_at:.0f}s 将自动停止）{tail}")
        return None


_DEFAULT_MONITOR: Optional[HearthstoneLiveness] = None
_MONITOR_LOCK = threading.Lock()


def default_monitor() -> HearthstoneLiveness:
    """进程内共享的检测器（状态机与 Web 界面看到的是同一份判定）。"""
    global _DEFAULT_MONITOR
    with _MONITOR_LOCK:
        if _DEFAULT_MONITOR is None:
            _DEFAULT_MONITOR = HearthstoneLiveness()
        return _DEFAULT_MONITOR
