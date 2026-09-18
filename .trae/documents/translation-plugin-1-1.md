# translation 插件 1:1 复刻（OCTools → 本 Electron 项目）

## Context（背景）
用户要求把 [OCTools/plugins/translation](../../../../D:/Project/PythonProject/OCTools/plugins/translation) 这一**商业级翻译套件**1:1 复刻到本项目的 `plugins/translation`。后端逻辑完全复刻（**只许优化，不许简化**），UI 用 HTML 并套用本软件「国风墨水」配色，排版复刻 OCTools。必须包含：**两种模型**（Hy-MT2-1.8B 本地大模型 / Opus-MT 轻量）+ **五种实时功能**（屏幕OCR / 屏幕翻译 / 屏幕实时翻译 / 屏幕字幕 / 语音翻译）。

现状：目标插件 `plugins/translation` 仅有一个 stdlib 在线翻译骨架（Google 免费接口），与本需求差距巨大，需整体重做后端与 UI。

**已与用户对齐的决策：**
- 悬浮窗/框选：**跨平台纯 Python 原生悬浮窗**，用 Tkinter（stdlib）为主、Windows 用 ctypes/win32 兜底，以**子进程**方式由插件自行管理，**不改 Electron**。
- 依赖：**完整隔离 venv**（`requirements.txt` + manifest `dependencies`，内核用 uv 装到 `deps/translation/.venv`）。
- 模型：**完全复刻 OCTools**——OCR 用 PaddleOCR `PP-OCRv6_tiny_det/rec`(+onnxruntime)；语音识别用 **sherpa-onnx**（OfflineRecognizer）做离线推理（改用 sherpa-onnx），而模型仍是 funasr/Paraformer 导出的 ONNX（`model.onnx` + `tokens.txt`）。

## 复刻目标清单（对照 OCTools）
| OCTools 文件 | 复刻职责 | 目标形态 |
|---|---|---|
| `core/engines/translation_engine.py` | 中英互译引擎（hy/opusmt/auto/清洗/分段/文件翻译） | 后端同名模块 |
| `config/translator_config.py` | TranslatorConfig 字段/校验/持久化、ENGINE_LABELS/ORDER | 后端 `translator_config.py`（存插件目录 JSON） |
| `core/engines/ocr_engine.py` | PaddleOCR 懒加载 + ocr_image/ocr_pil_image | 后端 `ocr_engine.py` |
| `core/engines/audio_capture_engine.py` | pyaudiowpatch 回环采集 + to_16k_mono 预处理 | 后端 `audio_capture_engine.py` |
| `core/engines/speech_engine.py` | 语音识别 | 后端 `speech_engine.py`，用 **sherpa-onnx**（OfflineRecognizer）推理，模型为 funasr/Paraformer 导出的 ONNX（model.onnx + tokens.txt） |
| `core/engines/screenshot_engine.py` | 区域截图 | 后端用 mss+Pillow 跨平台截屏 |
| `services/recipes/auto_region_capture.py` | 定时区域捕获（实时翻译帧源） | 后端线程 |
| `services/recipes/image_translate.py` | recognize / recognize_translate | 后端 |
| `services/recipes/builtin_speech_recognize.py` | 系统声音→识别 控制器 | 后端 |
| `services/recipes/realtime_speech_translate.py` | 声音→识别→翻译 | 后端 |
| 5 个 `apps/*.py` | 五种功能应用的生命周期/worker | 后端 orchestrator + 原生悬浮窗子进程 |
| 悬浮 overlay/region_box | 置顶半透明显示 + 拖拽框选 | Tkinter 子进程（Win32 兜底） |
| `tab_translation.py` + 7 张 card | UI 排版 | HTML（plugin.css 主题） |
| `set_translator.py` | 引擎参数对话框 | HTML 参数窗（新窗口，设置面板入口） |

## 架构
- **后端** = `plugins/translation/main.py`（stdin/stdout JSON-RPC），扩展为会话式：register 完方法后，还负责创建原生悬浮窗子进程、维护 5 个应用循环线程、向 HTML 实时推送状态。
- **前端** = `ui/index.html` + `app.js` + `ui.js`（现有 OCT 桥接），`OCT.callPlugin("translation", method, params)` 调用后端；后端用 `event.emit` 推送 `translation.<事件>`，前端 600ms 轮询兜底。
- **原生悬浮子系统** = `apps/overlay/` 下的子进程脚本：`region.py`（拖拽框选返回 bbox）、`float.py`（置顶半透明文本悬浮窗，stdin 收文本帧），均由后端 spawn pip，通过 stdin JSON 通信，退出即关。跨平台：Tkinter `-topmost/-alpha/-transparentcolor`；Windows 点击穿透用 ctypes `WS_EX_LAYERED|WS_EX_TRANSPARENT`。

## 模块划分（后端新增文件）
```
plugins/translation/
  main.py                      # 重写：JSON-RPC 服务 + 应用编排 + 事件推送
  requirements.txt             # 新增：重型依赖
  translator_config.py         # TranslatorConfig 移植
  translation_engine.py        # hy/opusmt 翻译引擎移植
  ocr_engine.py                # PaddleOCR 移植
  audio_capture_engine.py      # 回环采集 + 16k 单声道预处理
  speech_engine.py             # sherpa-onnx 离线识别（funasr/Paraformer ONNX 模型）
  screenshot_engine.py         # mss+Pillow 跨平台截图
  registry.py                  # 应用元数据（5 种功能行）移植
  apps/
    __init__.py
    app_controller.py          # 应用 启动/停止/状态/日志
    worker_bridge.py           # 线程→主循环 事件队列
    screen_ocr_app.py
    one_shot_screen_translate_app.py
    realtime_screen_translate_app.py
    screen_subtitle_app.py
    speech_translate_app.py
    overlay/                    # 原生悬浮窗子进程
      __init__.py
      region_select.py          # 拖拽框选（子进程）
      float_overlay.py          # 置顶半透明显示（子进程）
```

### manifest.json（改）
`dependencies` 指向 `path:requirements.txt, manager:pip`；保留 `functions` 并新增 app/引擎方法；`permissions` 增 `net`（可视化/音频不需要但模型下载可能需要）。

### requirements.txt（新增）
```
llama-cpp-python, ctranslate2, sentencepiece,
paddleocr, onnxruntime,
pyaudiowpatch, sherpa-onnx,
numpy, pillow, mss
```
（pyaudiowpatch Windows；Linux/mac 回环用 sounddevice 等价，见验证阶段。）

## 后端 JSON-RPC 方法契约（前端调用）
- `translation.ping` / `translation.langs`（保留）
- `translation.translate` `{text,direction}` → {source,text}（改用本地引擎，方向 auto/en2zh/zh2en）
- `translation.directions` → 方向列表
- `translation.engine.get` / `.set` → 当前引擎 hy/opusmt + 持久化
- `translation.config.get` / `.set` → TranslatorConfig 读写
- `translation.models.status` → 各子系统模型缺失预警
- `translation.app.start` `{name}` / `.stop` / `.status` → 5 种应用控制
- `translation.app.set_region` `{name,x,y,w,h}` → 预置框选区域
- `translation.app.region` → 触发原生框选子进程，回传 bbox
- `translation.logs` → 日志缓存
- 后端经 `event.emit`（method=`event.emit`,params=`{name:'translation.<evt>',data:{...}}`）推送：`app.state`、`text_ready`、`result_ready`、`status`、`error`、`log_line`。

## UI 排版（复刻 OCTools tab 顺序，plugin.css 主题）
```
[方向卡片]   方向下拉(auto/中→英/英→中) · 翻译引擎下拉(Hy-MT2-1.8B/Opus-MT)
             · 「模型参数…」按钮 · 默认引擎 Hint · 缺模型警告
[屏幕翻译卡片] 三行应用: 屏幕OCR / 屏幕翻译 / 屏幕实时翻译  (行=名称+提示+启动/停止按钮+状态点)
[语音翻译卡片] 两行应用: 屏幕字幕 / 语音翻译
[原文卡片]   textarea · 「载入文件…」「清空」
[开始翻译]   主按钮（#a8875a 暗金）
[译文卡片]   只读 textarea · 「复制译文」「保存译文…」
[日志卡片]   滚动日志（code 块 #eae1d1）
```
引擎参数用独立 HTML 窗（复用宿主「新窗口」打开机制）：引擎选择/模型预加载/悬浮窗字号·模式·背景/引擎参数(hy: 模型路径·n_ctx·线程·GPU层·max_tokens·temperature·top_p·top_k·repeat_penalty; opus: 模型根目录)/恢复默认/确定写回。

## 复用点（本仓库已有）
- `plugins/*/ui/app.js` 的 `OCT.callPlugin` + `oct.ready`（已存在，不改）
- `resources/theme/plugin.css` 令牌 `--paper/--ink/--gold/--code/--line`
- 宿主「新窗口打开参数」机制（参照已有插件，如设置面板/取色器）
- 内核依赖安装链路 `kernel/internal/deps/deps.go`（声明 `dependencies` 即可触发 uv venv）

## 实施顺序（增量，每阶段可验证）
1. **依赖 + manifest**: 写 `requirements.txt`、改 `manifest.json` dependencies/functions；跑一次内核触发 uv venv 安装（可先装轻的子集验证链路）。
2. **配置层**: `translator_config.py`（字段/校验/JSON 持久化）+ ENGINE_LABELS/ORDER + `translation.directions`。
3. **核心引擎**: 移植 `translation_engine.py`（hy/opusmt/clean/detect/split/translate/translate_file），先行用 `translation.translate` 跑通 1:1 翻译。
4. **OCR + 截图**: `ocr_engine.py` + `screenshot_engine.py`(mss)。
5. **音频 + ASR**: `audio_capture_engine.py` + `speech_engine.py`（loopback→16k mono→funasr）。
6. **原生悬浮子系统**: `overlay/` 子进程（region_select + float_overlay），用脚本单测横跨位置/透明度。
7. **应用编排**: 移植 5 个 app + `app_controller.py` + worker_bridge；后端事件推送。
8. **HTML UI**: 按上述排版实现全部卡片 + 引擎参数窗 + 实时状态订阅。
9. **整合验证**（见下）+ 兼容性打磨。

## 验证
- 后端自测：`venv py main.py` 喂 JSON-RPC，测 `translate`（zh2en/en2zh/auto）、`app.start screen_ocr` 等。
- 模型缺失降级：未配置模型时 `translation.models.status` 返回明确中文缺模型提示，UI 显示警告，不崩。
- OCR：框选区域→识别出文字（PaddleOCR tiny）。
- 一次性/实时：区域→截图→识别→翻译→`float_overlay` 显示；实时用定时帧。
- 字幕/语音：loopback 采集系统声音→funasr→字幕/中文译文上屏。
- UI 复刻比对 OCTools 排版与文案；国风配色与暗金按钮一致。
- 事件推送：HTML 能实时收到 `app.state/result_ready`，离线轮询兜底正常。
- 跨平台说明：Win32 全可用；Linux/mac 回环走 sounddevice、悬浮层 Tkinter，若某平台缺失给出明确报错而非静默失败。

## 风险
- 重型依赖安装体积大、首次 uv 安装慢；模型文件体积大且需用户提供（设置里指定路径）。
- Tkinter 在 uv-managed 纯 Python 是否携带 Tcl/Tk 需验证；缺失时 Windows 回落 ctypes/win32 悬浮、截屏用 mss。
- funasr/Paraformer ONNX + PaddleOCR 首次需模型文件；ASR 走 sherpa-onnx 离线推理，缺模型给清晰提示。