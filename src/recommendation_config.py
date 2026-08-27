"""推荐自动化全局配置（兼容转发点）。

所有设置已合并到仓库根的 config.py（单一来源）。本模块仅作兼容转发，
保留 `from src.recommendation_config import RecommendationConfig` 的导入路径，
避免逐个改动引用方。新增/修改设置请直接编辑 config.py。
"""
from config import RecommendationConfig

__all__ = ["RecommendationConfig"]
