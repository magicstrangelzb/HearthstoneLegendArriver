# -*- coding: utf-8 -*-
"""Right-top live log overlay via tkinter (topmost, translucent, draggable).

start() launches a background thread with a tkinter window pinned to the
top-right corner. It streams the latest automation log lines; lines that mark
the own-turn start are highlighted green. The window can be dragged by holding
the left mouse button. Failures disable the overlay and never crash the caller.

Buttons:
  * ▶ 开始对战            — start automation
  * ⏹ 中止 / ▶ 恢复       — toggle stop/resume (state-aware)
  * ⏸ 本局结束后停止        — toggle; cancel anytime before the match ends
Every button hands the foreground back to Hearthstone afterwards.
"""
from __future__ import annotations

import datetime
import os
import re
import threading
import time
from collections import deque

_LOCK = threading.Lock()
_LINES: deque = deque(maxlen=2000)
_STARTED = [False]
_REFRESH_MS = 350

# ---- flat dark palette ---------------------------------------------------
BG = "#171b24"
PANEL = "#202634"
TITLE_BG = "#10141c"
TEXT = "#e8ecf2"
DIM = "#8b93a3"
GREEN = "#5fd68a"
ACCENT = "#4aa3ff"
DANGER = "#e05e4b"
WARN = "#d98a2e"
OK = "#2ea06b"
DISABLED = "#394050"


def _turn_start(line: str) -> bool:
    return ("回合" in line and "延时" in line) or ("轮到己方" in line)


_DELAY = None
# 浮窗只显示最近 _MAX_LINES 行（超出丢弃最旧行，仅影响显示）。
_MAX_LINES = 500
# 完整正文日志缓存：不做行数丢弃，供“保存日志”写出全部历史。
_FULL_LINES = []
_delay_start_re = re.compile(r"(?:延时|等待)\s*(\d+(?:\.\d+)?)\s*s?\s*后")
_delay_end_markers = ("延时结束", "延时完毕")


def _delay_desc(line: str) -> str:
    """从延时日志行里提炼一句人类可读的说明（供进度条下方显示）。"""
    text = _delay_start_re.sub("", line)
    text = text.replace("[SYS]", "").replace("……", "").replace("…", "")
    text = text.replace(".", "").strip().strip("：:，, ")
    return text or "延时"


def _update_delay_from_line(line: str) -> bool:
    """从日志行识别延时起点/终点，驱动浮窗底部延时进度条。

    返回 True 表示本行是延时信息（已被进度条消费，不再进正文日志）。
    """
    global _DELAY
    start = _delay_start_re.search(line)
    if start:
        total = float(start.group(1))
        if "换牌重试" in line:
            label = "换牌重试"
        elif "换牌" in line:
            label = "换牌延时"
        elif "回合" in line:
            label = "回合延时"
        else:
            label = "延时"
        desc = _delay_desc(line)
        _DELAY = {
            "label": label, "desc": desc,
            "total": total, "started": time.time(),
        }
        # <1s 的短延时（如操作后 0.5s）进度条一闪而过，仍保留在正文日志
        # 以便看清；长延时只驱动进度条、不进正文。
        return total < 1.0
    if any(marker in line for marker in _delay_end_markers):
        _DELAY = None
        return True
    return False


def push(line: str, _level: str = "INFO") -> None:
    if not _STARTED[0]:
        return
    line = str(line).rstrip()
    if not line.strip():
        return
    is_turn_start = "轮到己方" in line
    with _LOCK:
        delay_only = _update_delay_from_line(line)
        # 完整日志缓存：所有日志行（含延时行）都保留，供保存完整写出。
        _FULL_LINES.append(line)
        if delay_only:
            return  # 延时行只驱动进度条，不写浮窗正文
        # _LINES 只保留最近 _MAX_LINES 行供浮窗显示（不影响完整缓存）。
        _LINES.append((line, _turn_start(line)))
        if len(_LINES) > _MAX_LINES:
            # _LINES 是 deque，不支持切片删除，改用 popleft 丢弃最旧行。
            # 仅影响浮窗显示；保存仍用 _FULL_LINES 完整写出。
            while len(_LINES) > _MAX_LINES:
                _LINES.popleft()
    if is_turn_start:
        # 我方回合开始：把炉石唤回前台，避免 OCR 被其他窗口挡住。
        _raise_hearthstone()


def _save_log() -> str:
    """把当前对战日志写入 logs/ 子目录，返回保存路径。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"对战日志_{ts}.txt")
    with _LOCK:
        lines = list(_FULL_LINES)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 对战日志 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write("\n".join(lines))
        if lines:
            f.write("\n")
    return path


_STOP = threading.Event()


_ON_START = None
_ON_HALT = None
_IS_RUNNING = None
_ON_STOP_AFTER = None
_IS_STOP_AFTER = None
_IS_IN_GAME = None
_SCORE = None
_ON_EXIT = None


def start(on_start=None, on_halt=None, is_running=None,
          on_stop_after=None, is_stop_after=None,
          is_in_game=None, score_callback=None, on_exit=None) -> None:
    global _ON_START, _ON_HALT, _IS_RUNNING, _ON_STOP_AFTER, _IS_STOP_AFTER
    global _IS_IN_GAME, _SCORE, _ON_EXIT
    if _STARTED[0]:
        return
    _ON_START = on_start
    _ON_HALT = on_halt
    _IS_RUNNING = is_running
    _ON_STOP_AFTER = on_stop_after
    _IS_STOP_AFTER = is_stop_after
    _IS_IN_GAME = is_in_game
    _SCORE = score_callback
    _ON_EXIT = on_exit
    _STOP.clear()
    _STARTED[0] = True
    threading.Thread(target=_run, name="hs-log-overlay", daemon=True).start()


def stop() -> None:
    """Signal the overlay thread to close its window."""
    _STOP.set()


def is_running() -> bool:
    return bool(_STARTED[0])


def _raise_hearthstone() -> None:
    """Return the foreground window to the Hearthstone main window.

    等效于“鼠标真点一下炉石”：先模拟按下/松开 Alt 绕过 Windows 前台锁，
    再 ShowWindow + SetForegroundWindow + BringWindowToTop。失败静默。
    """
    try:
        import ctypes
        import win32gui
        import win32con
    except Exception:
        return
    target = [None]

    def _enum(hwnd, _unused):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return True
        low = title.lower()
        if low and ("hearthstone" in low or "炉石" in low):
            target[0] = hwnd
            return False  # stop at the first matching main window
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return
    hwnd = target[0]
    if not hwnd:
        return
    # 模拟 Alt 键，授予本进程“可切换到前台”的权限（等同用户按了一次键）。
    try:
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)   # VK_MENU 按下
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)   # VK_MENU 抬起
    except Exception:
        pass
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass


def _disable_overlay_activation(root) -> None:
    """给浮窗加 WS_EX_NOACTIVATE/TOOLWINDOW：点它不抢炉石前台。

    普通 tkinter 窗口被点击会获得焦点，把炉石顶出“前台”，导致 OCR 的
    hearthstone_not_foreground 检查失败、自动对战停摆。加上这两个扩展样式后，
    浮窗像 HUD 一样不参与激活，鼠标点击仍可触发按钮。
    """
    try:
        import ctypes
    except Exception:
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) \
            or root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


def _run() -> None:
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"[overlay] tkinter 不可用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False
        return

    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.94)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        W, H = 292, 510
        x = sw - W - 12
        y = 12
        root.geometry(f"{W}x{H}+{x}+{y}")
        root.configure(bg=TITLE_BG)
        _disable_overlay_activation(root)

        def _hover(c):
            # lighten a hex color for hover feedback
            try:
                r = min(255, int(c[1:3], 16) + 22)
                g = min(255, int(c[3:5], 16) + 22)
                b = min(255, int(c[5:7], 16) + 22)
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                return c

        def _make_btn(parent, text_, bg, command):
            btn = tk.Button(
                parent, text=text_, bg=bg, fg="white",
                font=("Microsoft YaHei", 10, "bold"),
                relief="flat", bd=0, pady=6, cursor="hand2",
                activebackground=bg, activeforeground="white",
                command=command)
            btn.bind(
                "<Enter>", lambda _e, b=bg: btn.config(bg=_hover(b)))
            btn.bind("<Leave>", lambda _e, b=bg: btn.config(bg=b))
            return btn

        # ---- header ----------------------------------------------------
        head = tk.Frame(root, bg=TITLE_BG)
        head.pack(fill="x")
        dot = tk.Label(head, text="●", bg=TITLE_BG, fg=GREEN,
                       font=("Segoe UI", 10))
        dot.pack(side="left", padx=(10, 4), pady=8)
        title = tk.Label(head, text="自动化日志", bg=TITLE_BG, fg=TEXT,
                         font=("Microsoft YaHei", 10, "bold"))
        title.pack(side="left", pady=8)
        tk.Frame(root, bg=PANEL, height=1).pack(fill="x")

        # ---- 战绩行 -------------------------------------------------
        score_label = tk.Label(root, text="📊 战绩： —", bg=TITLE_BG, fg=DIM,
                               font=("Microsoft YaHei", 9), anchor="w")
        score_label.pack(fill="x", padx=8, pady=(6, 0))
        tk.Frame(root, bg=PANEL, height=1).pack(fill="x")

        # ---- buttons ---------------------------------------------------
        btn_frame = tk.Frame(root, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=(8, 0))

        def _call_start():
            try:
                # 对局已开始时按钮应处于禁用态；这里再兜底一次，避免误触发。
                if _IS_IN_GAME is not None and _IS_IN_GAME():
                    return
                if _ON_START is not None:
                    _ON_START()
            finally:
                _raise_hearthstone()

        start_btn = _make_btn(btn_frame, "▶  开始对战", ACCENT, _call_start)
        start_btn.pack(fill="x", pady=3)

        def _call_halt():
            try:
                if _ON_HALT is not None:
                    _ON_HALT()
            finally:
                _raise_hearthstone()

        halt_btn = _make_btn(btn_frame, "⏹  中止", DANGER, _call_halt)
        halt_btn.pack(fill="x", pady=3)

        def _set_stop_after_state():
            active = bool(_IS_STOP_AFTER() if _IS_STOP_AFTER is not None else False)
            if active:
                stop_after_btn.config(
                    text="✓  本局结束后停止（点击取消）",
                    bg=OK, activebackground=OK)
            else:
                stop_after_btn.config(
                    text="⏸  本局结束后停止", bg=WARN, activebackground=WARN)

        def _call_stop_after():
            try:
                if _ON_STOP_AFTER is not None:
                    _ON_STOP_AFTER()
            finally:
                _raise_hearthstone()
                _set_stop_after_state()

        stop_after_btn = _make_btn(btn_frame, "⏸  本局结束后停止", WARN,
                                   _call_stop_after)
        stop_after_btn.pack(fill="x", pady=3)

        def _call_save():
            try:
                path = _save_log()
                push(f"[SYS] 对战日志已保存：{path}")
                save_btn.config(text="✓  已保存", bg=OK)
                root.after(2000, lambda: save_btn.config(
                    text="💾  保存日志", bg=OK))
            except Exception as exc:
                push(f"[SYS] 保存对战日志失败：{exc}")
                save_btn.config(text="✗  保存失败", bg=DANGER)
                root.after(2000, lambda: save_btn.config(
                    text="💾  保存日志", bg=OK))
            finally:
                _raise_hearthstone()

        save_btn = _make_btn(btn_frame, "💾  保存日志", OK, _call_save)
        save_btn.pack(fill="x", pady=3)

        def _call_exit():
            try:
                if _ON_EXIT is not None:
                    _ON_EXIT()
                    return
            finally:
                _raise_hearthstone()
            # 没有绑定退出回调时，关闭浮窗本身（兜底）。
            stop()

        exit_btn = _make_btn(btn_frame, "🚪  退出脚本", DANGER, _call_exit)
        exit_btn.pack(fill="x", pady=3)

        # ---- delay progress (bottom; 先占底部，日志区填剩余空间) ------
        delay_frame = tk.Frame(root, bg=BG)
        delay_frame.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        delay_canvas = tk.Canvas(delay_frame, height=8, bg=PANEL,
                                 highlightthickness=0)
        delay_canvas.pack(fill="x", pady=(0, 2))
        delay_label = tk.Label(delay_frame, text="延时：无", bg=BG, fg=DIM,
                               font=("Microsoft YaHei", 8), anchor="w")
        delay_label.pack(fill="x")

        # ---- log body --------------------------------------------------
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        text = tk.Text(body, bg=BG, fg=TEXT, font=("Microsoft YaHei", 9),
                       bd=0, highlightthickness=0, wrap="word",
                       height=12, padx=2, pady=2, spacing1=2, spacing3=2)
        text.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, orient="vertical", command=text.yview,
                              width=10)
        scroll.pack(side="right", fill="y")
        text.config(yscrollcommand=scroll.set)
        text.tag_config("turn", foreground=GREEN)
        text.tag_config("act", foreground=TEXT)
        text.tag_config("dim", foreground=DIM)

        # ---- drag ------------------------------------------------------
        _drag = {"x": 0, "y": 0}

        def _start_drag(event):
            _drag["x"], _drag["y"] = event.x, event.y

        def _on_drag(event):
            nx = root.winfo_x() + event.x - _drag["x"]
            ny = root.winfo_y() + event.y - _drag["y"]
            root.geometry(f"+{nx}+{ny}")

        root.bind("<Button-1>", _start_drag)
        root.bind("<B1-Motion>", _on_drag)

        rendered = [0]

        def _refresh():
            global _DELAY
            if _STOP.is_set():
                root.destroy()
                _STARTED[0] = False
                return
            with _LOCK:
                lines = list(_LINES)
            pos = text.yview()
            at_bottom = pos[1] >= 0.999
            if len(lines) < rendered[0]:
                # 缓冲区溢出丢弃了旧行：全量重建并强制跟随底部。
                rendered[0] = 0
                text.delete("1.0", "end")
                at_bottom = True
            for ln, turn in lines[rendered[0]:]:
                tag = "turn" if turn else (
                    "act" if ln.startswith(("[推荐]", "[执行]")) else "dim")
                text.insert("end", ln + "\n", tag)
            rendered[0] = len(lines)
            if _IS_RUNNING is not None:
                if _IS_RUNNING():
                    halt_btn.config(text="⏹  中止", bg=DANGER,
                                    activebackground=DANGER)
                else:
                    halt_btn.config(text="▶  恢复", bg=OK, activebackground=OK)
            if _IS_IN_GAME is not None and _IS_IN_GAME():
                start_btn.config(state="disabled", text="⏳  对局进行中",
                                 bg=DISABLED, activebackground=DISABLED,
                                 disabledforeground=DIM,
                                 cursor="arrow")
            else:
                start_btn.config(state="normal", text="▶  开始对战",
                                 bg=ACCENT, activebackground=ACCENT,
                                 disabledforeground=DIM,
                                 cursor="hand2")
            if _SCORE is not None:
                score = _SCORE()
                if score is not None:
                    # score 三元组：(场数, 胜场, 自动认输数)。兼容旧的二元组。
                    if len(score) >= 3:
                        games, wins, concedes = score
                    else:
                        games, wins = score
                        concedes = 0
                    # 负 = 总完成局 - 胜（含自动认输，因为自动认输也算一局输）；
                    # 认输数单独列出作参考。
                    losses = max(games - wins, 0)
                    rate = (wins / games * 100) if games else 0.0
                    rate_txt = f"{rate:.1f}%" if games else "--"
                    concede_txt = f" · 认输 {concedes}" if concedes else ""
                    score_label.config(
                        text=f"📊 战绩： 胜 {wins} · 负 {losses} · "
                             f"胜率 {rate_txt}{concede_txt}", fg=TEXT)
                else:
                    score_label.config(text="📊 战绩： —", fg=DIM)
            with _LOCK:
                delay = dict(_DELAY) if _DELAY is not None else None
            if delay is not None:
                now = time.time()
                elapsed = now - delay["started"]
                total = max(delay["total"], 0.001)
                frac = min(max(elapsed / total, 0.0), 1.0)
                remaining = max(total - elapsed, 0.0)
                if remaining <= 0:
                    # 延时已结束：主动清空，避免 “0/0.5s” 这类短延时残留。
                    with _LOCK:
                        _DELAY = None
                    delay_label.config(text="延时：无", fg=DIM)
                    delay_canvas.delete("all")
                else:
                    delay_label.config(
                        text=f"⏳ {delay['desc']}（{remaining:.0f}/{total:.0f}s）",
                        fg=TEXT)
                    w = max(delay_canvas.winfo_width(), 1)
                    delay_canvas.delete("all")
                    delay_canvas.create_rectangle(
                        0, 0, w * frac, 8, fill=ACCENT, outline="")
            else:
                delay_label.config(text="延时：无", fg=DIM)
                delay_canvas.delete("all")
            _set_stop_after_state()
            if at_bottom and rendered[0]:
                text.see("end")
            root.after(_REFRESH_MS, _update)

        def _update():
            try:
                _refresh()
            except Exception as exc:
                # 单次刷新异常不杀死浮窗：打印并继续下一轮调度。
                print(f"[overlay] 刷新异常(继续): {type(exc).__name__}: {exc}")
                try:
                    root.after(_REFRESH_MS, _update)
                except Exception:
                    pass

        _update()
        root.mainloop()
    except Exception as exc:
        print(f"[overlay] 日志浮窗禁用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False
