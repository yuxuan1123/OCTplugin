"""
translation/config/ui_config.py
───────────────────────────────────────────────
模型 / 外部程序路径配置（纯 Python 最小实现），供本插件引擎层使用。

OCTools 通过 config/ui_config.json 管理模型路径（paths.models.root + 各子项），
本插件（Electron 宿主内的 Python 后端）改为「环境变量优先、缺省空串」的可运行最小版，
保持引擎层 `CONFIG.model_path(...)` 等调用签名一致。环境变量前缀 OCTTR_MODEL_。
缺省空 → 引擎层优雅回退（模型缺失给出清晰中文提示，绝不崩溃）。
"""

import os


def migrate_model_path(value, fallback: str = "") -> str:
    """把历史配置里保存的旧模型路径修正为当前路径（对齐 OCTools）。

    value 真实存在 → 原样返回；否则返回 fallback（当前默认值）。
    """
    if not isinstance(value, str) or not value:
        return fallback
    if os.path.exists(value):
        return value
    return fallback or value


class _ModelConfig:
    """路径配置访问器：model_path() / model_root()。环境变量可覆盖。"""

    @staticmethod
    def path(name: str) -> str:
        env = os.environ.get("OCTTR_" + name.upper().replace("-", "_"))
        return (env or "").strip()

    @staticmethod
    def model_path(name: str) -> str:
        """单个模型路径/目录。环境变量模型如 OCTTR_MODEL_HY_PATH。"""
        env = os.environ.get("OCTTR_MODEL_" + name.upper().replace("-", "_"))
        return (env or "").strip()

    @staticmethod
    def model_root() -> str:
        """模型根目录（供「浏览」起始目录；无统一根则返回空）。"""
        env = os.environ.get("OCTTR_MODEL_ROOT")
        return (env or "").strip()


CONFIG = _ModelConfig()