# 迁移 OCTools 插件进 OCTplugin · 实施计划

## Context（背景与目标）

OCTplugin 目前只有无界面插件进程（Python/Node，stdin/stdout JSON-RPC）+ 单个宿主 `index.html`，
**没有插件 UI 嵌入能力**。而待迁移的 OCTools 插件（home / md / 取色器 / 目录树 / 转换 / 合并 / 翻译）全是 PySide6 GUI。

用户约束：**前端禁用 PySide6，用 Electron/HTML；后端暂时保留 Python。**
用户决策：UI 采用 **iframe 沙箱插件页**（每插件自带 HTML UI，宿主 iframe 加载）；「添加插件」做成 **插件管理/导入能力**（本框架基建）。

目标：先搭好"插件自带 HTML UI + 动态导入"这一整套基础设施，再按顺序把 7 个插件一个个迁进来，
每个迁工 = 把纯计算逻辑做成无界面 Python 进程 + 用 HTML 重建界面（复用墨韵主题）+ 写 manifest。

## 阶段 0 · 插件 UI 嵌入基建（框架级，先做）

### 0.1 内核：插件 UI 静态文件路由 + manifest `ui` 字段
- [manifest.go](file:///d:/Project/ElectronProject/OCTplugin/kernel/internal/supervisor/manifest.go) `ManifestFields` 增加：
  ```go
  type UIDecl struct {
      Type  string `json:"type"`  // "web" 表示 iframe 插件页；空则无界面（兼容现有 demo）
      Entry string `json:"entry"` // 相对插件根，如 "ui/index.html"
  }
  ```
  并嵌入 `UI UIDecl \`json:"ui,omitempty"\``。
- [ipc/ws.go](file:///d:/Project/ElectronProject/OCTplugin/kernel/internal/ipc/ws.go) `ServeHTTP`：在 `/res/*` 分支旁新增
  `/plugin/<id>/*` 分支 → `servePluginUI`。映射 `plugins/<id>/ui/<relpath>`，复用现有 `serveRes` 的
  **目录穿越防护**逻辑（`filepath.Clean + HasPrefix`），缺失/越权一律 404/403。
- `Server` 需拿到 `pluginsDir`：`NewServer(...)` 增加参数，[main.go](file:///d:/Project/ElectronProject/OCTplugin/kernel/cmd/kerneld/main.go) 传入 `pluginsDir`。
- `plugin.list` / `plugin.details` 附带 `ui`（供宿主判断哪些插件有 web 界面、自动加到侧栏）。

### 0.2 宿主：自动侧栏 + iframe 装配 + postMessage 握手
- [host/index.html](file:///d:/Project/ElectronProject/OCTplugin/host/index.html)：左栏在固定的"设置/调试"后
  **动态追加** `ui.type=="web"` 的插件入口（墨韵样式 `.side-btn`）。
- 每个 web 插件对应一个 `<section class="panel"><iframe>`；切换面板即显示对应 iframe。
- iframe `src = <内核base> + "plugin/<id>/" + <entry相对路径>`；`<内核base>` 沿用 `kernel:ready` 已下发的
  `resBase`（其前缀同源）。**不设 `sandbox` 的晦涩限制**，用独立 iframe 天然隔离即可（MVP）。
- 握手：宿主 `iframe.onload` 后 `frame.contentWindow.postMessage({type:"oct.hello", port, token, pluginId}, "*")`；
  插件页 JS 收到后据此 `new WebSocket("ws://127.0.0.1:"+port)` 并 `kernel.hello` 鉴权，之后所有 RPC
  **在 iframe 内直接走它自己的 WS**（同源，延迟最低，无需二次中转）。
- `host/main.js`：确认 `resBase` 的完整 URL 前缀（含端口）现成可用，否则补发 `kernelBase`。

### 0.3 内核：「添加插件」导入能力 = `plugin.import` / `plugin.remove`
- [ipc/ws.go](file:///d:/Project/ElectronProject/OCTplugin/kernel/internal/ipc/ws.go) `dispatch` 增加：
  - `plugin.import {srcPath}`：校验源目录含 `manifest.json` 与 `ui/` → 复制 `plugins/<id>/`（覆盖校验）→
    `manager.Restart(id)` 或新注册启动 → 返回新插件 details。
  - `plugin.remove {pluginId}`：停进程、注销共享函数、删除 `plugins/<id>/`。
- [supervisor/manager.go](file:///d:/Project/ElectronProject/OCTplugin/kernel/internal/supervisor/manager.go)：新增
  `Import(srcPath)`（`readManifest` 校验 + 目录复制 + 启动注册+`RegisterFunctions`）与 `Remove(id)`
  （`StopPlugin` + 删目录）。依赖安装沿用现有 `deps.preview/install` + `plugin.restart`。

### 0.4 验证桥（先做通再做业务）
- 写 `plugins/demo_web/`：`manifest.json(ui.type=web)` + `main.py`（JSON-RPC，`hello` 返回当前时间）
  + `ui/index.html`（一个按钮 → iframe 内 WS 调 `plugin.call demo_web.hello` → 显示结果）。
- 手动 `npm start` 验证：侧栏出现 demo_web、iframe 渲染、按钮回调后端成功。

## 阶段 1 · 「添加插件」管理面板（宿主原生）
- [host/index.html](file:///d:/Project/ElectronProject/OCTplugin/host/index.html) 左栏新增"添加插件"入口 + 管理面板：
  - 插件列表（`plugin.list`）+ 选中 → 权限(`perms.list`)/依赖(`deps.preview`) 状态。
  - "选择目录导入"：`dialog` 选 OCTplugin 格式插件根目录 → `plugin.import` → `deps.install` → restart。
  - "移除插件"：`plugin.remove`（二次确认）。
- 该面板为宿主原生（不依赖 iframe），因为它是管理基建本身。

## 阶段 2~8 · 逐插件迁移（每插件同一套工序）
序：**home → md → 取色器 color_pick → 目录树 tree → 转换 conversion → 合并 merge → 翻译 translation**

每个迁移插件统一模板：
```
plugins/<id>/
  manifest.json     {id,name,type:"Python",entry:"main.py",ui:{type:"web",entry:"ui/index.html"},
                     dependencies:[{manager:"uv",path:"requirements.txt"}], commands:[...], functions:[...]}
  main.py           仅纯逻辑，绝对不 import PySide6；收到 {id,method,params} 从 stdin 读、写 JSON 到 stdout
  requirements.txt  该插件所需 pip 依赖
  ui/index.html     HTML + <style>/<script>；内嵌 iframe-WS 客户端（见 demo_web）；复用墨韵 CSS = ：#f3ecdd 底/墨 字/朱砂 按钮/浅金强调
  ui/app.js         （可抽出公共的 iframe→WS RPC 封装，供所有插件页复用）
  ui/app.css
```
迁移要点（取 OCTools 现有纯逻辑）：
- **home**：打卡 / 工作模式 / 网络加速（经内核权限门执行）/ 今日任务。后端做状态存取与脚本门；
  打卡与工作模式职责分离（内存偏好）。
- **md**：后端用 Python `markdown` 渲染（requirements.txt 加 markdown/bleach 白名单），HTML 端 `<div>` 渲染 + 自带编辑器。
- **取色器 color_pick**：后端 `color_pick.convert`（hex/rgb/hsl/hsv/cmyk 互转，原样搬 `tab_color_pick.py` 纯函数）；
  UI 用 `<input type=color>` 选色 + 逐格式展示 + 每行独立复制按钮（内存偏好：逐块复制）。
- **目录树 tree**：后端 `tree.scan {path}`（受 `file_read` 权限门保护）返回目录/文件树；UI 渲染树 + 输出/选项卡。
- **转换 conversion**：后端 `conversion.convert`（pdf/docx/image 互转，走纯 Python 库：pypdf/docx/Pillow）。
- **合并 merge**：后端 `merge.merge`（多文件合并，`pypdf` 等）；UI 输入列表 + 目标 + 输出。
- **翻译 translation**：后端 `translation.en/translate {text,from,to}`（先做通用文本翻译引擎封装，UI 走 `plugin.call`；
  屏幕 OCR/字幕/实时等扩展界面后续逐个补）。

## 关键文件清单
- 改：`kernel/internal/supervisor/manifest.go`、`kernel/internal/ipc/ws.go`、`kernel/cmd/kerneld/main.go`、
  `kernel/internal/supervisor/manager.go`、`host/index.html`、`host/main.js`
- 新增：`plugins/demo_web/{manifest.json,main.py,ui/index.html}`、`plugins/ui-app.{js,css}`（公共前端封装）、
  以及阶段2~8 每个 `plugins/<id>/` 与「添加插件」面板 HTML 片段。

## 验证
- **基建**：`cd host && npm start`；左侧能自动列出 web 插件；`demo_web` iframe 渲染、按钮回调后端成功。
- **桥连通断句**：内核 `go build` 后跑 `node host/smoke.js`（既有 A–F 冒烟保持通过），并新增一段
  `plugin.import`/`plugin.list`(带 ui) 断言。
- **每个迁移插件**：导入后在宿主点开对应面板，走一遍核心功能（取色：选色→各格式复制；转换/合并：传文件→出结果；
  翻译：输入文本→回译结果），确认后端隔离依赖（venv）与权限门正常。

> 说明：阶段 2~8 界面工作量占总工作量的大头（translation 尤其大）。本计划先夯实 0/1 基建并迁完
> home、取色器两个代表性插件跑通闭环后，再按序推进；每完成一个即冒烟验证一个。