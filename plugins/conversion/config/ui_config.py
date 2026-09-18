"""
conversion/config/ui_config.py
───────────────────────────────────────────────
资源 / 模型 / 外部程序路径配置（纯 Python 最小实现）。

OCTools 通过 QSettings 存外部可执行文件与模型路径；本插件改为
「环境变量优先、缺省空串」的可运行最小版，保持引擎层 `CONFIG.path(...)`
等调用签名一致。缺省空 → 引擎层会优雅回退（探测 PATH / 提示缺失）。
"""

import os


def _store_path(name: str) -> str:
    """从本插件 store/models.json 读用户配置的资源路径（宿主「外部地址与内存设置」写入）。

    key 与 store/models.json 一致（kokoro/moss/stt → model 类；bin.ffmpeg/bin.pandoc → binary 类）。
    """
    try:
        import json
        _d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "store", "models.json")
        with open(_d, "r", encoding="utf-8") as f:
            resources = (json.load(f).get("resources") or {})
        rec = resources.get(name) or {}
        return (rec.get("path") or "").strip()
    except Exception:
        return ""


class _Config:
    """路径配置访问器：path() / model_path() / espeak_data()。优先级 store/models.json → 环境变量 → 空。"""

    @staticmethod
    def path(name: str) -> str:
        """外部程序路径目录（如 ffmpeg / libreoffice）。store → 环境变量覆盖。"""
        user = _store_path("bin." + name) if name in ("ffmpeg", "pandoc") else ""
        if user:
            return user
        env = os.environ.get("OCTCONV_" + name.upper().replace("-", "_"))
        return env or ""

    @staticmethod
    def model_path(name: str) -> str:
        """模型目录路径。store → 环境变量覆盖（如 OCTCONV_MODEL_MOSS）。"""
        user = _store_path(name)
        if user:
            return user
        env = os.environ.get("OCTCONV_MODEL_" + name.upper().replace("-", "_"))
        return env or ""

    @staticmethod
    def espeak_data() -> str:
        """espeak-ng data 目录（Kokoro 语音需要）。环境变量覆盖。"""
        env = os.environ.get("OCTCONV_ESPEAK_DATA")
        return env or ""


CONFIG = _Config()