"""推荐自动化全局配置（所有实测调优数值集中于此，附来源说明）。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _user_roi() -> Optional[tuple[int, int, int, int]]:
    """读取用户校准的推荐区域（ui_config.json 的 recommendation_roi）。

    校准工具 calibrate_roi.py 把桌面上拖拽结果写入该文件；这里在每次创建
    配置时应用（打包版/源码版路径一致：应用目录=src 的上级目录）。
    未配置或格式非法时返回 None 走默认值。
    """
    try:
        cfg_path = Path(__file__).resolve().parents[1] / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        roi = data.get("recommendation_roi")
        vals = tuple(int(v) for v in roi)
        if len(vals) == 4 and 0 <= vals[0] < vals[2] and 0 <= vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return None


@dataclass(frozen=True)
class RecommendationConfig:
    # ------------------------------------------------------------------ 屏幕
    # 分辨率必须与炉石传说一致（程序校验用），DPI 100%.
    desktop_size: tuple[int, int] = (1920, 1080)
    desktop_dpi: int = 96

    # 盒子面板完整区域（屏幕坐标 left, top, right, bottom）。
    # 覆盖左侧推荐面板：宽 271，高 938。
    recommendation_roi: tuple[int, int, int, int] = (7, 200, 202, 500)


    # ------------------------------------------------------------------ 稳定帧
    # 读取面板需连续 stable_frames 帧识别出相同文本才算稳（防动画中间帧）。
    max_attempts: int = 3
    stable_frames: int = 2
    # 单行识别置信度低于该值即视为"读不清"重试。
    min_ocr_confidence: float = 0.70
    retry_interval_seconds: float = 0.1

    # ------------------------------------------------------------------ 换牌
    # 游戏开始后第 N 秒才开始换牌识图（盒子留牌面板此刻已就位）。
    mulligan_ready_delay_seconds: float = 7.0
    # 换牌识别成功到实际点击之间的缓冲（防止读错后立即点击，
    # 也留出面板稳定时间）。
    mulligan_post_ocr_delay_seconds: float = 5.0

    # ------------------------------------------------------------------ 出牌
    # 每个新回合开始延时一次（给盒子更新推荐留时间），
    # 同回合内多次出牌操作之间不重复延时。
    pre_action_delay_seconds: float = 7.0
    # 一次操作执行完成之后到下轮截图+OCR 的延时（0 = 立即开始）；
    # 配合上面"回合只延时一次"使用。
    post_action_delay_seconds: float = 0.0
    # 单次读取/单次执行的超时保护。
    recognition_timeout_seconds: float = 2.0
    result_timeout_seconds: float = 5.0

    # ------------------------------------------------------------------ OCR
    # 预处理缩放倍数：1.5x 是实测识别精度/速度最佳点
    # （1.0x 快约 28% 但识别率下降不可接受；3.0x 无效且慢）。
    # 仍可用环境变量 OCR_PREPROCESS_SCALE 临时覆盖。
    ocr_preprocess_scale: float = 1.4
    # 自动线程数下限：OpenMP/MKL 线程默认取机器物理核数，
    # 低于此下限固定为此值（核数少的机器保守）。
    ocr_thread_min: int = 4
    # 调试用：每次 OCR 的实际输入图按顺序存盘目录（空 = 关闭）。
    # 优先被环境变量 OCR_FRAME_DIR 覆盖。
    ocr_frame_dump_dir: str = ""

    def __post_init__(self) -> None:
        # 校准工具写入的用户区域优先于代码默认值。
        roi = _user_roi()
        if roi is not None:
            object.__setattr__(self, "recommendation_roi", roi)
