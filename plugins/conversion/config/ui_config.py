"""
conversion/config/ui_config.py
───────────────────────────────────────────────
资源 / 模型 / 外部程序路径配置（纯 Python 最小实现）。

OCTools 通过 QSettings 存外部可执行文件与模型路径；本插件改为
「环境变量优先、缺省空串」的可运行最小版，保持引擎层 `CONFIG.path(...)`
等调用签名一致。缺省空 → 引擎层会优雅回退（探测 PATH / 提示缺失）。
"""

import os


class _Config:
    """路径配置访问器：path() / model_path() / espeak_data()"""

    @staticmethod
    def path(name: str) -> str:
        """外部程序路径目录（如 ffmpeg / libreoffice）。环境变量覆盖。"""
        env = os.environ.get("OCTCONV_" + name.upper().replace("-", "_"))
        if env:
            return env
        return ""

    @staticmethod
    def model_path(name: str) -> str:
        """模型目录路径。环境变量覆盖（如 OCTCONV_MODEL_MOSS）。"""
        env = os.environ.get("OCTCONV_MODEL_" + name.upper().replace("-", "_"))
        if env:
            return env
        return ""

    @staticmethod
    def espeak_data() -> str:
        """espeak-ng data 目录（Kokoro 语音需要）。环境变量覆盖。"""
        env = os.environ.get("OCTCONV_ESPEAK_DATA")
        return env or ""


CONFIG = _Config()