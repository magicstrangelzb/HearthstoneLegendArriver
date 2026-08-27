"""用户/站点/机器级配置的单一来源（分层合并）。

三个来源，优先级从低到高：

    1. 内置默认值（本文件）
    2. ui_config.json —— Web 控制台 / 校准工具持久化的用户配置
    3. 环境变量 —— 机器 / 高级覆盖（如 HS_LOG_ROOT、HS_USER_NAME、HS_PORT）

约定：
    * 本模块只做"解析站点/用户/机器配置"，不含业务逻辑。
    * 其他模块直接 `from config import ...` 或 `import config` 取值，
      不要再在代码里写死机器路径/用户身份。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "ui_config.json"

_env = os.environ.get


def _load_ui_config() -> dict:
    """读取 ui_config.json；缺失/非法时返回空 dict（走默认值）。"""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_UI = _load_ui_config()


def _first(*values, default=""):
    """返回第一个非空值，用于默认值回退。"""
    for value in values:
        if value not in (None, "", 0):
            return value
    return default


# ---------------------------------------------------------------- 用户身份
# 环境变量 HS_USER_NAME > ui_config.json 的 name > 占位默认。
USER_NAME = _first(_env("HS_USER_NAME"), _UI.get("name"), "YOURNAME#1234")

# ---------------------------------------------------------------- 炉石日志根目录
_LOCALAPPDATA_BLIZZARD = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Blizzard", "Hearthstone", "Logs")


def _resolve_log_root() -> str:
    """环境变量 HS_LOG_ROOT > ui_config.json 的 log_root > LOCALAPPDATA 自动探测。"""
    candidates = (
        _env("HS_LOG_ROOT"),
        _UI.get("log_root"),
        _LOCALAPPDATA_BLIZZARD if os.path.isdir(_LOCALAPPDATA_BLIZZARD) else "",
    )
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    # 都不存在：返回配置值/空串，交由调用方处理（web 会提示用户填写）。
    return _first(_env("HS_LOG_ROOT"), _UI.get("log_root"))


HEARTHSTONE_LOG_ROOT = _resolve_log_root()

# ---------------------------------------------------------------- Web 控制台
HOST = _env("HS_HOST", "127.0.0.1")
BASE_PORT = int(_env("HS_PORT", "8765"))
LOG_BUFFER_SIZE = int(_env("HS_LOG_BUFFER_SIZE", "500"))

# ---------------------------------------------------------------- 操作节奏（环境变量可覆盖）
OPERATE_INTERVAL = float(_env("HS_OPERATE_INTERVAL", "0.15"))
STATE_CHECK_INTERVAL = float(_env("HS_STATE_CHECK_INTERVAL", "1.0"))
TINY_OPERATE_INTERVAL = float(_env("HS_TINY_OPERATE_INTERVAL", "0.08"))

# ---------------------------------------------------------------- OCR 模型目录
# 环境变量 HS_OCR_MODEL_ROOT > %LOCALAPPDATA%/AutoHS/ocr_models/paddleocr。
OCR_MODEL_ROOT = os.environ.get("HS_OCR_MODEL_ROOT") or os.path.normpath(
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                 "AutoHS", "ocr_models", "paddleocr"))
