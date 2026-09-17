# merge 插件 1:1 复刻 OCTools「拼接/合并」页

## Context（背景与目标）

- OCTools（`D:\Project\PythonProject\OCTools`）是 PySide6 项目，其 `plugins/merge/` 是「文件拼接/合并」页：输入（源文件/源文件夹）→ 目标格式（二级选择器）→ 输出（单个文件）→ 开始拼接 → 实时日志，预设区按 src/dst 动态切换（md→docx 排版 / 图片→docx 预设 / txt·md→音频 TTS / 音频→txt STT）。
- 本项目（`D:\Project\ElectronProject\OCTplugin`）是 Electron+Python 插件架构。目标：把 OCTools merge 功能 **1:1 复刻** 到 `plugins/merge/`，后端 Python、UI HTML 复刻 OCTools 排版、配色用本项目「国风墨水」主题。**只许优化、不许简化**（商业级）。

### 用户已确认的方向（必须遵守）
1. **范围=完整**：文档拼接、图片合并、docx 排版、TTS、STT、格式转换拼接全部复刻。（截图是 conversion 的工具项，非 merge 页功能，不并入本页。）
2. **跨插件调用**：merge 做「格式转换拼接」时调用 conversion 插件的 `conversion.convert`，不自带转换引擎。
3. **允许声明依赖**：merge requirements.txt + manifest dependencies 声明第三方库，可复用 conversion 已装库（PIL/pypdf/python-docx/openpyxl/bs4/pptx）。

### 关键架构事实（已核实）
- 本项目 `plugins/conversion` 已完整移植 OCTools 转换后端（`core/engines`、`services/conversion`、`core/formats`、`config`），manifest functions：`conversion.ping`、`conversion.formats`、`conversion.convert`。
- conversion 后端：`plugin.call`（内核 ws.go）由前端触发；`conversion.convert` 接受 input/output/out_dir/target/config/src_format，返回 `{ok, log[], done}`（前端/调用方需解包 `result`，再取内层 `ok`）。
- **跨插件后端调用已验证可行**：`kernel/internal/supervisor/plugin.go` `handleLine`（L154）——插件向自身 stdout 写带 `method` 的行 → `dispatchGate` → `registry.call`（L241）→ `mgr.CallFunc` 调 conversion → `writeGateReply` 回写到插件 stdin。因此 merge 后端无需 WS token/端口，可原地同步调用 conversion。
- **注意陷阱**：`dispatchGate` 的 `registry.call` 走 `CallFunc(..., 15*time.Second)`（plugin.go L255），有 **15s 硬超时**。单文件 TTS/STT/OCR（kokoro 冷启动约 20s）会超时 → 用前端直连逃生门规避。
- 本项目现有 `plugins/merge/` 只是极简文本追加（merge.append/read），与 OCTools 无关，整体重写。
- 端点各自独立 venv，merge 后端必须自带一份 `planner` 能力判定与 `ffmpeg_utils`（不能 import conversion 的纯逻辑之外代码，但纯逻辑可复制）。

---

## 文件清单

```
plugins/merge/
  manifest.json         [重写] id=merge, entry=main.py, functions, deps, perms
  requirements.txt      [新建] merge 自身第三方库
  main.py               [重写] 异步解复用 stdin JSON-RPC + ThreadPoolExecutor + stdout 锁
  core/__init__.py      [新建]
  core/ffmpeg_utils.py  [新建] 复制 conversion/core/engines/ffmpeg_utils.py 的常量与 _run_ffmpeg
  config/__init__.py    [新建]
  config/models.py      [新建] DocxLayout/Tts/Stt/ImageDocx 轻量默认配置(to/from dict，透传给 conversion)
  config/defaults.py    [新建] 四类默认值与命名预设列表
  services/merge/__init__.py       [新建] 导出 concat_files=concat
  services/merge/concat.py         [新建] 移植 concat.py，转换动作注入 bridge.call_conversion
  services/merge/planner.py        [新建] can_concat/can_batch/reachable（merge 自持）
  services/merge/base_merger.py    [复制] BaseMerger/MergeError
  services/merge/same_format_merger.py [复制] pdf/docx/md/txt/html/xlsx/csv/json/pptx 同格式合并
  services/merge/image_merger.py   [复制] gif 动画 / 联系表
  services/merge/audio_merger.py   [复制] 星型 wav 枢纽
  services/merge/video_merger.py   [复制] 星型 mp4 枢纽（先读 OCTools video_merger.py 复制）
  services/merge/media_merger.py   [复制] ffmpeg concat demuxer
  services/merge/picture_in_doc.py [复制] 图片→docx/pdf（保留；实际走 conversion）备成保留注释
  services/merge/bridge.py         [新建] call_conversion：stdout 写 registry.call，_PEND future 阻塞等响应
  ui/index.html       [重写] 5 卡布局 + 预设区 + 参数弹窗（照 conversion/index.html 国风墨水）
  ui/app.js           [复制] conversion/ui/app.js（WS 桥 + OCT.callPlugin，selfId 自动命中 merge）
  ui/ui.js            [重写] merge 交互：formats 加载 / 预设显隐 / 执行 concat / 日志
```

---

## 后端 JSON-RPC 方法 API

统一出参 `{ok:bool, ...}`。慢任务子线程。最大结果 >8MB 走文件传递（写 .cache 返回 URL）。

| method | 入参 | 出参 |
|---|---|---|
| `merge.ping` | - | `{pong:true}` |
| `merge.formats` | `{src?:str}` | `{ok, categories:[...], source_formats:[], all_formats:[], reachable:[]}`（categories 结构对齐 conversion.formats：`[{key,name,formats:{values,labels,icons}}]`） |
| `merge.concat` | `{input?/folder?, src_format?, target, output, config?}` | `{ok, log:[str], output, done:true}` |
| `merge.presets` | `{kind?:"docx"\|"tts"\|"stt"\|"img"}` | `{ok, defaults:object, presets:{...}}` |

**conversion.formats 元数据缓存**：`merge.formats` 首次经 `bridge.call_conversion("conversion.formats")` 拉全量并缓存（全局 dict）；`reachable` 用本地 `planner.can_concat(或 src==dst)` 过滤 `all_formats`；`category.formats.values` 同时按 reachable 裁减。

### bridge.py（后端跨插件调用核心）
```python
_next = itertools.count(1)
def call_conversion(params, timeout_ms=12000):
    rid = next(_next)
    fut = _PEND.setdefault(rid, concurrent.futures.Future())
    _write_out({"v":1,"jsonrpc":"2.0","id":rid,"method":"registry.call",
                "params":{"name":"conversion.convert","params":params}})
    return _unpack(fut.result(timeout_ms/1000))  # -> conversion 的 {ok, log, done}
```
main.py 读循环：stdin 行含 `method` → 是 kernel 派发请求 → 交付 worker；否则 `obj["id"] in _PEND` → registry.call 响应 → 完成 future。stdout 写加锁（照 conversion/main.py `_write/_stdout_lock`）。

### concat 编排移植（services/merge/concat.py）
- 原 `from services.conversion.pipeline import convert as _convert` → 全部替换为注入的 `bridge.call_conversion`。
- **图片→文档(src∈IMAGE & dst∈DOC)**：对整文件夹一次性 registry.call `conversion.convert`（conversion 支持图片文件夹→pdf/docx/md/txt 批量），转发 log。merge 不实现 images_to_*。
- **图片→gif**：merge 自带 `image_merger.merge_gif_animated`。
- **同格式 src==dst**：merge 自带 `same_format_merger.merge_files`。
- **格式转换拼接 src!=dst**：逐个文件 `bridge.call_conversion(f→tmp.<dst>)`，成功后 `merge_files(...)` 合并，finally 清理 temp。
- **音频/视频 src!=dst**：merge 自带 `audio_merger`（wav）/`video_merger`（mp4），ffmpeg 走 `core/ffmpeg_utils`。
- 关键取舍：**凡"格式转换"归 conversion，凡"多文件合并"归 merge 自带合并器**。

---

## 跨插件调用方案（定稿）

**主路径＝merge 后端 `registry.call`（bridge.py）**。链路：merge UI → plugin.call{merge.concat} → merge stdout 收请求 → worker 内 registry.call → kernel dispatchGate → conversion.convert → 写回 merge stdin → future 完成 → merge 继续编排 → UI。

**逃生门（防 15s 超时）**：
- 前端 `ui.js` 的 `panelKind()` 已识别 kind。当**单文件且 target∈audio(TTS)/txt-ocr(OCR)/audio→txt(STT)** 时，merge UI 直接 `OCT.callPlugin("conversion","conversion.convert",{...,timeoutMs:600000})`（单文件拼接==一次转换，1:1 等价），渲染其 log。
- 多文件 / 纯合并类（md→docx、images→pdf 等，通常 <15s）走后端 `registry.call`。
- 可增强内核 `dispatchGate` registry.call 超时参数化（建议项，非阻塞）。

---

## UI 复刻设计

复用 `/res/theme/plugin.css` 变量（`--ink/--gold/--line/--ink-soft/--ink-faint/--celadon/--cinnabar/--code/--card-line/--font`），照 conversion/index.html 结构。

**卡片1 输入**：`#input-path`+`#browse-input`、`#folder-path`+`#browse-folder`、`#src-fmt-field`（仅文件夹模式显示 src-picker）。事件 onSourceChanged：二选一清另一半、文件夹显示 src-picker、单文件由扩展名推断 srcFmt、联动可达与输出名。

**卡片2 目标格式**：`#dst-picker`（`.cats` 分类按钮 + `.chips` 芯片，照 conversion 二级选择器）、`#dst-hint`、`#preset-area`。loadFormats() 调 `merge.formats`；reachable 控芯片 disabled；refreshDst() 换 src 重取。

**预设区显隐 panelKind（镜像 target_ctl.update_preset_area）**：
- `src∈(txt,md) & dst∈AUDIO` → TTS 行（引擎下拉 + 「语音参数…」按钮 + 摘要）
- `src==md & dst==docx` → docx 排版卡（纸张/方向/页边距/字体/行距/编号/目录/页眉页脚）
- `src∈IMAGE & dst==docx` → 图片排版行（每行张数 / 预设下拉 + 「新增预设」）
- `src∈AUDIO & dst==txt` → STT 行（语言/设备/数字归一化/模型摘要）
- 其余 → `#preset-none`

**卡片3 输出**：`#output-path`+`#browse-output`；autoOutputName：单文件按 `base.<dstExt>`，文件夹 `parent+name+_合并_.<dst>`；文档→图片输出到 `*_图片/` 文件夹。

**卡片4 开始拼接**：`#concat-btn` CTA（点击→ ui.js runConcat / 单文件 TTS/STT 走 conversion 直连）。

**卡片5 日志**：`#log`+`#state`，右键复制全文（照 conversion）。

**app.js**：直接复制 conversion/app.js。

**ui.js**：`apply()` 调 `merge.*`（timeoutMs=600000）；`runConcat()` 组装 params（文件夹→`{folder,src_format,target,output,config}` / 单文件→`{input,target,output,config}`）。

---

## manifest.json 与依赖

```json
{
  "id":"merge","name":"合并","version":"2.0.0","type":"Python","entry":"main.py",
  "python_version":"3.12","ui":{"type":"web","entry":"ui/index.html"},
  "load_mode":"lazy","permissions":["file_read","file_write"],
  "dependencies":[{"path":"requirements.txt","manager":"uv","explicit":false}],
  "functions":[
    {"name":"merge.ping","method":"merge.ping","desc":"拼接桥连通性"},
    {"name":"merge.formats","method":"merge.formats","desc":"拼接目标格式元数据 + 可达目标"},
    {"name":"merge.concat","method":"merge.concat","desc":"单文件/多文件拼接为单个文件"},
    {"name":"merge.presets","method":"merge.presets","desc":"四类设置项默认值/预设"}
  ]
}
```

**requirements.txt**（merge 自身，远小于 conversion；ML 库走 conversion 不需要）：
```
pypdf==5.1.0
python-docx==1.1.2
python-pptx==1.0.2
openpyxl==3.1.5
beautifulsoup4==4.12.3
Pillow==11.0.0
```
**ffmpeg**：非 pip，`core/ffmpeg_utils._run_ffmpeg` subprocess 调外部 ffmpeg 二进制（audio/video/media 合并必需），运行时探测 PATH，无需 manifest 声明。md/txt 合并用 stdlib 编码即可，不需 html2text。

---

## 实施顺序（每阶段独立可验证）

1. **骨架**：`main.py` 异步解复用循环 + ThreadPoolExecutor + stdout 锁；建 `core/`、`config/`、`services/merge/__init__`；重写 manifest、UI 三件套（5 卡静态 + app.js 照抄）；`merge.ping` 通。
2. **元数据**：`planner.py` → `merge.formats`（拉 conversion.formats 缓存 + 本地可达）→ ui.js 加载分类/芯片/可达。
3. **文档拼接（核心）**：`base_merger` + `same_format_merger` + `bridge` + 改造 `concat.py`；`merge.concat` 通（3 个 md→1 docx、src==dst 本机合并）。
4. **图片**：`image_merger`；图片→docx 走 conversion（图片排版 config 透传）。
5. **媒体**：`media_merger` + `audio_merger` + `video_merger` + `core/ffmpeg_utils`（星型 wav/mp4 + concat demuxer）。
6. **TTS/STT**：merge UI 单文件 TTS/STT/OCR 直连 conversion（前端逃生门 timeout 放宽）；`merge.presets` 返回四类默认与摘要。
7. **设置项 & 打磨**：docx/图片预设下拉+新增预设、参数弹窗 cfg_* 字段、日志/状态/toast、右键复制；可选内核 registry.call 超时参数化。

---

## 验证（端到端）

每阶段用临时文件实测 `merge.concat`：
- 3 个 md → 1 个 docx（走 registry.call 逐文件转换 + same_format_merger 合并）
- 2 张图 → pdf / 图 → docx（conversion 批量 + 图片排版 config）
- 多 gif → 动画、多 png → 联系表（image_merger）
- 2 个 txt → mp3（TTS 前端直连）
- mp3+flac → wav / 2 个 mp4 → 1 个（audio/video_merger + ffmpeg）
- 单文件 md→docx 等价 conversion.convert
校验 log 流、产物存在、`result.ok` 内层判定。跨插件验证前提：conversion 已装自身依赖（kokoro/funasr 走 conversion 自身 requirements）。