# -*- coding: utf-8 -*-
"""merge —— FFmpeg 公共工具（自包含版本，不依赖 conversion 的 config.ui_config）。

被 audio_merger / video_merger / media_merger 共用：
  - 格式清单与别名（VIDEO_FORMATS / AUDIO_FORMATS / MEDIA_EXTENSIONS）
  - 编解码参数表（VIDEO_CODECS / AUDIO_CODECS / AUDIO_EXTRA）
  - ffmpeg 定位（FFMPEG_BIN 环境变量 → PATH → 常见安装位置）
  - 执行与日志（_run_ffmpeg / _ffmpeg_summary / _log_done）
"""
import os
import shutil
import subprocess


# ════════════════════════════════════════════
#  格式清单
# ════════════════════════════════════════════

VIDEO_FORMATS = ["mp4", "avi", "mkv", "mov", "webm", "flv", "wmv", "3gp", "ogv", "gif"]

AUDIO_FORMATS = ["mp3", "aac", "wav", "flac", "ogg", "opus", "wma", "m4a", "amr", "ac3", "aiff"]

IMAGE_FORMATS = ["jpg", "png", "gif", "bmp", "webp", "tiff", "heic", "svg", "ppm", "pgm"]

MEDIA_EXTENSIONS = set(VIDEO_FORMATS) | set(AUDIO_FORMATS) | set(IMAGE_FORMATS)

FORMAT_ALIASES = {"jpeg": "jpg", "tif": "tiff", "m4v": "mp4", "oga": "ogg", "mka": "mkv"}


def _norm_ext(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return FORMAT_ALIASES.get(ext, ext)


# ════════════════════════════════════════════
#  FFmpeg 定位
# ════════════════════════════════════════════

_FFMPEG_CACHE = None


def get_ffmpeg_path():
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE
    # 1) 环境变量
    for key in ("FFMPEG_BIN", "FFMPEG"):
        env = os.environ.get(key)
        if env and os.path.exists(env):
            _FFMPEG_CACHE = env
            return env
    # 2) PATH
    p = shutil.which("ffmpeg")
    if p:
        _FFMPEG_CACHE = p
        return p
    # 3) 常见安装位置（盘符由环境变量推导，不写死字面量）
    sys_drive = os.environ.get("SystemDrive", "C:") + os.sep
    program_files = os.environ.get("ProgramFiles", sys_drive + "Program Files")
    candidates = [
        os.path.join(sys_drive, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(program_files, "ffmpeg", "bin", "ffmpeg.exe"),
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ]
    for c in candidates:
        if os.path.exists(c):
            _FFMPEG_CACHE = c
            return c
    return "ffmpeg"  # 最后交给系统 PATH 尝试


# ════════════════════════════════════════════
#  编码参数表
# ════════════════════════════════════════════

VIDEO_CODECS = {
    "mp4":  {"v": "libx264",   "a": "aac",      "extra": ["-movflags", "+faststart", "-pix_fmt", "yuv420p"]},
    "avi":  {"v": "mpeg4",     "a": "libmp3lame"},
    "mkv":  {"v": "libx264",   "a": "aac"},
    "mov":  {"v": "libx264",   "a": "aac"},
    "webm": {"v": "libvpx-vp9", "a": "libopus", "extra": ["-b:v", "0", "-crf", "33", "-deadline", "good", "-cpu-used", "4"]},
    "flv":  {"v": "libx264",   "a": "aac"},
    "wmv":  {"v": "wmv2",      "a": "wmav2"},
    "3gp":  {"v": "libx264",   "a": "aac",      "extra": ["-profile:v", "baseline"]},
    "ogv":  {"v": "libtheora", "a": "libvorbis"},
    "gif":  {"gif": True},
}

AUDIO_CODECS = {
    "mp3":  "libmp3lame", "aac": "aac", "wav": "pcm_s16le", "flac": "flac",
    "ogg":  "libvorbis", "opus": "libopus", "wma": "wmav2", "m4a": "aac",
    "amr":  "libopencore_amrnb", "ac3": "ac3", "aiff": "pcm_s16be",
}

AUDIO_EXTRA = {
    "amr":  ["-ac", "1", "-ar", "8000"],
    "mp3":  ["-b:a", "192k"],
    "aac":  ["-b:a", "192k"],
    "ogg":  ["-q:a", "4"],
    "opus": ["-b:a", "128k"],
    "wma":  ["-b:a", "192k"],
    "m4a":  ["-b:a", "192k"],
    "ac3":  ["-b:a", "384k"],
}


# ════════════════════════════════════════════
#  公共工具
# ════════════════════════════════════════════

def ensure_outdir(output_path):
    d = os.path.dirname(os.path.abspath(output_path))
    if d:
        os.makedirs(d, exist_ok=True)


def _compact_args(args):
    out = []
    for a in args:
        if len(a) > 120:
            a = a[:60] + "…" + a[-50:]
        out.append(a)
    return out


def _run_ffmpeg(args, log, quiet=False):
    """执行 ffmpeg，返回 (ok, stderr 文本)。"""
    ff = get_ffmpeg_path()
    cmd = [ff, "-hide_banner", "-y"] + args
    log(f"   $ ffmpeg {' '.join(_compact_args(args))}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        log("❌ 未找到 ffmpeg。请安装 FFmpeg 并加入 PATH，"
            "或设置环境变量 FFMPEG_BIN 指向 ffmpeg 可执行文件。")
        return False, ""
    except OSError as e:
        log(f"❌ ffmpeg 启动失败: {e}")
        return False, ""
    if proc.returncode != 0:
        if not quiet:
            lines = (proc.stderr or "").strip().splitlines()
            log("❌ ffmpeg 执行失败：")
            for line in lines[-4:]:
                log(f"   {line}")
        return False, proc.stderr or ""
    return True, proc.stderr or ""


def _ffmpeg_summary(stderr, output_path):
    dur = ""
    for line in (stderr or "").splitlines():
        if "Duration:" in line and not dur:
            dur = line.split("Duration:")[1].split(",")[0].strip()
    size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    return dur, size


def _log_done(output_path, log, dur="", kind="媒体"):
    size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    extra = f"，时长 {dur}" if dur else ""
    log(f"✅ 完成 → {output_path}（{size / 1024:.1f} KB{extra}）")


def _probe_ffmpeg_info(input_path):
    ff = get_ffmpeg_path()
    try:
        proc = subprocess.run([ff, "-hide_banner", "-i", input_path],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError:
        return False, ""
    stderr = proc.stderr or ""
    has_audio = "Audio:" in stderr
    dur = ""
    for line in stderr.splitlines():
        if "Duration:" in line:
            dur = line.split("Duration:")[1].split(",")[0].strip()
            break
    return has_audio, dur