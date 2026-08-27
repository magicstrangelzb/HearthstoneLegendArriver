"""用户/站点/机器/自动化 配置的单一来源（分层合并）。

所有设置集中到这一个文件，来源优先级从低到高：

    1. 内置默认值（本文件）
    2. ui_config.json —— Web 控制台 / 校准工具持久化的用户配置
    3. 环境变量 —— 机器 / 高级覆盖（如 HS_LOG_ROOT、HS_USER_NAME、HS_PORT）

约定：
    * 本模块只做"解析站点/用户/机器配置"，不含业务逻辑。
    * 其他模块直接 `from config import ...` 或 `import config` 取值，
      不要再在代码里写死机器路径/用户身份/延时数值。
    * web 层与推荐自动化层共用本文件，不再各自读 ui_config.json。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 仓库根目录 = 本文件所在目录（ui_config.json / cards.json / web/ 都在这里）。
ROOT = Path(__file__).resolve().parent
# 用户配置持久化文件路径（Web 控制台 / 校准工具读写，与代码默认值分层合并）。
CONFIG_PATH = ROOT / "ui_config.json"

_env = os.environ.get


def _load_ui_config() -> dict:
    """读取 ui_config.json；缺失/非法时返回空 dict（走默认值）。

    只在模块加载时读一次。注意：RecommendationConfig.__post_init__ 里的
    _user_roi()/_user_delays() 是每次实例化时重新读文件，以便校准工具或
    Web 保存后无需重启即可生效。
    """
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# 模块加载时缓存的 ui_config.json 内容（用于 USER_NAME / 日志目录等一次性设置）。
_UI = _load_ui_config()


def _first(*values, default=""):
    """返回第一个非空值，用于默认值回退。

    视为"空"的值：None、空字符串、0。用于环境变量 < ui_config < 内置默认 的
    逐级回退，保证最终一定有一个可用值。
    """
    for value in values:
        if value not in (None, "", 0):
            return value
    return default


# ---------------------------------------------------------------- 用户身份
# 你的战网完整昵称（含 # 及后面数字）。
# 用于：日志里识别“我方”玩家（哪些手牌/随从属于自己），
#       以及 Web 控制台里展示/校验。填错会导致“我方/敌方”判断颠倒。
# 来源优先级：环境变量 HS_USER_NAME > ui_config.json 的 name > 占位默认。
USER_NAME = _first(_env("HS_USER_NAME"), _UI.get("name"), "YOURNAME#1234")

# ---------------------------------------------------------------- 炉石日志根目录
# 炉石 Power.log 所在目录（Hearthstone/Logs 文件夹，不是单文件）。
# 用于：log_iter_func / 自动换牌与对战识别读取对局日志。
# 自动探测路径：%LOCALAPPDATA%\\Blizzard\\Hearthstone\\Logs。
_LOCALAPPDATA_BLIZZARD = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Blizzard", "Hearthstone", "Logs")


def _resolve_log_root() -> str:
    """环境变量 HS_LOG_ROOT > ui_config.json 的 log_root > LOCALAPPDATA 自动探测。

    只接受“当前真实存在的目录”。若三者都不存在，返回配置值/空串，
    交由调用方处理（Web 控制台会提示用户填写 & 校验目录存在）。
    """
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
# Web 控制台监听地址/端口。改端口后需用新端口访问（并同步浏览器收藏夹）。
HOST = _env("HS_HOST", "127.0.0.1")
BASE_PORT = int(_env("HS_PORT", "8765"))
# 内存里保留的实时日志条数（Web 界面上滚动的日志缓冲区大小）。
# 越大能回看越多，但占用内存越多。经环境变量 HS_LOG_BUFFER_SIZE 覆盖。
LOG_BUFFER_SIZE = int(_env("HS_LOG_BUFFER_SIZE", "500"))

# ---------------------------------------------------------------- 操作节奏
# 这三个是鼠标/点击操作的节奏控制，与“推荐延时”分离：
#   * OPERATE_INTERVAL：普通桌面对局操作（下随从/打法术/右键等）之间的随机延时
#     基准值。越大越“拟人”、越稳，但整体节奏越慢。
#   * STATE_CHECK_INTERVAL：状态机主循环里轮询对局状态/日志的间隔基准（秒）。
#     太小会空转吃 CPU，太大则对局状态切换反应迟钝。
#   * TINY_OPERATE_INTERVAL：极小操作（如取消点击、微调鼠标）之间的延时，
#     用于不希望你感觉到停顿的“无感”操作。
# 三者均可经环境变量 HS_OPERATE_INTERVAL / HS_STATE_CHECK_INTERVAL /
# HS_TINY_OPERATE_INTERVAL 覆盖。注意点击函数内部用 rand_sleep(interval)
# 做 0.75x~1.25x 随机抖动，这里给的是基准值。
OPERATE_INTERVAL = float(_env("HS_OPERATE_INTERVAL", "0.15"))
STATE_CHECK_INTERVAL = float(_env("HS_STATE_CHECK_INTERVAL", "1.0"))
TINY_OPERATE_INTERVAL = float(_env("HS_TINY_OPERATE_INTERVAL", "0.08"))

# ---------------------------------------------------------------- 自动投降默认值
# 自动投降功能的默认配置（单一来源：Web 层与 FSM_action 层共用，避免各自硬编码）。
# 真实值保存在 ui_config.json 的 auto_concede 段；这里只提供“没配置时”的兜底。
#   enabled   : 是否启用自动投降。默认 False（需用户在 Web 手动开启）。
#   threshold : AI 胜率阈值（%）。当检测到“我方胜率”低于此值才可能触发。
#   rounds    : 连续低于 threshold 的回合数。达到后才真正点“认输”，
#               避免开局一两次低胜率就误降。
DEFAULT_AUTO_CONCEDE = {"enabled": False, "threshold": 10.0, "rounds": 3}

# ---------------------------------------------------------------- 日志 / 快照
# 读取 Power.log 到尾部(EOF)后、等待下一新行的轮询间隔（秒）。
# 原先 0.2s 且连续两次 EOF 才返回，静止时每轮阻塞 0.4s；缩短后主循环对
# 日志的响应延迟大幅下降，空轮询 CPU 开销仍很低。经 HS_LOG_TAIL_WAIT_INTERVAL 覆盖。
LOG_TAIL_WAIT_INTERVAL = float(_env("HS_LOG_TAIL_WAIT_INTERVAL", "0.05"))
# game_state 快照（调试用 log/game_state_snapshot.txt）写盘的最小间隔（秒）。
# 日志每次变化都全量序列化整个 log_state 会拖慢主循环，故节流到每 N 秒一次。
# 调小能更频繁留档，但更吃 CPU/磁盘。经 HS_SNAPSHOT_WRITE_INTERVAL 覆盖。
SNAPSHOT_WRITE_INTERVAL = float(_env("HS_SNAPSHOT_WRITE_INTERVAL", "5.0"))

# ---------------------------------------------------------------- 卡牌数据下载
# 炉石卡牌 JSON 数据源（hearthstonejson.com，zhCN 最新版本）。
# 用于 cards.json 缺失时联网下载、以及未知卡牌触发一次重下载补齐名称。
# 可通过 HS_JSON_URL 指向镜像/本地文件。
JSON_URL = _env("HS_JSON_URL",
                "https://api.hearthstonejson.com/v1/latest/zhCN/cards.json")
# 联网下载/重下载的超时（秒）。防止无网/慢网时主循环被 requests.get 卡死。
# 经 HS_DOWNLOAD_TIMEOUT_SECONDS 覆盖。
DOWNLOAD_TIMEOUT_SECONDS = int(_env("HS_DOWNLOAD_TIMEOUT_SECONDS", "30"))

# ---------------------------------------------------------------- OCR 模型目录
# PaddleOCR 模型所在目录（det/rec/cls 三个 ppocrv4 子模型）。
# 用于：PaddleOcrAdapter.load() 加载模型；缺模型会报 OCR 不可用。
# 来源优先级：环境变量 HS_OCR_MODEL_ROOT >
#             %LOCALAPPDATA%/AutoHS/ocr_models/paddleocr（默认自动探测）。
OCR_MODEL_ROOT = os.environ.get("HS_OCR_MODEL_ROOT") or os.path.normpath(
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                 "AutoHS", "ocr_models", "paddleocr"))


# ---------------------------------------------------------------- 推荐自动化调优
def _user_roi() -> Optional[tuple[int, int, int, int]]:
    """读取用户校准的推荐区域（ui_config.json 的 recommendation_roi）。

    校准工具 calibrate_roi.py 把桌面上拖拽结果写入该文件；这里在每次创建
    配置时应用（打包版/源码版路径一致：应用目录=本文件所在目录）。
    未配置或格式非法时返回 None 走默认值。

    返回格式：(left, top, right, bottom)，必须满足 left<right && top<bottom。
    """
    try:
        cfg_path = ROOT / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        roi = data.get("recommendation_roi")
        vals = tuple(int(v) for v in roi)
        if len(vals) == 4 and 0 <= vals[0] < vals[2] and 0 <= vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return None


def _user_confirm_roi() -> Optional[tuple[int, int, int, int]]:
    """读取用户校准的换牌"确认"按钮区域（ui_config.json 的 mulligan_confirm_roi）。

    用于换牌阶段检测“确认”按钮是否在场：按钮在 → 可安全执行换牌并点击确认；
    按钮不在 → 面板未就绪或已提交，不盲目点击。格式同 recommendation_roi。
    """
    try:
        cfg_path = ROOT / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        roi = data.get("mulligan_confirm_roi")
        vals = tuple(int(v) for v in roi)
        if len(vals) == 4 and 0 <= vals[0] < vals[2] and 0 <= vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return None


# 可通过 ui_config.json 的 delays 段覆盖的延时字段白名单。
# 这些字段默认取下方 RecommendationConfig 的上游时序值，但用户可在
# ui_config.json 里写 "delays": { "<字段名>": <数值> } 逐个覆盖，避免写死。
# 对应 Web「⏱️ 延时设置」卡片与 /api/delays 接口。
_USER_DELAY_KEYS = (
    "mulligan_ready_delay_seconds",
    "mulligan_post_ocr_delay_seconds",
    "mulligan_retry_delay_seconds",
    "first_turn_per_card_delay_seconds",
    "pre_action_delay_seconds",
    "post_action_delay_seconds",
    "ocr_preprocess_scale",
)


def _user_delays() -> dict:
    """读取用户可修改的延时（ui_config.json 的 delays 段）。

    返回仅含“合法数值”的键：只接受 >=0 的浮点数，非法/缺失的字段直接忽略，
    从而让该类字段继续用 RecommendationConfig 的默认值。这样每次实例化都会
    重新读文件，Web 保存后无需重启即对新开局/下回合生效。
    """
    try:
        cfg_path = ROOT / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        delays = data.get("delays") or {}
        result = {}
        for key in _USER_DELAY_KEYS:
            if key in delays:
                try:
                    value = float(delays[key])
                except (TypeError, ValueError):
                    continue
                if value >= 0:
                    result[key] = value
        return result
    except Exception:
        return {}


@dataclass(frozen=True)
class RecommendationConfig:
    """推荐自动化全局配置（所有实测调优数值集中于此，附来源说明）。

    本类是 frozen（不可变）dataclass，字段默认值即“上游时序/实测最佳值”。
    __post_init__ 会把 ui_config.json 里用户覆盖的推荐ROI、确认ROI、延时
    逐项覆盖到实例上，从而实现“代码默认 < 用户配置”。
    """

    # ------------------------------------------------------------------ 屏幕
    # 分辨率必须与炉石传说一致（程序校验用），DPI 100%。
    desktop_size: tuple[int, int] = (1920, 1080)
    desktop_dpi: int = 96

    # 盒子面板完整区域（屏幕坐标 left, top, right, bottom）。
    # 覆盖左侧“打法参考A”推荐面板：宽 271，高 938。
    # 可通过 ui_config.json 的 recommendation_roi 覆盖（校准工具写入）。
    recommendation_roi: tuple[int, int, int, int] = (7, 200, 202, 500)

    # ------------------------------------------------------------------ 稳定帧
    # 读取面板需连续 stable_frames 帧识别出相同文本才算稳（防动画中间帧）。
    # 面板在卡片入场/动画期会闪烁，取 2 帧同文本可滤掉不稳定的中间帧。
    max_attempts: int = 3
    stable_frames: int = 2
    # 单行识别置信度低于该值即视为"读不清"重试。太低会误读（点错牌），
    # 太高会频繁重试（卡节奏），0.70 是精度/速度的实测折中。
    min_ocr_confidence: float = 0.70
    # 识别到“不稳定/读不清”后，再次截图+OCR 之间的等待间隔（秒）。
    retry_interval_seconds: float = 0.1

    # ------------------------------------------------------------------ 换牌
    # 每局进入换牌阶段后等待 N 秒，再开始识图和换牌操作（上游时序）。
    # 作用：留出时间让盒子留牌面板就位、避免开局日志/截图尚未稳定。
    # 用户当前设为 18s（本地调优）。可通过 delays 段覆盖。
    mulligan_ready_delay_seconds: float = 18.0
    # 换牌建议已稳定识别后到实际点击之间的缓冲（秒）。
    # 用户当前设为 1s（防止面板动画中间帧导致点错）。可通过 delays 段覆盖。
    mulligan_post_ocr_delay_seconds: float = 1.0
    # 换牌阶段“重试”之间的等待间隔（秒）。区别于上面的 post_ocr：
    # post_ocr 是“识别成功 → 点击”的单次缓冲；本字段是“面板未就绪 /
    # 推荐暂不可执行 / 确认仍未消失”时，每轮重试前等多久。
    # 上游把 post_ocr 设为 0 后，若重试也复用它会变成 0 秒忙等（CPU 空转、
    # 疯狂截图）。因此单独设一个默认 5s 的重试间隔，保证失败后的重试有退避。
    mulligan_retry_delay_seconds: float = 5.0
    # 换牌"确认"按钮区域（屏幕坐标 left, top, right, bottom）。
    # 对齐 commit_choose_card 的点击点 (960,850)，以该点为中心外扩。
    # 点击确认后该按钮消失；仍能识别到"确认"说明换牌未提交成功，需重试。
    # 可通过 ui_config.json 的 mulligan_confirm_roi 覆盖。
    mulligan_confirm_roi: tuple[int, int, int, int] = (860, 810, 1060, 890)

    # ------------------------------------------------------------------ 第一回合额外延时
    # 第一回合开始时会有一批"开局生效的全局卡"（如黑暗主教本尼迪塔斯 SW_448
    # 触发 TriggerKeyword=START_OF_GAME_KEYWORD），它们要跑入场/效果动画，导致
    # 第一回合实际比普通回合更久，盒子推荐也可能更新更晚。
    # 这里在第一回合的 pre_action_delay 基础上追加延时：
    #   * 每张生效卡额外延时：Power.log 里每检测到一张 cardId 非空且
    #     TriggerKeyword=START_OF_GAME_KEYWORD 的开局触发卡，追加这么多秒。
    # 用户当前设为 3.5s/张（SW_448 实测）。
    first_turn_per_card_delay_seconds: float = 3.5

    # ------------------------------------------------------------------ 出牌
    # 每个新回合开始延时一次（给盒子更新推荐留时间），
    # 同回合内多次出牌操作之间不重复延时。调大更稳但节奏更慢。
    pre_action_delay_seconds: float = 7.0
    # 一次操作执行完成之后到下轮截图+OCR 的延时（0 = 立即开始）；
    # 配合上面"回合只延时一次"使用。>0 会拉开操作间距，让盒子有时间更新。
    post_action_delay_seconds: float = 0.5
    # 单次读取（截图+OCR+解析）的超时保护（秒）。
    recognition_timeout_seconds: float = 2.0
    # 单次执行（点击操作）结果的等待/校验超时（秒）。
    result_timeout_seconds: float = 5.0

    # ------------------------------------------------------------------ OCR
    # 预处理缩放倍数：1.5x 是实测识别精度/速度最佳点
    # （1.0x 快约 28% 但识别率下降不可接受；3.0x 无效且慢）。
    # 上游取 1.4；仍可用环境变量 OCR_PREPROCESS_SCALE 临时覆盖。
    ocr_preprocess_scale: float = 1.4
    # 自动线程数下限：OpenMP/MKL 线程默认取机器物理核数，
    # 低于此下限固定为此值（核数少的机器保守）。
    ocr_thread_min: int = 4
    # 调试用：每次 OCR 的实际输入图按顺序存盘目录（空 = 关闭）。
    # 若开启，会在该目录下生成 run_<HHMMSS>/001_*.png 系列，便于排查“识别
    # 到了什么图”。优先被环境变量 OCR_FRAME_DIR 覆盖。
    ocr_frame_dump_dir: str = ""

    def __post_init__(self) -> None:
        # 用户可覆盖项依次应用（代码默认 < ui_config.json）：
        # 1) 推荐区域 ROI；2) 换牌确认按钮 ROI；3) 各延时。
        roi = _user_roi()
        if roi is not None:
            object.__setattr__(self, "recommendation_roi", roi)
        confirm_roi = _user_confirm_roi()
        if confirm_roi is not None:
            object.__setattr__(self, "mulligan_confirm_roi", confirm_roi)
        # 用户可在 ui_config.json 的 delays 段覆盖延时（默认采用上游时序）。
        for key, value in _user_delays().items():
            object.__setattr__(self, key, value)
