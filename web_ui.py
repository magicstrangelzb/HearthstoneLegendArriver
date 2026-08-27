# -*- coding: utf-8 -*-
"""HSLegendArriver Web 控制台（零依赖后端）。

仅使用 Python 标准库：本地 HTTP 服务 + 自动化线程管理 + 定时调度 + 实时日志。
前端页面位于 web/index.html，启动后自动在浏览器打开。

用法:
    python web_ui.py [--no-browser]

功能:
    * 页面配置用户 ID 与炉石日志目录（写入 constants 并持久化到 ui_config.json）
    * 定时任务：开始时间不能早于当前时间；到达结束时间后，
      先等本局对战打完（状态机回到非对局状态）再自动终止自动化
    * 「开始对战」按钮立即启动，「本局结束后停止」「立即停止」手动控制
    * Ctrl+Q 全局热键立即停止
"""
from __future__ import annotations

import ctypes
try:
    import log_overlay
except Exception as exc:
    print(f"[overlay] 导入日志浮窗失败(已禁用): {type(exc).__name__}: {exc}")
    log_overlay = None
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
CONFIG_PATH = ROOT / "ui_config.json"
# 站点/端口/日志缓冲来自 config.py（可通过环境变量覆盖，见 HS_HOST/HS_PORT/HS_LOG_BUFFER_SIZE）。
from config import (
    DEFAULT_AUTO_CONCEDE, HOST, BASE_PORT, LOG_BUFFER_SIZE,
    _USER_DELAY_KEYS, RecommendationConfig)


# ---------------------------------------------------------------- 管理员检测
def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


IS_ADMIN = _is_admin()


# ---------------------------------------------------------------- 配置持久化
DEFAULT_CONFIG = {
    "name": "",
    "log_root": "",
    "schedule_start": None,
    "schedule_end": None,
    "auto_concede": dict(DEFAULT_AUTO_CONCEDE),
}


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_CONFIG)
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf8")
    except Exception as exc:
        _log("WARN", f"配置保存失败：{exc}")


# ---------------------------------------------------------------- 运行日志缓冲
_log_seq = 0
_log_buffer = deque(maxlen=LOG_BUFFER_SIZE)
_log_lock = threading.Lock()


def _log(level: str, msg: str):
    global _log_seq
    with _log_lock:
        _log_seq += 1
        _log_buffer.append({
            "seq": _log_seq,
            "t": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "msg": str(msg),
        })
    # 统一推入日志浮窗（关键行），与自动化日志同通道、按时间先后。
    # 浮窗若出现异常绝不能让日志系统把主流程一起拖垮。
    if log_overlay is not None and _overlay_key(str(msg)):
        try:
            log_overlay.push(str(msg), level)
        except Exception:
            pass
    # 同时落盘：程序关闭/异常退出后仍可离线查看（诊断不依赖人工复制）。
    try:
        path = ROOT / "ui_log_last.txt"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                    f"[{level}] {msg}\n")
        if path.stat().st_size > 2 * 1024 * 1024:  # 只保留最近日志
            lines = path.read_text(encoding="utf-8").splitlines()[-800:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _overlay_key(line: str) -> bool:
    keys = ("回合", "延时", "轮到己方", "等待", "[推荐]", "[执行]",
            "识别换牌", "换牌", "留牌", "阶段", "对局结束", "未对局",
            "本局结束", "失败", "立即停止")
    return ("[OCR]" not in line) and any(k in line for k in keys)


def take_logs_after(seq: int):
    with _log_lock:
        lines = [e for e in _log_buffer if e["seq"] > seq]
        return lines, _log_seq


# ---------------------------------------------------------------- stdout 捕获
_stdout_original = None


class _TeeStream:
    """把自动化线程的 print 输出同时送进 UI 日志缓冲。"""

    def __init__(self, original):
        self._original = original

    def write(self, text):
        try:
            self._original.write(text)
        except Exception:
            pass
        text = str(text)
        if text and text.strip():
            level = "INFO"
            for lv in ("ERROR", "WARN", "DEBUG", "SYS"):
                if f"{lv}]" in text:
                    level = lv
                    break
            for line in text.splitlines():
                if line.strip():
                    _log(level, line.rstrip())

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return getattr(self._original, "isatty", lambda: False)()

    def fileno(self):
        return getattr(self._original, "fileno", lambda: -1)()


def _stdout_capture_start():
    global _stdout_original
    if _stdout_original is not None:
        return
    _stdout_original = sys.stdout
    sys.stdout = _TeeStream(_stdout_original)


def _stdout_capture_stop():
    global _stdout_original
    if _stdout_original is None:
        return
    try:
        sys.stdout = _stdout_original
    finally:
        _stdout_original = None


# ---------------------------------------------------------------- 控制器
class _Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.fsm = None
        self.automation_thread = None
        self.scheduler_thread = None
        self.starting = False
        self.phase = "idle"    # idle | waiting | playing | stopping | finished
        self.stopped_by = None  # schedule | user_after_game | user_immediate | ctrlq
        self.schedule = {"start": None, "end": None}
        self.last_error = None
        self.last_summary = None
        self.hotkey_registered = False


CTRL = _Controller()


def _apply_constants(name: str, log_root: str):
    """把页面配置注入 constants，并同步到已导入模块的绑定副本。"""
    import constants.constants as cc
    if name:
        cc.YOUR_NAME = name
    if log_root:
        cc.HEARTHSTONE_LOG_ROOT = log_root
    try:
        import log_op
        import log_state
        log_op.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        log_state.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        log_state.MY_NAME = cc.YOUR_NAME
    except Exception:
        pass
    if CTRL.fsm is not None:
        CTRL.fsm.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        CTRL.fsm.log_state.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        CTRL.fsm.log_state.MY_NAME = cc.YOUR_NAME


def _persist_schedule(start_dt, end_dt):
    cfg = load_config()
    cfg["schedule_start"] = (
        start_dt.isoformat(timespec="minutes") if start_dt else None)
    cfg["schedule_end"] = (
        end_dt.isoformat(timespec="minutes") if end_dt else None)
    save_config(cfg)


# ---------------------------------------------------------------- 自动化线程
def _start_automation():
    cfg = load_config()
    name = (cfg.get("name") or "").strip()
    log_root = (cfg.get("log_root") or "").strip()
    try:
        _apply_constants(name, log_root)
        import FSM_action as fsm
        CTRL.fsm = fsm
    except Exception as exc:
        traceback.print_exc()
        return False, f"自动化组件加载失败：{exc}"
    fsm = CTRL.fsm
    # 重置运行状态；每次“打开脚本/开始对战”时战绩清零，从 0 计。
    fsm.quitting_flag = False
    fsm.stop_after_current_game = False
    fsm.FSM_state = ""
    fsm.time_begin = 0.0
    fsm.game_count = 0
    fsm.win_count = 0
    fsm.concede_count = 0
    try:
        fsm.print_info_init()
        fsm.init()
    except Exception as exc:
        traceback.print_exc()
        return False, f"自动化初始化失败：{exc}"
    CTRL.last_error = None
    CTRL.last_summary = None
    CTRL.stopped_by = None
    CTRL.phase = "playing"
    _log("SYS", f"自动化启动：用户 {name}，日志目录 {log_root}")
    if name and "#" not in name:
        _log("WARN", "用户 ID 未包含 #编号，可能无法识别己方玩家，建议填写完整战网昵称。")
    _stdout_capture_start()
    _register_hotkey()
    t = threading.Thread(target=_automation_worker, args=(fsm,),
                         name="hs-automation", daemon=True)
    CTRL.automation_thread = t
    t.start()
    return True, "自动化已启动"


def _automation_worker(fsm):
    summary = {"games": 0, "wins": 0}
    _bind_overlay()
    # 自动化在后台线程运行；get_screen 等模块使用 win32com / win32ui（COM），
    # COM 要求线程级初始化，否则报“尚未调用 CoInitialize”并导致线程崩溃。
    com_ready = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        com_ready = True
    except Exception:
        _log("WARN", "COM 初始化失败（自动化可能仍可运行，若崩溃请检查 pywin32）。")
    try:
        fsm.AutoHS_automata()
    except SystemExit:
        pass
    except Exception:
        tb = traceback.format_exc()
        traceback.print_exc()
        _log("ERROR", "自动化线程异常退出，堆栈如下：")
        for line in tb.rstrip().splitlines():
            _log("ERROR", "  " + line)
        with CTRL.lock:
            CTRL.last_error = "自动化运行异常：请查看页面「实时日志」中的 ERROR 详情。"
    finally:
        _stdout_capture_stop()
        _remove_hotkey()
        try:
            fsm.print_info_close()
        except Exception:
            pass
        with CTRL.lock:
            summary = {
                "games": int(fsm.game_count),
                "wins": int(fsm.win_count),
                "concedes": int(getattr(fsm, "concede_count", 0) or 0),
            }
            CTRL.last_summary = {**summary, "stopped_by": CTRL.stopped_by}
            CTRL.automation_thread = None
            if CTRL.phase == "stopping" and CTRL.stopped_by == "schedule":
                CTRL.phase = "finished"
                CTRL.schedule = {"start": None, "end": None}
                _persist_schedule(None, None)
            else:
                CTRL.phase = "idle"
        if summary["games"]:
            _log("SYS", f"自动化结束：共完成 {summary['games']} 场对战，"
                        f"赢 {summary['wins']} 场"
                        f"{'，自动认输 ' + str(summary['concedes']) + ' 场' if summary['concedes'] else ''}。")
        else:
            _log("SYS", "自动化结束。")
        if com_ready:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass


# ---------------------------------------------------------------- 热键
def _register_hotkey():
    try:
        import keyboard
        try:
            keyboard.remove_hotkey("ctrl+q")
        except Exception:
            pass
        keyboard.add_hotkey("ctrl+q", _on_ctrl_q)
        CTRL.hotkey_registered = True
        _log("SYS", "热键 Ctrl+Q 已注册（立即停止）。")
    except Exception as exc:
        CTRL.hotkey_registered = False
        _log("WARN", f"注册 Ctrl+Q 热键失败：{exc}（仍可通过页面停止）")


def _remove_hotkey():
    try:
        import keyboard
        keyboard.remove_hotkey("ctrl+q")
    except Exception:
        pass
    CTRL.hotkey_registered = False


def _on_ctrl_q():
    _log("WARN", "收到 Ctrl+Q，立即停止自动化。")
    with CTRL.lock:
        CTRL.phase = "idle"
        CTRL.stopped_by = "ctrlq"
        CTRL.schedule = {"start": None, "end": None}
    _persist_schedule(None, None)
    try:
        CTRL.fsm.request_immediate_stop()
    except Exception:
        pass


# ---------------------------------------------------------------- 定时调度
def _schedule_worker(start_dt: datetime, end_dt):
    try:
        while datetime.now() < start_dt:
            with CTRL.lock:
                if CTRL.schedule.get("start") != start_dt:
                    return  # 计划被取消或替换
            time.sleep(0.5)
        _log("SYS", "到达计划开始时间，自动启动对战。")
        ok, msg = _start_automation()
        if not ok:
            with CTRL.lock:
                CTRL.phase = "idle"
                CTRL.schedule = {"start": None, "end": None}
                CTRL.last_error = msg
            _persist_schedule(None, None)
            _log("ERROR", f"定时启动失败：{msg}")
            return
        if end_dt is None:
            return
        while datetime.now() < end_dt:
            with CTRL.lock:
                if (CTRL.schedule.get("start") != start_dt
                        or CTRL.automation_thread is None):
                    return
            time.sleep(0.5)
        _log("SYS", "到达计划结束时间：等待本局对战打完再停止。")
        with CTRL.lock:
            CTRL.phase = "stopping"
            CTRL.stopped_by = "schedule"
        try:
            CTRL.fsm.request_stop_after_game()
        except Exception as exc:
            _log("ERROR", f"发送停止请求失败：{exc}")
        # 等待自动化线程自然退出（对局结束后）
        while True:
            with CTRL.lock:
                if CTRL.schedule.get("start") != start_dt:
                    return
                t = CTRL.automation_thread
            if t is None or not t.is_alive():
                break
            time.sleep(1)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------- API 实现
STATE_LABELS = {
    "": "待机",
    "Leave Hearth Stone": "炉石未运行",
    "Wait main menu": "等待主菜单",
    "Main Menu": "主菜单",
    "Choosing Hero": "选择职业",
    "Match Opponent": "匹配对手",
    "Choosing Card": "换牌阶段",
    "Battling": "对战中",
    "Quitting Battle": "对局结算",
    "ERROR": "状态异常",
}


def api_save_config(body: dict):
    name = str(body.get("name") or "").strip()
    log_root = str(body.get("log_root") or "").strip()
    warnings = []
    if name and "#" not in name:
        warnings.append("建议填写完整战网昵称（含 #编号），否则可能无法识别己方玩家。")
    if log_root and not os.path.isdir(log_root):
        warnings.append("日志目录不存在，请确认路径正确（通常为炉石安装目录下的 Logs 文件夹）。")
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中，请先停止后再修改配置。"}
        cfg = load_config()
        cfg["name"] = name
        cfg["log_root"] = log_root
        save_config(cfg)
        _apply_constants(name, log_root)
    _log("SYS", f"配置已保存：用户 {name or '（未填写）'}，日志目录 {log_root or '（未填写）'}")
    return {"ok": True, "message": "配置已保存", "warnings": warnings}


def api_save_concede(body):
    """保存自动投降配置（ui_config.json 的 auto_concede 段）。"""
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中，请先停止后再修改自动投降配置。"}
        cfg = load_config()
        ac = dict(cfg.get("auto_concede") or DEFAULT_AUTO_CONCEDE)
        ac["enabled"] = bool(body.get("enabled", ac.get(
            "enabled", DEFAULT_AUTO_CONCEDE["enabled"])))
        try:
            threshold = float(body.get("threshold", ac.get(
                "threshold", DEFAULT_AUTO_CONCEDE["threshold"])))
        except (TypeError, ValueError):
            return {"ok": False, "error": "阈值必须为数字（0-100）。"}
        threshold = max(0.0, min(100.0, threshold))
        try:
            rounds = int(body.get("rounds", ac.get(
                "rounds", DEFAULT_AUTO_CONCEDE["rounds"])))
        except (TypeError, ValueError):
            return {"ok": False, "error": "连续回合数必须为整数（1-50）。"}
        rounds = max(1, min(50, rounds))
        ac["threshold"] = threshold
        ac["rounds"] = rounds
        cfg["auto_concede"] = ac
        save_config(cfg)
    _log("SYS", f"自动投降配置已保存：{'开启' if ac['enabled'] else '关闭'}，"
                f"阈值 {threshold:.0f}%，连续 {rounds} 回合。")
    return {"ok": True, "message": "自动投降配置已保存",
            "concede": {"enabled": ac["enabled"],
                        "threshold": threshold, "rounds": rounds}}


# ------------------------------------------------------------------ 延时设置
# 各延时字段的边界与默认值（默认值取自 RecommendationConfig，即上游时序）。
_DELAY_BOUNDS = {
    "mulligan_ready_delay_seconds": (0.0, 120.0),
    "mulligan_post_ocr_delay_seconds": (0.0, 30.0),
    "mulligan_retry_delay_seconds": (0.0, 60.0),
    "first_turn_per_card_delay_seconds": (0.0, 20.0),
    "pre_action_delay_seconds": (0.0, 60.0),
    "post_action_delay_seconds": (0.0, 10.0),
    "ocr_preprocess_scale": (0.5, 4.0),
}


def _current_delays() -> dict:
    """返回当前生效的延时配置（默认值叠加 ui_config.json 的 delays 段）。"""
    return {key: getattr(RecommendationConfig(), key)
            for key in _USER_DELAY_KEYS}


def api_save_delays(body):
    """保存延时配置（ui_config.json 的 delays 段）。"""
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中，请先停止后再修改延时配置。"}
        cfg = load_config()
        delays = dict(cfg.get("delays") or {})
        for key in _USER_DELAY_KEYS:
            if key not in body:
                continue
            try:
                value = float(body[key])
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} 必须为数字。"}
            lo, hi = _DELAY_BOUNDS[key]
            if not (lo <= value <= hi):
                return {"ok": False,
                        "error": f"{key} 必须介于 {lo}–{hi}。"}
            delays[key] = value
        cfg["delays"] = delays
        save_config(cfg)
    _log("SYS", "延时配置已保存。")
    return {"ok": True, "message": "延时配置已保存", "delays": _current_delays()}


def api_start(body: dict):
    with CTRL.lock:
        if CTRL.automation_thread is not None or CTRL.starting:
            return {"ok": False, "error": "自动化已经在运行中。"}
        cfg = load_config()
        name = (cfg.get("name") or "").strip()
        log_root = (cfg.get("log_root") or "").strip()
        if not name:
            return {"ok": False, "error": "请先填写用户 ID。"}
        if not log_root:
            return {"ok": False, "error": "请先填写炉石日志目录。"}
        if not os.path.isdir(log_root):
            return {"ok": False, "error": f"日志目录不存在：{log_root}"}
        # 手动开始会取消尚未开始的定时计划
        CTRL.schedule = {"start": None, "end": None}
    _persist_schedule(None, None)
    _log("SYS", "收到启动请求，正在初始化自动化组件（首次启动可能较慢，请稍候）……")

    def _boot():
        ok, msg = _start_automation()
        if not ok:
            with CTRL.lock:
                CTRL.last_error = msg
            _log("ERROR", msg)

    threading.Thread(target=_boot, name="hs-boot", daemon=True).start()
    return {"ok": True, "message": "正在启动自动化，请稍候……"}


def api_stop(body: dict):
    mode = str(body.get("mode") or "now")
    if mode == "after_game":
        with CTRL.lock:
            if CTRL.automation_thread is None:
                return {"ok": False, "error": "当前没有正在运行的自动化。"}
            fsm = CTRL.fsm
            CTRL.phase = "stopping"
            CTRL.stopped_by = "user_after_game"
            if CTRL.schedule.get("end"):
                CTRL.schedule = {"start": None, "end": None}
                _persist_schedule(None, None)
        try:
            fsm.request_stop_after_game()
        except Exception as exc:
            return {"ok": False, "error": f"发送停止请求失败：{exc}"}
        _log("WARN", "已请求：本局对战结束后停止。")
        return {"ok": True, "message": "本局对战结束后将自动停止"}
    with CTRL.lock:
        if CTRL.automation_thread is None:
            return {"ok": False, "error": "当前没有正在运行的自动化。"}
        fsm = CTRL.fsm
        CTRL.phase = "idle"
        CTRL.stopped_by = "user_immediate"
        CTRL.schedule = {"start": None, "end": None}
    _persist_schedule(None, None)
    try:
        fsm.request_immediate_stop()
    except Exception as exc:
        return {"ok": False, "error": f"发送停止指令失败：{exc}"}
    _log("WARN", "已发送立即停止指令。")
    return {"ok": True, "message": "正在停止……"}


def api_schedule(body: dict):
    start_text = str(body.get("start") or "").strip()
    end_text = str(body.get("end") or "").strip()
    try:
        start_dt = datetime.fromisoformat(start_text)
    except ValueError:
        return {"ok": False, "error": "开始时间格式不正确。"}
    end_dt = None
    if end_text:
        try:
            end_dt = datetime.fromisoformat(end_text)
        except ValueError:
            return {"ok": False, "error": "结束时间格式不正确。"}
    now = datetime.now()
    if start_dt < now:
        return {"ok": False, "error": "开始时间不能早于当前时间。"}
    if end_dt is not None and end_dt <= start_dt:
        return {"ok": False, "error": "结束时间必须晚于开始时间。"}
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中，请先停止后再设置定时任务。"}
    with CTRL.lock:
        CTRL.schedule = {"start": start_dt, "end": end_dt}
        CTRL.phase = "waiting"
        CTRL.stopped_by = None
        CTRL.last_summary = None
    _persist_schedule(start_dt, end_dt)
    t = threading.Thread(target=_schedule_worker, args=(start_dt, end_dt),
                         name="hs-scheduler", daemon=True)
    CTRL.scheduler_thread = t
    t.start()
    end_text = f"，{end_dt:%m-%d %H:%M} 结束" if end_dt else "，不设结束时间（运行至手动停止）"
    _log("SYS", f"定时任务已设置：{start_dt:%m-%d %H:%M} 开始{end_text}。")
    return {"ok": True, "message": "定时任务已设置，请保持本程序运行。"}


def api_calibrate():
    """启动推荐区域校准工具（绿框对齐，无实时 OCR 预览窗）。"""
    script = ROOT / "calibrate_roi.py"
    if not script.exists():
        return {"ok": False, "error": f"校准工具缺失：{script}"}
    try:
        subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
    except Exception as exc:
        return {"ok": False, "error": f"启动校准工具失败：{exc}"}
    _log("SYS", "已启动推荐区域校准：拖绿框对齐盒子面板，按 S 保存，Esc 退出。")
    return {"ok": True, "message": "校准工具已启动（无预览：拖绿框对齐盒子面板后按 S 保存，Esc 退出）"}


def _hearthstone_foreground_guard():
    """常驻守护：确保炉石主窗口在前台（复用脚本开始的置顶方式）。

    每 30 分钟检查一次当前前台窗口；若不是炉石主窗口，就用
    get_screen.move_window_foreground（同「开始对战」脚本开头的一致）
    把炉石切回最前台。只在启动 30 分钟后才做第一次校正，避免
    一打开 web 控制台就把炉石抢到前台（用户只想打开 web）。
    """
    interval = 30 * 60
    try:
        import pythoncom
        pythoncom.CoInitialize()   # move_window_foreground 用 WScript.Shell(COM)
    except Exception:
        pass
    try:
        import get_screen
        import win32gui
    except Exception as exc:
        _log("WARN", f"炉石前台守护不可用：{exc}")
        return
    while True:
        time.sleep(interval)
        try:
            hwnd = get_screen.get_HS_hwnd()
            if not hwnd:
                continue  # 炉石未运行，跳过
            if win32gui.GetForegroundWindow() == hwnd:
                continue  # 已在前台
            get_screen.move_window_foreground(hwnd)
            _log("SYS", "检测到炉石不在前台，已自动切换到前台。")
        except Exception:
            pass


# 常驻阶段监测线程维护的当前阶段标签（供浮窗“开始对战”按钮禁用判定）。
_current_stage = None


def _stage_label(ls):
    if getattr(ls, "game_entity_id", 0) == 0:
        return "未对局"
    if getattr(ls, "is_end", False):
        return "对局结束"
    try:
        turns = ls.game_num_turns_in_play
    except Exception:
        turns = 0
    if turns == 0:
        return "换牌"
    if ls.is_my_turn:
        return "我方出牌"
    return "对手回合"


def _stage_monitor_loop():
    """常驻阶段监测：独立读 Power.log，阶段变化打 -----xx阶段-----。"""
    global _current_stage
    try:
        from log_state import LogState, log_iter_func, update_state
    except Exception as exc:
        _log("WARN", f"阶段监测初始化失败：{exc}")
        return
    cfg = load_config()
    log_root = (cfg.get("log_root") or "").strip()
    if not log_root:
        return
    ls = LogState()
    try:
        li = log_iter_func(log_root)
    except Exception as exc:
        _log("WARN", f"阶段监测日志读取失败：{exc}")
        return
    last = None
    while True:
        try:
            c = next(li)
            if getattr(c, "log_type", "ERROR") == "ERROR":
                continue
            for line in c.message_list:
                try:
                    update_state(ls, line)
                except Exception:
                    pass
        except Exception:
            pass
        stage = _stage_label(ls)
        _current_stage = stage
        if stage != last:
            last = stage
            _log("SYS", f"-----{stage}阶段-----")
        time.sleep(0.3)


def _overlay_stop_after():
    """切换“本局结束后停止”：已设则取消，未设则请求本局结束后停。

    对局进行中随时可反悔（再点一次取消），无需重启脚本。
    """
    with CTRL.lock:
        fsm = CTRL.fsm
        active = bool(getattr(fsm, "stop_after_current_game", False)) \
            if fsm is not None else False
    if active:
        try:
            fsm.request_cancel_stop_after_game()
        except Exception as exc:
            _log("ERROR", f"取消「本局结束后停止」失败：{exc}")
    else:
        api_stop({"mode": "after_game"})


def _overlay_is_running():
    with CTRL.lock:
        return CTRL.automation_thread is not None


def _overlay_is_in_game():
    """对局是否已开始（换牌/出牌/对手回合），或自动化正在运行。

    供浮窗“开始对战”按钮禁用判定：对局进行中不允许再点开始，
    避免脚本被误触发而重复启动/复位。
    """
    with CTRL.lock:
        running = CTRL.automation_thread is not None
    stage = _current_stage
    if stage in ("换牌", "我方出牌", "对手回合"):
        return True
    return running


def _overlay_is_stop_after():
    with CTRL.lock:
        fsm = CTRL.fsm
        return bool(getattr(fsm, "stop_after_current_game", False)) if fsm is not None else False


def _current_score():
    """自动化战绩唯一数据源 (场数, 胜场, 自动认输数)，供浮窗与 Web 页面共用。

    fsm 尚未初始化时按 0 处理，保证浮窗与 /api/status 口径一致。
    """
    with CTRL.lock:
        fsm = CTRL.fsm
    games = int(getattr(fsm, "game_count", 0) or 0) if fsm is not None else 0
    wins = int(getattr(fsm, "win_count", 0) or 0) if fsm is not None else 0
    concedes = int(getattr(fsm, "concede_count", 0) or 0) if fsm is not None else 0
    return games, wins, concedes


def _overlay_score():
    """当前自动化战绩 (场数, 胜场, 自动认输数)。供浮窗头部显示胜负情况。"""
    return _current_score()


def _overlay_exit():
    """浮窗“退出脚本”按钮：先停止自动化，再退出整个脚本进程。"""
    try:
        with CTRL.lock:
            running = CTRL.automation_thread is not None
        if running:
            api_stop({"mode": "now"})
    except Exception:
        pass
    try:
        _log("SYS", "用户从浮窗点击“退出脚本”。")
    except Exception:
        pass
    # 立即可靠结束进程（浮窗/自动化均为 daemon 线程，os._exit 直接终止）。
    os._exit(0)


def _overlay_halt():
    with CTRL.lock:
        running = CTRL.automation_thread is not None
    if running:
        api_stop({"mode": "now"})
    else:
        api_start({})


def _bind_overlay():
    """绑定日志浮窗回调（开始/中止/本局结束后停止 + 状态查询）。

    供自动化线程第一次启动与网页“开启浮窗”共用，避免重复。
    """
    if log_overlay is None:
        return
    log_overlay.start(
        on_start=lambda: api_start({}),
        on_halt=_overlay_halt,
        is_running=_overlay_is_running,
        on_stop_after=_overlay_stop_after,
        is_stop_after=_overlay_is_stop_after,
        is_in_game=_overlay_is_in_game,
        score_callback=_overlay_score,
        on_exit=_overlay_exit,
    )


def api_toggle_overlay(body=None):
    """开/关右上角实时日志浮窗。"""
    if log_overlay is None:
        return {"ok": False, "error": "日志浮窗模块不可用"}
    if log_overlay.is_running():
        log_overlay.stop()
        return {"ok": True, "enabled": False, "message": "日志浮窗已关闭"}
    _bind_overlay()
    return {"ok": True, "enabled": True, "message": "日志浮窗已开启"}


def api_cancel_schedule():
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中；如需停止请使用停止按钮。"}
        CTRL.schedule = {"start": None, "end": None}
        CTRL.phase = "idle"
        CTRL.scheduler_thread = None
    _persist_schedule(None, None)
    _log("SYS", "定时任务已取消。")
    return {"ok": True, "message": "定时任务已取消"}


def check_log_dir(path: str):
    path = str(path or "").strip()
    if not path:
        return {"exists": False, "checked": False, "message": "请先输入日志目录。"}
    p = Path(path)
    if not p.is_dir():
        return {"exists": False, "checked": True, "message": "目录不存在，请检查路径。"}
    from power_log import find_latest_power_log, find_latest_session_dir
    session = find_latest_session_dir(p)
    plog = find_latest_power_log(p)
    result = {
        "exists": True,
        "checked": True,
        "session": session.name if session else None,
        "power_log": str(plog) if plog else None,
        "power_log_found": plog is not None,
    }
    if plog:
        result["message"] = "✅ 找到最新对局日志 Power.log"
    elif session:
        result["message"] = "找到会话目录，暂无 Power.log（进行一局对战后自动生成）"
    else:
        result["message"] = "目录存在，但未发现炉石会话子目录（Hearthstone_时间戳 形式）"
    return result


def status_snapshot():
    with CTRL.lock:
        cfg = load_config()
        fsm = CTRL.fsm
        running = CTRL.automation_thread is not None
        phase = CTRL.phase
        if running and fsm is not None and fsm.stop_after_current_game                 and phase == "playing":
            phase = "stopping"
        state = fsm.FSM_state if fsm is not None else ""
        games = int(fsm.game_count) if fsm is not None else 0
        wins = int(fsm.win_count) if fsm is not None else 0
        concedes = int(getattr(fsm, "concede_count", 0) or 0) if fsm is not None else 0
        time_begin = float(getattr(fsm, "time_begin", 0.0) or 0.0)             if fsm is not None else 0.0
        stop_after = bool(fsm.stop_after_current_game) if fsm is not None else False
        sched = CTRL.schedule
        start_iso = sched["start"].isoformat(timespec="minutes")             if sched.get("start") else None
        end_iso = sched["end"].isoformat(timespec="minutes")             if sched.get("end") else None
        err = CTRL.last_error
        summary = CTRL.last_summary
        stopped_by = CTRL.stopped_by
        hotkey_ok = CTRL.hotkey_registered
        starting = CTRL.starting
    elapsed = int(time.time() - time_begin) if time_begin > 0 else 0
    win_rate = round(wins * 100.0 / games, 1) if games else 0.0
    return {
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "is_admin": IS_ADMIN,
        "running": running,
        "starting": starting,
        "phase": phase,
        "stopped_by": stopped_by,
        "stop_after_game": stop_after,
        "state": state,
        "state_label": STATE_LABELS.get(state, state or "待机"),
        "in_game": state in ("Choosing Card", "Battling", "Quitting Battle"),
        "games": games,
        "wins": wins,
        "concede_count": concedes,
        "win_rate": win_rate,
        "game_elapsed_sec": elapsed,
        "schedule_start": start_iso,
        "schedule_end": end_iso,
        "concede": cfg.get("auto_concede") or dict(DEFAULT_AUTO_CONCEDE),
        "delays": _current_delays(),
        "config": {"name": cfg.get("name", ""), "log_root": cfg.get("log_root", "")},
        "last_error": err,
        "last_summary": summary,
        "hotkey_ok": hotkey_ok,
    }


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    server_version = "HSLegendArriver/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _json(self, payload, code=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1024 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf8"))
        except Exception:
            raise ValueError("请求体 JSON 解析失败")

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_text(encoding="utf8")
                data = html.encode("utf8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/api/status":
                self._json(status_snapshot())
            elif path == "/api/logs":
                q = parse_qs(parsed.query)
                try:
                    seq = int(q.get("seq", ["0"])[0])
                except (TypeError, ValueError):
                    seq = 0
                lines, max_seq = take_logs_after(seq)
                self._json({"seq": max_seq, "lines": lines})
            elif path == "/api/check_log":
                q = parse_qs(parsed.query)
                self._json(check_log_dir(q.get("path", [""])[0]))
            else:
                self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            body = {}
            try:
                body = self._read_json()
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            path = parsed.path
            if path == "/api/config":
                self._json(api_save_config(body))
            elif path == "/api/start":
                self._json(api_start(body))
            elif path == "/api/stop":
                self._json(api_stop(body))
            elif path == "/api/schedule":
                self._json(api_schedule(body))
            elif path == "/api/schedule/cancel":
                self._json(api_cancel_schedule())
            elif path == "/api/calibrate":
                self._json(api_calibrate())
            elif path == "/api/overlay":
                self._json(api_toggle_overlay(body))
            elif path == "/api/concede":
                self._json(api_save_concede(body))
            elif path == "/api/delays":
                self._json(api_save_delays(body))
            else:
                self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": str(exc)}, 500)


# ---------------------------------------------------------------- 启动
def _boot_resume_schedule():
    cfg = load_config()
    s, e = cfg.get("schedule_start"), cfg.get("schedule_end")
    if not s:
        return
    try:
        start_dt = datetime.fromisoformat(s)
        end_dt = datetime.fromisoformat(e) if e else None
    except Exception:
        return
    now = datetime.now()
    if now < start_dt:
        with CTRL.lock:
            CTRL.schedule = {"start": start_dt, "end": end_dt}
            CTRL.phase = "waiting"
        t = threading.Thread(target=_schedule_worker, args=(start_dt, end_dt),
                             name="hs-scheduler", daemon=True)
        CTRL.scheduler_thread = t
        t.start()
        _log("SYS", f"恢复未完成的定时任务：{start_dt:%m-%d %H:%M} 开始。")
    else:
        _persist_schedule(None, None)
        _log("WARN", "上次保存的定时任务已过期，已自动取消，请在页面重新设置。")


def main():
    os.chdir(ROOT)
    # 常驻阶段监测线程：停止自动化也继续检测当前阶段（随时可恢复）
    threading.Thread(target=_stage_monitor_loop, name="hs-stage",
                     daemon=True).start()
    # 常驻前台守护线程：每 30 分钟把炉石切回最前台
    threading.Thread(target=_hearthstone_foreground_guard,
                     name="hs-fg-guard", daemon=True).start()
    cfg = load_config()
    _apply_constants(cfg.get("name") or "", cfg.get("log_root") or "")
    _boot_resume_schedule()

    server = None
    port = None
    for port in range(BASE_PORT, BASE_PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("无法绑定端口，请检查 8765-8785 是否被占用。")
        sys.exit(1)
    threading.Thread(target=server.serve_forever, name="hs-web", daemon=True).start()

    url = f"http://{HOST}:{port}"
    print("=" * 64)
    print("  HSLegendArriver Web 控制台")
    print(f"  请在浏览器中打开: {url}")
    if not IS_ADMIN:
        print("  [!] 当前不是管理员权限，自动化鼠标/键盘操作可能失效，")
    print("      建议以管理员身份重新运行本程序。")
    print("  按 Ctrl+C 或直接关闭窗口即可退出控制台。")
    print("=" * 64)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n控制台已退出。")


if __name__ == "__main__":
    main()
