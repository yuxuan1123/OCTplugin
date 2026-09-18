"""
translation/core/engines/translation_engine.py
───────────────────────────────────────────────
中英互译引擎（完全离线）—— 核心引擎层，支持两种模型后端，可配置并持久化：

  1. hy     : Hy-MT2-1.8B（默认，llama_cpp 本地大模型）
              中英双向，提示词式翻译，质量更好
  2. opusmt : Opus-MT（CTranslate2 + SentencePiece）
              英→中 opus-mt-en-zh-ct2（需加语言标记 >>cmn_Hans<<）
              中→英 opus-mt-zh-en-ct2

设计要点：
  - TranslatorConfig 数据模型（引擎选择 + 各引擎参数），可保存 / 加载
  - 模型按需懒加载，并做进程内缓存
  - translate() 支持 中→英 / 英→中 / 自动检测 三种方向
  - 长文本按段落切分翻译；输入自动清洗（OCR 噪声防护）

1:1 复刻 OCTools/core/engines/translation_engine.py。
"""

import os
import re

from core.utils.file_handler import file_exists, read_text, write_text
from config.translator_config import (
    ENGINE_LABELS, OPUS_MT_BASE, TranslatorConfig, default_config,
)


# ════════════════════════════════════════════
#  模型路径
# ════════════════════════════════════════════

# 默认根目录（来自 ui_config / 环境变量 OCTTR_MODEL_opus_base）
EN2ZH_CT2_DIR = os.path.join(OPUS_MT_BASE, "opus-mt-en-zh-ct2")
EN2ZH_SRC_SPM = os.path.join(OPUS_MT_BASE, "raw_en-zh", "source.spm")
EN2ZH_TGT_SPM = os.path.join(OPUS_MT_BASE, "raw_en-zh", "target.spm")

ZH2EN_CT2_DIR = os.path.join(OPUS_MT_BASE, "opus-mt-zh-en-ct2")
ZH2EN_SRC_SPM = os.path.join(OPUS_MT_BASE, "raw_zh-en", "source.spm")
ZH2EN_TGT_SPM = os.path.join(OPUS_MT_BASE, "raw_zh-en", "target.spm")


def resolve_opus_paths(base: str = ""):
    """按给定模型根目录（优先当前配置的 opusmt_base）解析 Opus-MT 全部路径。

    根目录缺省时回落为 ui_config 的 OPUS_MT_BASE（env OCTTR_MODEL_opus_base）。
    这样设置面板里保存的 opusmt_base 会即时生效。
    """
    base = (base or "").strip() or OPUS_MT_BASE
    return {
        "base": base,
        "en2zh_ct2": os.path.join(base, "opus-mt-en-zh-ct2"),
        "en2zh_src_spm": os.path.join(base, "raw_en-zh", "source.spm"),
        "en2zh_tgt_spm": os.path.join(base, "raw_en-zh", "target.spm"),
        "zh2en_ct2": os.path.join(base, "opus-mt-zh-en-ct2"),
        "zh2en_src_spm": os.path.join(base, "raw_zh-en", "source.spm"),
        "zh2en_tgt_spm": os.path.join(base, "raw_zh-en", "target.spm"),
    }

DIRECTION_LABELS = {
    "zh2en": "中 → 英",
    "en2zh": "英 → 中",
    "auto": "自动检测",
}
DIRECTION_ORDER = ["zh2en", "en2zh", "auto"]


# ════════════════════════════════════════════
#  语言检测（自动方向用）
# ════════════════════════════════════════════

def detect_language(text: str) -> str:
    """粗略判断原文语言：含较多 CJK 字符视为中文，否则视为英文"""
    text = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "zh" if cjk >= 2 else "en"


# ════════════════════════════════════════════
#  文本清洗（OCR / 屏幕翻译噪声防护）
# ════════════════════════════════════════════

_NOISE_CHARS = frozenset("\u2047\ufffd\u25a1\uFFFD")


def clean_text(text: str) -> str:
    """清洗 OCR / 输入文本：去控制字符、折叠空白、丢弃噪声行，保留空行作段落分隔。"""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.replace("\u00a0", " ").replace("\u3000", " ")
        line = "".join(ch for ch in line if ch >= " " and ch != "\x7f")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")   # 保留一个空行作段落分隔
            continue
        if any(ch in _NOISE_CHARS for ch in line):
            continue
        if line.count("\\") / max(len(line), 1) > 0.2:
            continue
        alnum = sum(1 for ch in line if ch.isalnum())
        if alnum == 0 or alnum / max(len(line), 1) < 0.35:
            continue
        lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _strip_tag_leak(text: str) -> str:
    """剥离模型偶尔回显的语言标记（cmn_Hans / >>cmn_Hans<< / * cmn_Hans …）"""
    text = re.sub(r"^\s*[*>\s\u00bb]*cmn\s*[-_ ]?\s*Hans\s*[<*\s\u00ab]*",
                  "", text, flags=re.I)
    return text.strip(" *>\t\u00bb\u00ab")


def _is_garbage_output(text: str) -> bool:
    """输出是否几乎全是乱码（⁇  □）或空白"""
    text = text or ""
    if not text.strip():
        return True
    junk = sum(1 for ch in text if ch in _NOISE_CHARS)
    if junk / max(len(text), 1) > 0.2:
        return True
    meaningful = sum(1 for ch in text if ch.isalnum())
    return meaningful == 0


# ════════════════════════════════════════════
#  引擎懒加载单例（Opus-MT，按 模型根目录 缓存）
# ════════════════════════════════════════════

# base -> {"direction": {"translator","sp_src","sp_tgt"}}
_CT2_CACHE = {}


def _check_models_ready(base: str = ""):
    """预检模型/分词器文件是否存在，给出明确报错"""
    p = resolve_opus_paths(base)
    missing = [v for k, v in (
        ("opus-mt-en-zh-ct2", p["en2zh_ct2"]),
        ("raw_en-zh\\source.spm", p["en2zh_src_spm"]),
        ("raw_en-zh\\target.spm", p["en2zh_tgt_spm"]),
        ("opus-mt-zh-en-ct2", p["zh2en_ct2"]),
        ("raw_zh-en\\source.spm", p["zh2en_src_spm"]),
        ("raw_zh-en\\target.spm", p["zh2en_tgt_spm"]),
    ) if not os.path.exists(v)]
    if missing:
        raise RuntimeError(
            "❌ 翻译模型缺失，请先准备好 Opus-MT 模型目录：\n  "
            + "\n  ".join(missing)
            + f"\n模型根目录: {p['base']}")


def _ensure_deps():
    try:
        import ctranslate2  # noqa: F401
        import sentencepiece as spm  # noqa: F401
        return spm
    except ImportError as e:
        raise RuntimeError(
            "ctranslate2 / sentencepiece 未安装，请执行:\n"
            "  pip install ctranslate2 sentencepiece") from e


def _ensure_opusmt(direction: str, base: str = ""):
    """加载 Opus-MT 引擎（懒加载 + 按模型根目录缓存）"""
    p = resolve_opus_paths(base)
    chunk = _CT2_CACHE.get(p["base"])
    if chunk is None:
        _CT2_CACHE[p["base"]] = chunk = {
            "zh2en": {"translator": None, "sp_src": None, "sp_tgt": None},
            "en2zh": {"translator": None, "sp_src": None, "sp_tgt": None},
        }
    eng = chunk[direction]
    if eng["translator"] is not None:
        return eng
    _check_models_ready(p["base"])
    spm = _ensure_deps()
    import ctranslate2
    if direction == "zh2en":
        eng["translator"] = ctranslate2.Translator(
            p["zh2en_ct2"], device="cpu", compute_type="int8")
        eng["sp_src"] = spm.SentencePieceProcessor(model_file=p["zh2en_src_spm"])
        eng["sp_tgt"] = spm.SentencePieceProcessor(model_file=p["zh2en_tgt_spm"])
    else:
        eng["translator"] = ctranslate2.Translator(
            p["en2zh_ct2"], device="cpu", compute_type="int8")
        eng["sp_src"] = spm.SentencePieceProcessor(model_file=p["en2zh_src_spm"])
        eng["sp_tgt"] = spm.SentencePieceProcessor(model_file=p["en2zh_tgt_spm"])
    return eng


# ════════════════════════════════════════════
#  Hy-MT2 引擎（llama_cpp）懒加载单例
# ════════════════════════════════════════════

_HY_LLM = None
_HY_LLM_KEY = None


def _cpu_supports_avx() -> bool:
    """检测 CPU 是否支持 AVX 指令集（llama-cpp-python 官方 CPU wheel 以 AVX 为基线）。"""
    import sys
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(39))
    except Exception:
        return True


def _ensure_cpu_for_hy():
    """Hy-MT2 运行前的 CPU 能力门槛检查（不满足直接给可操作的中文报错）"""
    if not _cpu_supports_avx():
        raise RuntimeError(
            "当前 CPU 不支持 AVX 指令集，无法运行 Hy-MT2 本地大模型"
            "（llama-cpp-python 预编译库要求 AVX 及以上指令集）。\n"
            "请在「模型参数」中切换为「Opus-MT（轻量）」。")


def _ensure_hy(cfg: TranslatorConfig):
    """创建/复用 Hy-MT2 模型（按 模型路径+上下文参数 缓存）"""
    global _HY_LLM, _HY_LLM_KEY
    key = (cfg.hy_model_path, int(cfg.hy_n_ctx),
           int(cfg.hy_n_threads), int(cfg.hy_n_gpu_layers))
    if _HY_LLM is not None and _HY_LLM_KEY == key:
        return _HY_LLM
    _ensure_cpu_for_hy()
    if not os.path.exists(cfg.hy_model_path):
        raise RuntimeError(f"Hy-MT 模型文件不存在: {cfg.hy_model_path}")
    try:
        from llama_cpp import Llama
    except ImportError as e:
        raise RuntimeError(
            "llama-cpp-python 未安装，请执行: pip install llama-cpp-python") from e
    _HY_LLM = Llama(
        model_path=cfg.hy_model_path,
        n_ctx=int(cfg.hy_n_ctx),
        n_threads=int(cfg.hy_n_threads),
        n_gpu_layers=int(cfg.hy_n_gpu_layers),
        verbose=False,
    )
    _HY_LLM_KEY = key
    return _HY_LLM


def _translate_hy_once(text: str, target_lang: str, cfg: TranslatorConfig) -> str:
    """Hy-MT2 提示词式翻译"""
    llm = _ensure_hy(cfg)
    prompt = (f"将以下文本翻译为{target_lang},注意只需要输出翻译后的结果,"
              f"不要额外解释:\n\n{text}")
    out = llm(
        prompt,
        max_tokens=int(cfg.hy_max_tokens),
        temperature=float(cfg.hy_temperature),
        top_p=float(cfg.hy_top_p),
        top_k=int(cfg.hy_top_k),
        repeat_penalty=float(cfg.hy_repeat_penalty),
        stop=["\n\n"],
        echo=False,
    )
    return out["choices"][0]["text"].strip()


def models_ready(config=None) -> bool:
    """当前所选翻译引擎是否已就绪（用于 UI 提示，不做加载）"""
    cfg = config or default_config()
    try:
        if cfg.engine == "hy":
            return os.path.exists(cfg.hy_model_path)
        _check_models_ready(cfg.opusmt_base)
        return True
    except Exception:
        return False


def warmup(direction: str = "auto", config=None) -> bool:
    """后台预热：按所选引擎预加载翻译模型"""
    cfg = config or default_config()
    try:
        if cfg.engine == "hy":
            _ensure_hy(cfg)
            return True
        if direction in ("auto", "zh2en"):
            _ensure_opusmt("zh2en", cfg.opusmt_base)
        if direction in ("auto", "en2zh"):
            _ensure_opusmt("en2zh", cfg.opusmt_base)
        return True
    except Exception:
        return False


# ════════════════════════════════════════════
#  核心翻译
# ════════════════════════════════════════════

def _translate_once(text: str, direction: str, base: str = "") -> str:
    """翻译单段文本（不切分），方向已归一化到 zh2en / en2zh。"""
    if direction == "zh2en":
        eng = _ensure_opusmt("zh2en", base)
        tokens = eng["sp_src"].encode(text, out_type=str) + [""]
        results = eng["translator"].translate_batch([tokens])
        out = eng["sp_tgt"].decode(results[0].hypotheses[0])
        return "" if _is_garbage_output(out) else out.strip()
    eng = _ensure_opusmt("en2zh", base)
    tokens = eng["sp_src"].encode(f">>cmn_Hans<< {text}", out_type=str) + [""]
    results = eng["translator"].translate_batch([tokens])
    out = _strip_tag_leak(eng["sp_tgt"].decode(results[0].hypotheses[0]))
    return "" if _is_garbage_output(out) else out.strip()


def _split_paragraphs(text: str) -> list:
    """按空行切段（保留翻译可读性），单段过长时再按句切分"""
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paras:
        paras = [text.strip()]
    out = []
    for p in paras:
        if len(p) > 800:
            sentences = [s.strip() for s in re.split(
                r"(?<=[。！？!?；;])\s*", p) if s.strip()]
            out.extend(sentences or [p])
        else:
            out.append(p)
    return out or [text.strip()]


def release():
    """释放已加载的翻译引擎缓存（Opus-MT ctranslate2 + Hy-MT2 llama），并 gc.collect()。

    供闲置回收器 / 模型管理调用；释放后下次 translate 会重新懒加载。
    """
    import gc
    global _HY_LLM, _HY_LLM_KEY
    _CT2_CACHE.clear()
    if _HY_LLM is not None:
        try:
            _HY_LLM.close()
        except Exception:
            pass
        _HY_LLM = None
        _HY_LLM_KEY = None
    gc.collect()


def translate(text: str, direction: str = "auto", log=None, config=None) -> str:
    """中英互译统一入口（按 TranslatorConfig 自动选择引擎）。

    参数:
      text      : 原文
      direction : zh2en / en2zh / auto
      log       : 可选日志回调（线程安全由调用方保证）
      config    : TranslatorConfig；缺省用默认（hy 引擎）
    返回译文；输入为空返回 ""。
    """
    text = str(text or "").strip()
    if not text:
        return ""
    text = clean_text(text)
    if not text.strip():
        return ""
    cfg = config or default_config()
    direction = direction if direction in DIRECTION_LABELS else "auto"
    if direction == "auto":
        direction = "zh2en" if detect_language(text) == "zh" else "en2zh"

    # 重置所用模型引擎的闲置计时（模型管理用空闲回收），并按下 store 策略自注册
    try:
        from core.idle_manager import get_manager
        from resources import register_engine
        _key = "hymt2" if cfg.engine == "hy" else "opusmt"
        register_engine(_key, release)
        get_manager().touch(_key)
    except Exception:
        pass

    hy_target = "中文" if direction == "en2zh" else "英文"
    eng_label = ENGINE_LABELS.get(cfg.engine, cfg.engine)
    if cfg.engine == "hy":
        _ensure_cpu_for_hy()
    paras = _split_paragraphs(text)
    parts = []
    for i, para in enumerate(paras):
        if log:
            log(f"   🌐 翻译第 {i + 1}/{len(paras)} 段（{len(para)} 字符，{eng_label}）…")
        try:
            if cfg.engine == "hy":
                out = _translate_hy_once(para, hy_target, cfg)
            else:
                out = _translate_once(para, direction, cfg.opusmt_base)
        except Exception as e:
            if log:
                log(f"   ⚠ 第 {i + 1} 段翻译失败: {e}")
            continue
        out = _strip_tag_leak(out)
        if out and not _is_garbage_output(out):
            parts.append(out)
    return "\n\n".join(parts)


# ════════════════════════════════════════════
#  文件翻译（txt / md → txt）
# ════════════════════════════════════════════

def translate_file(input_path, output_path, log=lambda m: print(m),
                   direction: str = "auto", config=None) -> bool:
    """把文本文件（txt / md）翻译并写入 output_path（txt）。"""
    if not file_exists(input_path, log):
        return False
    try:
        raw = read_text(input_path)
        if not raw.strip():
            log("❌ 文件内容为空，无法翻译")
            return False
        log(f"🌐 开始翻译（{direction}，共 {len(raw)} 字符）…")
        out = translate(raw, direction, log, config=config)
        if not out.strip():
            log("❌ 翻译结果为空")
            return False
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        write_text(output_path, out)
        log(f"✅ 完成 → {output_path}")
        return True
    except Exception as e:
        log(f"❌ {e}")
        return False