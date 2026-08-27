# -*- coding: utf-8 -*-
"""Bottom-right live log overlay via tkinter (topmost, translucent, draggable).

start() launches a background thread with a tkinter window pinned to the
bottom-right corner. It streams the latest automation log lines; lines that
mark the own-turn start are highlighted green. The window can be dragged by
holding the left mouse button. Failures disable the overlay and never crash
the caller.
"""
from __future__ import annotations

import threading
import time
from collections import deque

_LOCK = threading.Lock()
_LINES: deque = deque(maxlen=64)
_STARTED = [False]
_REFRESH_MS = 350

GREEN = "#60e260"
ACT = "#f0f0f0"
DIM = "#a8acb4"
BG = "#181c22"


def _turn_start(line: str) -> bool:
    return ("回合" in line and "延时" in line) or ("轮到己方" in line)


def push(line: str, _level: str = "INFO") -> None:
    if not _STARTED[0]:
        return
    line = str(line).rstrip()
    if not line.strip():
        return
    with _LOCK:
        _LINES.append((line, _turn_start(line)))


_STOP = threading.Event()


def start() -> None:
    if _STARTED[0]:
        return
    _STOP.clear()
    _STARTED[0] = True
    threading.Thread(target=_run, name="hs-log-overlay", daemon=True).start()


def stop() -> None:
    """Signal the overlay thread to close its window."""
    _STOP.set()


def is_running() -> bool:
    return bool(_STARTED[0])


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
        root.attributes("-alpha", 0.92)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        W, H = 276, 342
        x = sw - W - 12
        y = 12
        root.geometry(f"{W}x{H}+{x}+{y}")
        title = tk.Label(root, text="自动化日志", bg=BG, fg="#e0e0e0",
                         font=("Microsoft YaHei", 10, "bold"))
        title.pack(fill="x")
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, bg=BG, fg="white", font=("Microsoft YaHei", 10),
                       bd=0, highlightthickness=0, wrap="none")
        text.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.config(yscrollcommand=scroll.set)
        text.tag_config("turn", foreground=GREEN)
        text.tag_config("act", foreground=ACT)
        text.tag_config("dim", foreground=DIM)

        # 鼠标按住可拖动窗口
        _drag = {"x": 0, "y": 0}

        def _start_drag(event):
            _drag["x"], _drag["y"] = event.x, event.y

        def _on_drag(event):
            nx = root.winfo_x() + event.x - _drag["x"]
            ny = root.winfo_y() + event.y - _drag["y"]
            root.geometry(f"+{nx}+{ny}")

        root.bind("<Button-1>", _start_drag)
        root.bind("<B1-Motion>", _on_drag)

        def _update():
            if _STOP.is_set():
                root.destroy()
                _STARTED[0] = False
                return
            with _LOCK:
                lines = list(_LINES)[-40:]
            pos = text.yview()
            text.delete("1.0", "end")
            for ln, turn in lines:
                tag = "turn" if turn else (
                    "act" if ln.startswith(("[推荐]", "[执行]")) else "dim")
                text.insert("end", ln + "\n", tag)
            text.yview_moveto(pos[0])
            root.after(_REFRESH_MS, _update)

        _update()
        root.mainloop()
    except Exception as exc:
        print(f"[overlay] 日志浮窗禁用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False
