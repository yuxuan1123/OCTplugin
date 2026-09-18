# 模型 / 外部依赖统一管理（模型管理）实施计划 v2

## Context（为什么做）

应用启动即被 `~300MB` 的 PaddleOCR 预加载占满内存。用户希望把本应用所有**重资源**（>100MB 的 AI 模型 + 外部二进制）统一交给用户管理：

- **安装地址用户自选**（UI 填路径）
- **加载时机**可选：懒加载（默认）/ 启动 / 进页
- **结束时机**可选：常驻 / 闲置10分钟释放 / 用完即退
- 默认全部**懒加载**，加载后可**闲置 10 分钟自动释放**，释放后下次用时重新懒加载

涉及对象集中分布在 translation / conversion 两个插件。

## ⚠ 本次修订（v1 被拒后）两条关键约束

1. **宿主不写死任何模型/外部配置 schema**。配置归各插件自己声明、各自读写自己的 store。宿主的「模型管理/外部配置」卡片是**动态发现**：调用各插件的 `.models.status` 拿到声明式清单，动态聚合渲染；宿主只做通用渲染，不含任何 `translation.opusmt` 之类的硬编码 key。
2. **LibreOffice 卡合并**进同一张统一卡片「外部地址与内存设置」，替换原 K 卡；该卡片同时承载 AI 模型路径选择 + 外部二进制路径与**下载等操作**（复用 LibreOffice 已有的 radio/路径/下载安装/进度条交互）。

## 现状锚点

- 宿主设置面板：`host/index.html` settings-list（L268-280，每排 名称+[设置]，`data-sec`）；卡片区 L284-411；LibreOffice K 卡 L379-411（radio=`auto/dir/msi`、dir/msi/url 输入、`清空配置/应用配置/开始下载安装` 按钮、进度条 `loBar`）；`rpc()`=`ipcRenderer.invoke("rpc",...)`，保存走 `rpc("plugin.call",{pluginId,method,params})` 取 `r.result`
- `host/main.js`：`dialog:pickDir`/`pickFile` 已具备
- translation RPC：`translation.models.status`（已存在，仅一方法）、`translation.config.get/set`、`translation.preload`（L337 hardcode timing）
- conversion RPC：`conversion.libreoffice_status/set/install`（宿主→store 的先例，写项目根 `store/libreoffice.json`）
- 模型路径现走环境变量无持久化：translation `config/ui_config.py`（OCTTR_MODEL_*）、conversion `config/ui_config.py`（OCTCONV_*）
- 引擎懒加载缓存（release+touch 对象）：
  - translation `core/engines/translation_engine.py`：`_CT2_CACHE` L135（_ensure_opusmt L167，ctranslate2 无 close）、`_HY_LLM/_HY_LLM_KEY` L199-200（_ensure_hy L224，Llama 有 `.close()`）；入口 `translate` L331
  - `ocr_engine.py`：`_OCR` L38/`_OCR_FAILED` L39；入口 `ocr_image` L86
  - translation `speech_engine.py`：`_STT_MODEL/_STT_MODEL_KEY` L26-27（sherpa）；入口 `transcribe_audio` L99
  - conversion `core/engines/tts_engine.py`：`_KOKORO_PIPES` L165、`_MOSS_RT/_MOSS_RT_MODEL` L166-167；入口 `synthesize_text_to_audio` L290
  - conversion `core/engines/speech_engine.py`：`_STT_MODEL` L41（FunASR 无 close）
  - conversion `core/engines/ffmpeg_utils.py`：`_FFMPEG_CACHE` L56、`get_ffmpeg_path()` L59-94
  - conversion `core/engines/document_engine.py`：`_ensure_pandoc` L63-74（pypandoc，可用 env `PYPANDOC_PANDOC`）

## 方案概览

### 1) 各插件自声明模型清单 + 自身 store（宿主不写死）

每个插件新增 `manifest`（代码内常量，插件自己的资源清单），并新增通用 RPC；配置各自写**本插件根 `store/models.json`**：

- **translation** 声明：opusmt / hymt2 / paddle(OCR) / stt(SenseVoice)
- **conversion** 声明：kokoro / moss / stt / bin.ffmpeg / bin.pandoc / libreoffice（复用现有 libreoffice.json 集成清单，由合并后的卡片统一呈现，下载走现有 install）
- 每项：`{key, label, type: model|binary, path, load, unload, action: {download?}}`
- `load ∈ lazy|startup|tab`，`unload ∈ keep|idle|once` + `idle_min`（分钟，默认 10），默认 `lazy / idle`，闲置时长由 `idle_min` 自定义

插件侧新增 RPC：
- `*.resources.status`：返回声明清单 + 当前已持久化配置 + 就绪状态（`{ok, result:{groups:[{key,label,type,path,load,unload,ready}]}}`），宿主据此动态渲染
- `*.resources.set`：params `{item:{key,path,load,unload}}` → 写本插件 `store/models.json` + 缓存失效（ffmpeg `reset_ffmpeg_cache()`）+ unload 改 keep 取消回收计划 → `{ok,result}`
- `*.preload`：透传 `timing`(startup|tab)，复用现有 translation.preload 结构

### 2) 宿主统一卡片「外部地址与内存设置」（动态聚合）

- 设置列表原 `LibreOffice 引擎(K)` 改为 **`外部地址与内存设置`**（`data-sec="K"` 复用以省几处映射改动）
- 卡片：顶部标题 + 通用说明；下方分两段动态渲染：
  - **AI 模型**：调用 `translation.resources.status` 与 `conversion.resources.status` 聚合，每行 = 模型名 + 路径输入(`dialog:pickDir`)+ 加载时机下拉 + 结束时机下拉 + 保存；不写死任何 key，纯循环渲染
  - **外部二进制**：同样动态列出 bin.ffmpeg / bin.pandoc / bin.libreoffice；LibreOffice 行保留现有 `auto/dir/msi`、url、`应用/清空/下载安装`、进度条逻辑（复用现有 K 卡 JS）
- 保存回调 `rpc("plugin.call",{pluginId,method:"<pid>.resources.set",params:{item}})`

### 3) 引擎 release（每引擎新增 release()）

- translation_engine.py：`_CT2_CACHE.clear()`；`_HY_LLM.close()` 后置 None
- ocr_engine.py：`_OCR.clear(); _OCR_FAILED.clear()`
- speech_engine.py（两处）：`_STT_MODEL=None; _STT_MODEL_KEY=None`
- tts_engine.py：`_KOKORO_PIPES.clear(); _MOSS_RT=None; _MOSS_RT_MODEL=None`
- 之后 `gc.collect()`；全部按 key 缓存，重新懒加载天然成立

### 4) 闲置回收器 `core/idle_manager.py`（每插件各一份，插件 venv 隔离）

- `IdleModelManager` 单例：`ensure(key, release_fn, unload, idle_s)` + `touch(key)` + `set_policy(key, unload, idle_s)` + daemon 线程 60s tick 释放 `unload=idle`；`once` 用 try/finally 入口返回后即 release；`keep` 不回收
- idle_s 由用户配置的 `idle_min`（分钟）换算；引擎入口自注册（`resources.register_engine(key, release)`）保证宿主直写 store 后照样生效
- touch 点 = 各引擎 public 入口函数体开头（translate/ocr/transcribe/tts）

### 5) 加载时机落地

- lazy（默认）：现状不动
- startup：插件 main() 启动按 policy 逐模型 warmup（preload.py 已按 timing 支持，main.py 按清单加模型粒度）
- tab：插件 UI iframe 显示时页面 JS 调 `*.preload {timing:"tab"}`

### 6) 外部二进制

- ffmpeg：`get_ffmpeg_path()` 前置读本插件 store `bin.ffmpeg.path`；变更调 `reset_ffmpeg_cache()`
- pandoc：`_ensure_pandoc` 前置若用户路径存在则 `os.environ["PYPANDOC_PANDOC"]=path`
- LibreOffice：不动现有 `libreoffice_runtime.py`/store；仅宿主卡片合并呈现，下载/安装复用现有 install

## 文件改动清单

新增：
- `plugins/translation/store/models.json`（插件自管，初始全 lazy/idle/10min 空路径）
- `plugins/conversion/store/models.json`
- `plugins/{translation,conversion}/core/idle_manager.py`
- `plugins/translation/resources.py`、`plugins/conversion/resources.py`（声明清单 + status/set 逻辑，避免塞进 main.py）

修改：
- `host/index.html`：settings-list K 改名"外部地址与内存设置"；K 卡重写为动态聚合（模型段 + 外部二进制段，LibreOffice 复用现有 radio/下载/进度条）
- `plugins/translation/config/ui_config.py`：`model_path()` 优先读本插件 store/models.json
- `plugins/translation/core/engines/{translation_engine,ocr_engine,speech_engine}.py`：release+touch
- `plugins/translation/{main.py,preload.py}`：resources.status/set、preload 透传 timing、startup warmup
- `plugins/conversion/config/ui_config.py`：`path()`/`model_path()` 优先读本插件 store
- `plugins/conversion/core/engines/{tts_engine,speech_engine,ffmpeg_utils,document_engine}.py`：release+touch、ffmpeg reset、pandoc 路径
- `plugins/conversion/{main.py,resources.py,preload.py?}`：resources.status/set、timing、startup warmup

## 验证

1. 两插件 `store/models.json` 初始文件能被各自 `resources.status` 读出、`resources.set` 写入并即时让 `ui_config.model_path()`/`get_ffmpeg_path()` 生效
2. 触载一个模型（用引擎入口）→ 等 >10min 观察 `release()` 执行（缓存清空、内存回落）→ 再调入口能重新懒加载
3. 宿主设置面板：设置列表出现"外部地址与内存设置"，卡片动态列出两插件全部模型与二进制（含 LibreOffice），改路径/时机保存 `resources.set` 返回 `{ok,result}`，LibreOffice 下载/安装/进度条照常
4. `py_compile` 全部改动文件；`host/main.js` 无关改动不破坏现有 K 卡事件