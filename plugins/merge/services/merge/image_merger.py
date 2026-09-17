# -*- coding: utf-8 -*-
"""merge —— 图像合并器（1:1 移植自 OCTools/services/merge/image_merger.py）。

覆盖：
  - merge_gif_animated    N 张图（含多帧 gif）→ 动画 GIF
  - merge_images_contact  N 张图 → 网格「联系表」单图（自我拼接）
"""
import os
import math

from services.merge.base_merger import BaseMerger


class ImageMerger(BaseMerger):
    """图像合并器（gif 动画 / 联系表单图）"""

    supported_formats = ["gif", "jpg", "jpeg", "png", "bmp", "webp", "tiff"]

    def merge(self, files, output, log=lambda m: print(m)):
        dst = os.path.splitext(output)[1].lstrip(".").lower()
        if dst == "gif":
            return merge_gif_animated(files, output, log)
        return merge_images_contact(files, output, log, dst)


def merge_gif_animated(files, output, log=lambda m: print(m)):
    from PIL import Image
    frames = []
    durations = []
    for f in files:
        try:
            im = Image.open(f)
            try:
                while True:
                    frames.append(im.convert("RGBA"))
                    durations.append(im.info.get("duration", 500) or 500)
                    im.seek(im.tell() + 1)
            except EOFError:
                pass
        except Exception as e:
            log(f"⚠ 跳过 {os.path.basename(f)}: {e}")
            continue
        log(f"   + {os.path.basename(f)}")
    if not frames:
        log("❌ 没有可用的图片帧")
        return False
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    frames[0].save(output, "GIF", save_all=True,
                   append_images=frames[1:], duration=durations, loop=0)
    log(f"✅ 完成 → {output}（{len(frames)} 帧动画）")
    return True


def merge_images_contact(files, output, log=lambda m: print(m), dst_fmt=""):
    from PIL import Image
    thumbs = []
    for f in files:
        try:
            im = Image.open(f)
            im.load()
            thumbs.append(im.convert("RGB"))
        except Exception as e:
            log(f"⚠ 跳过 {os.path.basename(f)}: {e}")
            continue
        log(f"   + {os.path.basename(f)}")
    if not thumbs:
        log("❌ 没有可用的图片")
        return False
    cols = 2 if len(thumbs) > 1 else 1
    rows = math.ceil(len(thumbs) / cols)
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs)
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    for i, t in enumerate(thumbs):
        canvas.paste(t, ((i % cols) * cell_w, (i // cols) * cell_h))
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    fmt_name = dst_fmt.lstrip(".").lower()
    if fmt_name in ("jpg", "jpeg"):
        canvas.save(output, "JPEG", quality=92)
    elif fmt_name in ("png", "bmp", "webp", "tiff"):
        canvas.save(output, fmt_name.upper())
    else:
        canvas.save(output, "PNG")
    log(f"✅ 完成 → {output}（{len(thumbs)} 张图联系表 {cols} 列）")
    return True