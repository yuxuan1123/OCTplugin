"""
translation/core/engines/speech_engine.py
───────────────────────────────────────────────
语音识别（ASR / STT）引擎：音频 → 文本 —— 核心引擎层。

模型方案（用户已确认）：用 **sherpa-onnx**（OfflineRecognizer）做离线推理，
模型仍是 funasr/Paraformer 导出的 ONNX 目录（model.onnx / model.int8.onnx +
tokens.txt）。完全离线、无网络；识别统一转为 16kHz 单声道 int16。
"""

# -*- coding: utf-8 -*-

import os
import tempfile
import wave

from core.utils.file_handler import file_exists, write_text
from config.stt_config import (
    STT_LANGUAGE_LABELS, SttConfig, default_config,
)

# ════════════════════════════════════════════
#  引擎懒加载单例
# ════════════════════════════════════════════

_STT_MODEL = None
_STT_MODEL_KEY = None


def _model_files(cfg: SttConfig):
    """返回 (encoder onnx, tokens) 模型文件路径。"""
    enc = os.path.join(cfg.model_dir, "model.onnx")
    if not os.path.exists(enc):
        enc = os.path.join(cfg.model_dir, "model.int8.onnx")
    tok = os.path.join(cfg.model_dir, "tokens.txt")
    return enc, tok


def _ensure_model(cfg: SttConfig):
    """创建/复用 sherpa-onnx Paraformer 识别器（按 模型目录+线程+provider 缓存）"""
    global _STT_MODEL, _STT_MODEL_KEY
    key = (cfg.model_dir, int(cfg.sherpa_num_threads), cfg.sherpa_provider)
    if _STT_MODEL is not None and _STT_MODEL_KEY == key:
        return _STT_MODEL
    enc, tok = _model_files(cfg)
    if not os.path.exists(enc):
        raise RuntimeError(f"语音识别模型缺失: {enc}（请配置包含 model.onnx 的目录）")
    if not os.path.exists(tok):
        raise RuntimeError(f"语音识别词表缺失: {tok}")
    try:
        import sherpa_onnx
    except ImportError as e:
        raise RuntimeError(
            "sherpa-onnx 未安装，请执行: pip install sherpa-onnx（语音识别需要）") from e
    try:
        provider = cfg.sherpa_provider or "cpu"
        _STT_MODEL = sherpa_onnx.OfflineRecognizer.from_paraformer(
            encoder=enc,
            decoder=None,
            tokens=tok,
            num_threads=int(cfg.sherpa_num_threads or 2),
            sample_rate=16000,
            feature_dim=80,
            provider=provider,
            use_itn=bool(cfg.use_itn),
        )
    except Exception as e:
        # 兼容：from_paraformer 某些版本无 use_itn 参数
        import sherpa_onnx
        _STT_MODEL = sherpa_onnx.OfflineRecognizer.from_paraformer(
            encoder=enc,
            decoder=None,
            tokens=tok,
            num_threads=int(cfg.sherpa_num_threads or 2),
            sample_rate=16000,
            feature_dim=80,
            provider=provider,
        )
    _STT_MODEL_KEY = key
    return _STT_MODEL


# ════════════════════════════════════════════
#  推理（16kHz 单声道 int16 PCM → 文本）
# ════════════════════════════════════════════

def _infer(mono16k: bytes, log, cfg: SttConfig) -> str:
    import numpy as np
    model = _ensure_model(cfg)
    samples = np.frombuffer(mono16k, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return ""
    stream = model.create_stream()
    stream.accept_waveform(16000, samples)
    model.decode_stream(stream)
    return (stream.get_result() or "").strip()


def transcribe_audio(audio_path, log=lambda m: print(m), config=None) -> str:
    """识别音频文件并返回文本；失败抛异常或返回 ""。"""
    cfg = config or default_config()
    errors = cfg.validate()
    if errors:
        for e in errors:
            log(f"❌ {e}")
        return ""
    mono16k = None
    tmp = None
    try:
        # 读取 wav；若采样率/声道不符则下混至 16k 单声道
        with wave.open(audio_path, "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
        if rate != 16000 or channels != 1:
            from core.engines.audio_capture_engine import to_16k_mono
            mono16k = to_16k_mono(rate, channels, raw)
        else:
            mono16k = raw
    except Exception as e:
        log(f"   ⚠ 读取音频失败: {e}")
        return ""
    lang_label = dict(STT_LANGUAGE_LABELS).get(cfg.language, cfg.language)
    log(f"🎙️ 语音识别中（语言: {lang_label}，模型: {os.path.basename(cfg.model_dir)}）…")
    try:
        return _infer(mono16k, log, cfg)
    except RuntimeError:
        raise
    except Exception as e:
        log(f"❌ {e}")
        return ""


def transcribe_pcm16k(mono16k: bytes, log=lambda m: print(m), config=None) -> str:
    """识别一段 16kHz 单声道 int16 PCM（bytes），返回文本。"""
    cfg = config or default_config()
    errors = cfg.validate()
    if errors:
        for e in errors:
            log(f"❌ {e}")
        return ""
    if not mono16k:
        return ""
    return _infer(mono16k, log, cfg)


# ════════════════════════════════════════════
#  转换函数（与转换引擎统一签名）
# ════════════════════════════════════════════

def audio_to_text(input_path, output_path, log=lambda m: print(m), config=None) -> bool:
    """音频 → TXT（语音识别）。config: SttConfig。"""
    if not file_exists(input_path, log):
        return False
    try:
        ext = os.path.splitext(input_path)[1].lower().lstrip(".")
        log(f"🔄 {ext.upper()} → TXT（语音识别）: {os.path.basename(input_path)}")
        text = transcribe_audio(input_path, log, config)
        text = (text or "").strip()
        if not text:
            log("❌ 未能识别出文本（音频可能为空或无人声）")
            return False
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        write_text(output_path, text)
        log(f"✅ 完成 → {output_path}（{len(text)} 字符）")
        return True
    except Exception as e:
        log(f"❌ {e}")
        return False