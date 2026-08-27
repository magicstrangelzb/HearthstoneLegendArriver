# -*- coding: utf-8 -*-
"""推荐区域校准工具：桌面绿框对齐 + 实时 OCR 预览。

启动方式（三选一）：
  * Web 控制台（打包版）：点「校准推荐区域」按钮
  * 源码运行：python calibrate_roi.py
  * 自检：python calibrate_roi.py --selftest —— 窗口打开约 1 秒自动退出
    （不加载 OCR 引擎，验证窗口/图层链路可用）

对战时启动（盒子推荐面板只在对局内出现）：
  1. 屏幕上的绿框 = 程序实际截图区域 recommendation_roi（默认 7,200,202,500）；
  2. 拖动绿框边框可整体移动，拖右下角手柄可调整大小，
     把盒子「打法参考A」面板框进绿框；
  3. 右侧预览窗按 OCR 同款 1.5x 放大显示，并逐帧跑实际 OCR，
     看到「✓ 识别到『打法参考A』」即对齐成功（绿框保持绿色）；
  4. 点 [保存]（或按 S）把区域写入 ui_config.json，Esc 退出。

原理：盒子推荐面板顶部才是「打法参考A」红头标题；面板没被框进
推荐区域时 OCR 读不到信标 → 程序判定面板不存在 → 回合内
recommendation_not_stable 重试——这正是「回合开始无法打牌」
最常见的原因。校准 = 让截图区域与面板重合。

注意：
  * 校准完成后请关闭本工具再启动自动化（预览窗会遮挡游戏画面）；
  * 无需管理员权限——本工具只画框+截图，不模拟鼠标/键盘。
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageGrab

# ---------------------------------------------------------------- DPI
# 与程序截图坐标一致：物理像素坐标（进程声明 DPI aware，1.0x 缩放直接吻合）。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------- Win32
USER32 = ctypes.windll.user32
GDI32 = ctypes.windll.gdi32
KERNEL32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
PM_REMOVE = 0x0001
CLASS_NAME = "HSLegendArriverCalibrate"

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204

VK_ESCAPE = 0x1B
VK_S = 0x53

EDGE = 6          # 边框厚度（画在截图区域外侧，捕捉画面保持干净）
HANDLE = 22       # 右下角缩放手柄边长
MIN_SIZE = 60     # 区域最小边长


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM)


def _passive_wndproc(hwnd, msg, wparam, lparam):
    """窗口类回调：消息全部在主循环手动处理，这里只走默认流程。"""
    return USER32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_IMPL = WNDPROC(_passive_wndproc)  # 全局引用防止被回收

USER32.RegisterClassW.argtypes = [ctypes.c_void_p]
USER32.RegisterClassW.restype = ctypes.c_ushort
USER32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE,
                                   wintypes.LPVOID]
USER32.CreateWindowExW.restype = wintypes.HWND
USER32.SetCapture.argtypes = [wintypes.HWND]
USER32.ReleaseCapture.argtypes = []
USER32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
USER32.PeekMessageW.restype = wintypes.BOOL
USER32.TranslateMessage.argtypes = [ctypes.c_void_p]
USER32.DispatchMessageW.argtypes = [ctypes.c_void_p]
USER32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.GetSystemMetrics.argtypes = [ctypes.c_int]
USER32.GetSystemMetrics.restype = ctypes.c_int
USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
USER32.GetAsyncKeyState.restype = ctypes.c_short
USER32.GetDC.argtypes = [wintypes.HWND]
USER32.GetDC.restype = wintypes.HDC
USER32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
USER32.DestroyWindow.argtypes = [wintypes.HWND]
GDI32.CreateCompatibleDC.argtypes = [wintypes.HDC]
GDI32.CreateCompatibleDC.restype = wintypes.HDC
GDI32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p,
                                   wintypes.UINT, ctypes.c_void_p,
                                   wintypes.HANDLE, wintypes.DWORD]
GDI32.CreateDIBSection.restype = wintypes.HBITMAP
GDI32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
GDI32.DeleteDC.argtypes = [wintypes.HDC]
GDI32.DeleteObject.argtypes = [wintypes.HANDLE]
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.GetModuleHandleW.restype = wintypes.HINSTANCE
USER32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
                                       ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.HDC, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD]
USER32.UpdateLayeredWindow.restype = wintypes.BOOL

# ---------------------------------------------------------------- 配色（BGR）
GREEN = (80, 200, 80)          # 对齐成功：绿框
SAVE_COLOR = (240, 140, 50)    # 保存成功：亮蓝反馈框
ORANGE = (0, 140, 255)         # 尚未确认信标
RED = (56, 56, 235)            # 错误
PANEL_BG = (36, 40, 48)
PANEL_BORDER = (118, 124, 132)
BTN_BLUE = (216, 150, 62)
TEXT_MAIN = (240, 240, 240)
TEXT_DIM = (168, 172, 180)
TEXT_RED = (80, 92, 240)
TEXT_GREEN = (96, 226, 96)


def _font(size: int):
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                 r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _user_config_path() -> Path:
    """与 RecommendationConfig 读取位置一致：应用目录下的 ui_config.json。"""
    return Path(__file__).resolve().parent / "ui_config.json"


def _current_roi() -> list[int]:
    """优先读用户配置（校准结果），否则用 config 默认值。"""
    try:
        from src.recommendation_config import RecommendationConfig
        default = list(RecommendationConfig().recommendation_roi)
    except Exception:
        default = [7, 200, 202, 500]
    try:
        data = json.loads(_user_config_path().read_text(encoding="utf-8"))
        roi = data.get("recommendation_roi")
        vals = [int(v) for v in roi]
        if len(vals) == 4 and 0 <= vals[0] < vals[2] and 0 <= vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return default


def _grab_crop(l: int, t: int, r: int, b: int) -> np.ndarray | None:
    sw = USER32.GetSystemMetrics(0)
    sh = USER32.GetSystemMetrics(1)
    if l < 0 or t < 0 or r > sw or b > sh or r - l < 4 or b - t < 4:
        return None
    try:
        rgb = np.asarray(ImageGrab.grab(bbox=(l, t, r, b), all_screens=False))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


class Calibrator:
    """绿框 + 预览窗 + 逐帧 OCR 的完整校准界面。"""

    def __init__(self, selftest: bool, ocr_enabled: bool):
        self.selftest = selftest
        self.ocr_enabled = ocr_enabled and not selftest
        self.running = True
        self.hwnd = None
        self.roi = _current_roi()
        self.drag = None            # None | ("move", dx, dy) | ("resize",)
        self.save_pending = False
        self.capture_pending = False
        self.saved_flash_until = 0.0
        self._flash_active = False
        self._dirty = True
        self.last_crop = None
        self.crop_event = threading.Event()
        self.ocr_result = {"status": "loading", "lines": [], "beacon": None,
                           "conf": 0.0, "msg": "正在加载 OCR 引擎……"}
        self._esheld = False
        self._shed = False
        self._msg = MSG()

    # ------------------------------------------------------------ 布局/命中
    def preview_rect(self, sw: int, sh: int):
        w = self.roi[2] - self.roi[0]
        h = self.roi[3] - self.roi[1]
        img_h = min(int(round(h * 1.5)), 470)
        img_w = max(int(round(w * 1.5)), 150)
        panel_w = img_w + 16
        panel_h = img_h + 282                    # 标题+文本+按钮区
        panel_h = min(panel_h, sh - 20)
        px = max(8, sw - panel_w - 12)
        py = 16
        save_btn = (px + panel_w - 130, py + panel_h - 44,
                    px + panel_w - 12, py + panel_h - 12)
        capture_btn = (px + 12, py + panel_h - 44,
                       px + panel_w - 142, py + panel_h - 12)
        return (px, py, px + panel_w, py + panel_h), save_btn, capture_btn

    def _hit_test(self, sx: int, sy: int, sw: int, sh: int):
        l, t, r, b = self.roi
        _, save_btn, capture_btn = self.preview_rect(sw, sh)
        if (capture_btn[0] <= sx <= capture_btn[2]
                and capture_btn[1] <= sy <= capture_btn[3]):
            return "btn_capture"
        if save_btn[0] <= sx <= save_btn[2] and save_btn[1] <= sy <= save_btn[3]:
            return "btn_save"
        if r + 2 <= sx <= r + 2 + HANDLE and b + 2 <= sy <= b + 2 + HANDLE:
            return "corner"
        if l - EDGE <= sx <= r + EDGE and t - EDGE <= sy <= b + EDGE:
            return "edge"
        return None

    # ------------------------------------------------------------ 事件
    def _on_mouse_down(self, sx: int, sy: int, sw: int, sh: int):
        kind = self._hit_test(sx, sy, sw, sh)
        if kind == "btn_capture":
            self.capture_pending = True
        elif kind == "btn_save":
            self.save_pending = True
        elif kind == "corner":
            self.drag = ("resize",)
        elif kind == "edge":
            self.drag = ("move", sx - self.roi[0], sy - self.roi[1])
        else:
            return
        USER32.SetCapture(self.hwnd)

    def _on_mouse_up(self, sx: int, sy: int, sw: int, sh: int):
        if self.capture_pending:
            self.capture_pending = False
            if self._hit_test(sx, sy, sw, sh) == "btn_capture":
                self._do_capture()
        if self.save_pending:
            self.save_pending = False
            if self._hit_test(sx, sy, sw, sh) == "btn_save":
                self.save_roi(flash=True)
        self.drag = None
        USER32.ReleaseCapture()

    def _on_mouse_move(self, sx: int, sy: int, sw: int, sh: int):
        if self.drag is None:
            return
        l, t, r, b = self.roi
        if self.drag[0] == "move":
            _, dx, dy = self.drag
            l = min(max(0, sx - dx), sw - MIN_SIZE)
            t = min(max(0, sy - dy), sh - MIN_SIZE)
            r = min(sw, l + (r - l))
            b = min(sh, t + (b - t))
        else:  # resize
            r = min(sw, max(l + MIN_SIZE, sx))
            b = min(sh, max(t + MIN_SIZE, sy))
        self.roi = [l, t, r, b]
        self._dirty = True

    # ------------------------------------------------------------ 保存
    def save_roi(self, flash: bool = False):
        path = _user_config_path()
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:
            cfg = {}
        cfg["recommendation_roi"] = [int(v) for v in self.roi]
        try:
            path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[校准] 保存失败：{exc}")
            return
        if flash:
            self.saved_flash_until = time.time() + 1.5
            self._flash_active = True
        self._dirty = True
        print(f"[校准] 已保存推荐区域 -> {path}")
        print(f"[校准] recommendation_roi={tuple(self.roi)}（重开对局生效，"
              "不覆盖名字/日志目录等配置）")

    # ------------------------------------------------------------ OCR 线程
    def _ocr_worker(self):
        try:
            from src.ocr.paddle_adapter import PaddleOcrAdapter
            from src.ocr.preprocess import iter_preprocess_recommendation
            adapter = PaddleOcrAdapter()
            while self.running:
                self.crop_event.wait(timeout=5.0)
                self.crop_event.clear()
                if not self.running:
                    break
                # 节流：拖动时截图事件密，限制 OCR 最小间隔，避免满速空转拖卡主循环
                time.sleep(0.25)
                crop = self.last_crop
                if crop is None:
                    continue
                started = time.time()
                ev = None
                for tag, candidate in iter_preprocess_recommendation(crop):
                    if tag != "scaled_color_v1":
                        continue
                    ev = adapter.recognize(
                        candidate, f"calib-{id(crop)}", "calib_scaled")
                    break
                if ev is None:
                    continue
                lines = [ln.text for ln in ev.lines if ln.text]
                beacon = any("打法参考A" in ln or "打法参考Ａ" in ln
                             for ln in lines)
                self.ocr_result = {
                    "status": "ok", "lines": lines[:6], "beacon": beacon,
                    "conf": ev.confidence,
                    "msg": f"OCR {len(lines)} 行 置信 {ev.confidence:.2f}"
                           f" 耗时 {time.time() - started:.0f}s",
                }
        except Exception as exc:
            self.ocr_result = {"status": "error", "lines": [], "beacon": False,
                               "conf": 0.0,
                               "msg": f"OCR 未运行：{type(exc).__name__}: {exc}"}

    def _start_ocr_thread(self):
        if not self.ocr_enabled:
            self.ocr_result = {"status": "off", "lines": [], "beacon": False,
                               "conf": 0.0, "msg": "预览模式（--no-ocr）"}
            return
        threading.Thread(target=self._ocr_worker, name="calib-ocr",
                         daemon=True).start()

    def _do_capture(self):
        """手动触发一次：抓取当前推荐区域并刷新 OCR 预览。"""
        l, t, r, b = self.roi
        crop = _grab_crop(l, t, r, b)
        if crop is not None:
            self.last_crop = crop
            self.crop_event.set()

    # ------------------------------------------------------------ 渲染
    def _paint(self, sw: int, sh: int) -> np.ndarray:
        layer = np.zeros((sh, sw, 4), dtype=np.uint8)  # 内存序即 DIB BGRA
        l, t, r, b = self.roi
        color = SAVE_COLOR if time.time() < self.saved_flash_until else GREEN
        alpha = layer[:, :, 3]
        band = np.zeros((sh, sw), dtype=bool)
        # 四周边框，画在截图区域【外侧】，捕捉画面保持干净
        band[max(0, t - EDGE):min(sh, b + EDGE),
             max(0, l - EDGE):max(0, l)] = True            # 左
        band[max(0, t - EDGE):min(sh, b + EDGE),
             min(sw, r):min(sw, r + EDGE)] = True          # 右
        band[max(0, t - EDGE):max(0, t),
             max(0, l - EDGE):min(sw, r + EDGE)] = True    # 上
        band[min(sh, b):min(sh, b + EDGE),
             max(0, l - EDGE):min(sw, r + EDGE)] = True    # 下
        layer[band, 0:3] = color
        alpha[band] = 255
        # 右下角缩放手柄
        hx, hy = min(sw - 1, r + 2), min(sh - 1, b + 2)
        hx1, hy1 = min(sw, hx + HANDLE), min(sh, hy + HANDLE)
        layer[hy:hy1, hx:hx1, 0:3] = color
        alpha[hy:hy1, hx:hx1] = 255
        for i in range(6, HANDLE - 4, 7):   # 对角抓握点
            y, x = hy + i, hx + i
            if x < hx1 and y < hy1:
                layer[y, x, 0:3] = TEXT_MAIN
        return layer

    def _draw_preview(self, layer: np.ndarray, sw: int, sh: int):
        (px, py, px1, py1), save_btn, capture_btn = self.preview_rect(sw, sh)
        panel_w = px1 - px
        panel_h = py1 - py
        panel = np.full((panel_h, panel_w, 3), PANEL_BG, dtype=np.uint8)
        self._draw_text(panel, "推荐区域预览（1.5x 缩放）",
                        font=_font(13), xy=(8, 6), color=TEXT_MAIN)
        img_h = min(int(round((self.roi[3] - self.roi[1]) * 1.5)), 470)
        img_h = max(1, min(img_h, panel_h - 60))   # 小屏兜底
        img_w = max(int(round((self.roi[2] - self.roi[0]) * 1.5)), 1)
        crop = self.last_crop
        if crop is not None and crop.size:
            shown = cv2.resize(crop, (img_w, img_h), interpolation=cv2.INTER_CUBIC)
            panel[30:30 + img_h, 8:8 + img_w] = shown
        else:
            self._draw_text(panel, "等待画面……", font=_font(13),
                            xy=(10, 44), color=TEXT_DIM)
        # OCR 结果文本区
        res = self.ocr_result
        lines = res.get("lines") or []
        ys = 30 + img_h + 10
        status = res.get("status")
        if status == "ok":
            for i, ln in enumerate(lines[:6]):
                color = TEXT_GREEN if ("打法参考A" in ln
                                       or "打法参考Ａ" in ln) else TEXT_MAIN
                self._draw_text(panel, ln[:40], font=_font(12),
                                xy=(10, ys + i * 17), color=color)
            self._draw_text(panel, res.get("msg", ""), font=_font(11),
                            xy=(10, ys + 6 * 17 + 2), color=TEXT_DIM)
            if time.time() < self.saved_flash_until:
                self._draw_text(panel, "✓ 已保存，重开对局后生效",
                                font=_font(14), xy=(10, ys + 7 * 17 + 6),
                                color=TEXT_GREEN)
            elif res.get("beacon"):
                self._draw_text(panel, "✓ 识别到『打法参考A』→ 对齐成功",
                                font=_font(14), xy=(10, ys + 7 * 17 + 6),
                                color=TEXT_GREEN)
            else:
                self._draw_text(panel, "把盒子面板移入绿框，等待刷新后再看",
                                font=_font(12), xy=(10, ys + 7 * 17 + 8),
                                color=TEXT_RED)
        elif status == "loading":
            self._draw_text(panel, res.get("msg", ""), font=_font(13),
                            xy=(10, ys), color=TEXT_DIM)
        elif status == "error":
            self._draw_text(panel, res.get("msg", "OCR 不可用"), font=_font(12),
                            xy=(10, ys), color=TEXT_RED)
        else:  # off
            self._draw_text(panel, res.get("msg", ""), font=_font(13),
                            xy=(10, ys), color=TEXT_DIM)
        self._draw_text(panel, "拖框·缩放手柄·[保存]·Esc退出",
                        font=_font(12), xy=(10, max(4, min(panel_h - 78,
                                                          ys + 8 * 17 + 8))),
                        color=TEXT_DIM)
        # 截图按钮
        cx0, cy0, cx1, cy1 = capture_btn
        c_btn_w, c_btn_h = cx1 - cx0, cy1 - cy0
        c_btn_img = np.full((c_btn_h, c_btn_w, 3), BTN_BLUE, dtype=np.uint8)
        self._draw_text(c_btn_img, "截图", font=_font(15),
                        xy=(c_btn_w // 2 - 16, c_btn_h // 2 - 10),
                        color=TEXT_MAIN)
        panel[cy0 - py:cy1 - py, cx0 - px:cx1 - px] = c_btn_img
        # 保存按钮
        bx0, by0, bx1, by1 = save_btn
        btn_w, btn_h = bx1 - bx0, by1 - by0
        btn_img = np.full((btn_h, btn_w, 3), BTN_BLUE, dtype=np.uint8)
        self._draw_text(btn_img, "保存 (S)", font=_font(15),
                        xy=(btn_w // 2 - 30, btn_h // 2 - 10), color=TEXT_MAIN)
        panel[by0 - py:by1 - py, bx0 - px:bx1 - px] = btn_img
        # 组合
        layer[py:py1, px:px1, 0:3] = panel
        layer[py:py1, px:px1, 3] = 246
        layer[py, px:px1, 0:3] = PANEL_BORDER
        layer[py1 - 1, px:px1, 0:3] = PANEL_BORDER
        layer[py:py1, px, 0:3] = PANEL_BORDER
        layer[py:py1, px1 - 1, 0:3] = PANEL_BORDER

    @staticmethod
    def _draw_text(img: np.ndarray, text: str, font, xy: tuple[int, int],
                   color: tuple[int, int, int]):
        if not text or font is None:
            return
        h, w = img.shape[:2]
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        ImageDraw.Draw(pil).text(xy, text, fill=(
            color[2], color[1], color[0]), font=font)
        out = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
        img[:h, :w] = out[:h, :w]

    # ------------------------------------------------------------ 窗口
    def _create_window(self, sw: int, sh: int) -> wintypes.HWND:
        inst = KERNEL32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(_WNDPROC_IMPL, ctypes.c_void_p).value
        wc.hInstance = inst
        wc.lpszClassName = CLASS_NAME
        USER32.RegisterClassW(ctypes.byref(wc))
        return USER32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            CLASS_NAME, "HSLegendArriver 校准", WS_POPUP | WS_VISIBLE,
            0, 0, sw, sh, None, None, inst, None)

    def _handle_message(self, sw: int, sh: int):
        msg = self._msg
        if msg.message == WM_LBUTTONDOWN:
            sx = ctypes.c_short(msg.lParam & 0xFFFF).value
            sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            self._on_mouse_down(sx, sy, sw, sh)
        elif msg.message == WM_LBUTTONUP:
            sx = ctypes.c_short(msg.lParam & 0xFFFF).value
            sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            self._on_mouse_up(sx, sy, sw, sh)
        elif msg.message == WM_MOUSEMOVE:
            sx = ctypes.c_short(msg.lParam & 0xFFFF).value
            sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            self._on_mouse_move(sx, sy, sw, sh)
        elif msg.message == WM_RBUTTONDOWN:
            self.running = False
        else:
            USER32.TranslateMessage(ctypes.byref(msg))
            USER32.DispatchMessageW(ctypes.byref(msg))

    # ------------------------------------------------------------ 主循环
    def run(self) -> int:
        sw = USER32.GetSystemMetrics(0)
        sh = USER32.GetSystemMetrics(1)
        self.hwnd = self._create_window(sw, sh)
        if not self.hwnd:
            print("[校准] 创建窗口失败")
            return 1
        print(f"[校准] 屏幕 {sw}x{sh}，当前推荐区域 recommendation_roi="
              f"{tuple(self.roi)}")
        print("[校准] 拖动绿框->对齐盒子面板->按 S 保存，Esc 退出。"
              "（窗口置顶，空白处鼠标可穿透操作游戏，无预览窗）")
        hdc = USER32.GetDC(None)
        mem_dc = GDI32.CreateCompatibleDC(hdc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = sw
        bmi.bmiHeader.biHeight = -sh            # 顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        dib = GDI32.CreateDIBSection(hdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        GDI32.SelectObject(mem_dc, dib)
        USER32.ReleaseDC(None, hdc)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        pt_dst = wintypes.POINT(0, 0)
        size = wintypes.SIZE(sw, sh)
        pt_src = wintypes.POINT(0, 0)
        deadline = time.time() + 1.3 if self.selftest else None
        self._start_ocr_thread()
        try:
            while self.running:
                # 排空消息队列（拖动时消息密，必须全部处理）
                while USER32.PeekMessageW(ctypes.byref(self._msg), None,
                                          0, 0, PM_REMOVE):
                    self._handle_message(sw, sh)
                    if not self.running:
                        break
                if not self.running:
                    break
                # 全局按键：Esc 退出，S 保存
                esc = bool(USER32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
                if esc and not self._esheld:
                    self.running = False
                self._esheld = esc
                s_key = bool(USER32.GetAsyncKeyState(VK_S) & 0x8000)
                if s_key and not self._shed:
                    self.save_roi(flash=True)
                self._shed = s_key
                if (self._flash_active
                        and time.time() >= self.saved_flash_until):
                    self._flash_active = False
                    self._dirty = True
                if self.running and self._dirty:
                    layer = self._paint(sw, sh)
                    ctypes.memmove(bits.value, layer.tobytes(), layer.nbytes)
                    USER32.UpdateLayeredWindow(
                        self.hwnd, None, ctypes.byref(pt_dst),
                        ctypes.byref(size), mem_dc, ctypes.byref(pt_src), 0,
                        ctypes.byref(blend), ULW_ALPHA)
                    self._dirty = False
                if deadline is not None and time.time() > deadline:
                    break
                time.sleep(0.1)
        finally:
            if self.hwnd:
                USER32.ReleaseCapture()
                USER32.DestroyWindow(self.hwnd)
                self.hwnd = None
            GDI32.DeleteDC(mem_dc)
            GDI32.DeleteObject(dib)
            self.running = False
        print("[校准] 已退出。")
        return 0


def main() -> int:
    selftest = "--selftest" in sys.argv
    ocr_enabled = "--no-ocr" not in sys.argv
    cal = Calibrator(selftest=selftest, ocr_enabled=ocr_enabled)
    try:
        code = cal.run()
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    if selftest:
        print("[校准] selftest OK")
    return code


if __name__ == "__main__":
    sys.exit(main())
