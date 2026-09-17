# -*- coding: utf-8 -*-
"""merge —— 拼接/合并业务层（自包含）。

- concat.py        多文件「拼接」编排（业务主入口）
- base_merger.py   合并器抽象接口
- same_format_merger.py  同格式合并（pdf/docx/md/txt/html/xlsx/csv/json/pptx）
- image_merger.py  图像合并（gif 动画 / 联系表）
- audio_merger.py  音频星型合并（wav 枢纽）
- video_merger.py  视频星型合并（mp4 枢纽）
- media_merger.py  ffmpeg concat demuxer（媒体同格式合并）
- bridge.py        跨插件调用 conversion（格式转换）
- planner.py       本地可达性判定（复用 conversion.formats 元数据）
"""

from .concat import concat as concat_files  # noqa: F401

__all__ = ["concat_files"]