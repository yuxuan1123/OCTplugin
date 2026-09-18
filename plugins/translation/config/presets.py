"""
translation/config/presets.py
───────────────────────────────────────────────
配置持久化：保存 / 加载 上次的 翻译 / 语音识别 / 截图框 配置。

本插件是 Electron 宿主内的 Python 后端，无 QSettings；改为把各配置
存成 JSON 文件，放在插件目录的 store/ 下。缺省用默认配置。
"""

import os
import json

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STORE_DIR = os.path.join(_PLUGIN_ROOT, "store")

_TRANSLATOR = os.path.join(_STORE_DIR, "translator.json")
_STT = os.path.join(_STORE_DIR, "stt.json")
_SCREEN_REGION = os.path.join(_STORE_DIR, "screen_region.json")


def _ensure_dir():
    os.makedirs(_STORE_DIR, exist_ok=True)


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_json(path, data):
    _ensure_dir()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 翻译配置 ──────────────────────────────
def save_last_translator_config(cfg):
    _save_json(_TRANSLATOR, cfg.to_dict())


def load_last_translator_config():
    from config.translator_config import TranslatorConfig
    d = _load_json(_TRANSLATOR)
    return TranslatorConfig.from_dict(d) if d else None


# ── 语音识别配置 ──────────────────────────
def save_last_stt_config(cfg):
    _save_json(_STT, cfg.to_dict())


def load_last_stt_config():
    from config.stt_config import SttConfig
    d = _load_json(_STT)
    return SttConfig.from_dict(d) if d else None


# ── 截图框配置 ────────────────────────────
def save_last_screen_region_config(cfg):
    _save_json(_SCREEN_REGION, cfg.to_dict())


def load_last_screen_region_config():
    from config.screen_region_config import ScreenRegionConfig
    d = _load_json(_SCREEN_REGION)
    return ScreenRegionConfig.from_dict(d) if d else None