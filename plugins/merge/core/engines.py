# -*- coding: utf-8 -*-
"""merge —— 音视频引擎（自包含，1:1 对齐 OCTools audio_engine / video_engine）。

OCTools 中由 audio_engine._audio_encode 与 video_engine.video_to_video 承担；
本项目不依赖 conversion 的引擎，改用 merge/core/ffmpeg_utils 实现等价能力。
"""
import os

from core.ffmpeg_utils import (
    AUDIO_CODECS,
    AUDIO_EXTRA,
    VIDEO_CODECS,
    VIDEO_FORMATS,
    _ffmpeg_summary,
    _log_done,
    _norm_ext,
    _probe_ffmpeg_info,
    _run_ffmpeg,
    ensure_outdir,
)


def audio_encode(input_path, output_path, log, src_is_video=False):
    """通用音频编码：-vn 丢弃视频轨，按目标格式选编码器。

    若源是视频且没有音轨，则生成等时长的静音音频，保证转换成功。
    """
    dst = _norm_ext(output_path)
    codec = AUDIO_CODECS.get(dst)
    if not codec:
        log(f"❌ 不支持的目标音频格式: {dst}")
        return False
    ensure_outdir(output_path)
    args = (["-i", input_path, "-vn", "-c:a", codec]
            + list(AUDIO_EXTRA.get(dst, [])) + [output_path])
    ok, err = _run_ffmpeg(args, log, quiet=True)
    if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        _log_done(output_path, log, kind="音频")
        return True

    # 兜底：视频没有音轨 → 生成等时长静音音频
    if src_is_video and os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
    if src_is_video:
        has_audio, dur = _probe_ffmpeg_info(input_path)
        if not has_audio and dur:
            log(f"   ⚠ 源文件没有音轨，生成等时长（{dur}）静音音频…")
            args2 = (["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                      "-i", input_path, "-t", dur, "-vn", "-c:a", codec]
                     + list(AUDIO_EXTRA.get(dst, [])) + [output_path])
            ok2, _ = _run_ffmpeg(args2, log)
            if ok2 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                _log_done(output_path, log, kind="音频")
                return True

    # 两次尝试都失败：把第一次的 ffmpeg 错误打印出来
    if err:
        lines = err.strip().splitlines()
        log("❌ 音频转换失败：")
        for line in lines[-4:]:
            log(f"   {line}")
    return False


def video_to_video(input_path, output_path, log):
    """视频 → 视频（含 视频 → 动态 GIF）。

    快路径：流拷贝（不重新编码）→ 成功即返回；
    慢路径：按目标容器选编码器完整转码。
    """
    if input_path and not os.path.exists(input_path):
        log(f"❌ 文件不存在: {input_path}")
        return False
    dst = _norm_ext(output_path)
    if dst not in VIDEO_FORMATS:
        log(f"❌ 不支持的目标视频格式: {dst}")
        return False
    log(f"🎬 视频转换: {os.path.basename(input_path)} → {os.path.basename(output_path)}")
    ensure_outdir(output_path)

    if dst == "gif":
        return _to_animated_gif(input_path, output_path, log)

    if _try_stream_copy(input_path, output_path, log):
        return True

    log("   ⚙️ 流拷贝不可用，改为完整转码…")
    return _transcode_video(input_path, output_path, log, dst)


def _try_stream_copy(input_path, output_path, log):
    """尝试 ffmpeg -c copy 直接封装（最快）"""
    log("   ⚡ 尝试流拷贝（不重新编码）…")
    ok, _ = _run_ffmpeg(["-i", input_path, "-c", "copy", output_path], log, quiet=True)
    if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        _log_done(output_path, log, kind="视频")
        return True
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
    return False


def _transcode_video(input_path, output_path, log, dst):
    prof = VIDEO_CODECS.get(dst, VIDEO_CODECS["mp4"])
    args = ["-i", input_path, "-c:v", prof["v"], "-c:a", prof["a"]]
    args += list(prof.get("extra", [])) + [output_path]
    ok, err = _run_ffmpeg(args, log)
    if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        dur, _ = _ffmpeg_summary(err, output_path)
        _log_done(output_path, log, dur=dur, kind="视频")
        return True
    return False


def _to_animated_gif(input_path, output_path, log):
    vf = ("fps=10,scale='min(480\\,iw)':-2:flags=lanczos,"
          "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
    ok, err = _run_ffmpeg(["-i", input_path, "-an", "-vf", vf, "-loop", "0", output_path], log)
    if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        _log_done(output_path, log, kind="GIF")
        return True
    return False