扫描范围：全项目文件（不含 `.gitignore` 已忽略的 `deps/`、`tools/`、`node_modules/` 等）
> 说明：本报告仅做静态文件审计，**不修改任何代码**。按严重程度分三级：🔴 必须清理 / 🟡 建议清理 / 🔵 信息归档

---

## 一、严重问题（🔴 必须清理）

### 1.1 根目录遗留敏感 token 文件

| 文件 | 说明 | 风险 |
|------|------|------|
| `_auth.txt` | 内核启动时生成的 auth 快照，硬编码 token `442110bbaebee...` 和端口 `55296` | 🔴 运行时产物不应出现在源码根目录；token 泄露风险 |
| `_kerr.txt` | 内核 stderr 日志快照（2026-09-16 转换插件崩溃循环） | 🔴 运行时产物 |
| `_ve.txt` | 内核 stdout 日志快照（2026-09-16 插件启动记录） | 🔴 运行时产物 |

**备注**：文件名前缀 `_` 暗示本来打算用 .gitignore 排除，但 `.gitignore` 中没有覆盖 `_*.txt` 模式。

---

### 1.2 kernel/ 下临时编译产物

| 文件 | 说明 |
|------|------|
| `kernel/kerneld.exe.bak` | 之前编译的可执行文件备份 |
| `kernel/.tmp_kd.exe` | 临时编译输出，已被 `.gitignore` 规则 `*.exe` 部分覆盖但文件仍在磁盘上 |
| `kernel/.tmp_kd.err.log` | go build stderr 临时日志 |
| `kernel/.tmp_kd.out.log` | go build stdout 临时日志 |

---

### 1.3 deps/ 下孤儿虚拟环境（没有对应 plugins/ 目录）

`deps/` 下有 3 个孤立的 venv 目录，其对应的插件源码已经从 `plugins/` 中删除：

| 孤儿 deps 目录 | 曾对应该插件 | 证据 |
|---------------|-------------|------|
| `deps/demo_hello/` | `plugins/demo_hello/` | `_kerr.txt` 日志中有 `plugin demo_hello started` 记录；`架构落地拆分.md` 文档提到它 |
| `deps/demo_np1/` | `plugins/demo_np1/` | 同上 |
| `deps/demo_np2/` | `plugins/demo_np2/` | 同上 |

当前 `plugins/` 目录列表：`color_pick / conversion / demo_js / demo_web / home / md / merge / translation / tree`，已不存在 `demo_hello / demo_np1 / demo_np2`。

**额外发现**：`plugins/` 下还多了一个 `.cache` 目录（不是插件，是内核 RegisterAll 时遍历出来的"伪插件"）。

---

### 1.4 host/ 目录下临时调试脚本（硬编码 token）

| 文件 | 问题 |
|------|------|
| `host/_extra.js` | 硬编码端口 `56911`、硬编码 token `577b6f10fcc7...`（32 字符 hex）、手动 spawn 内核、写 `_kfert.log`。注释说"复现多连接场景"，用完即删 |
| `host/_startkern.js` | 硬编码 `port=56911`、手动 spawn kerneld.exe、写 `_kfert.port` 和 `_kfert.log`。注释说"供浏览器测试" |

**风险**：代码里写死了真实 token 值，应立即清除。

---




### 1.5 大体积安装包重复存留

| 文件 | 体积估计 | 说明 |
|------|---------|------|
| `plugins/conversion/pandoc-3.11-windows-x86_64.msi` | ~50MB | Pandoc 安装包，conversion 插件资源。应通过下载/安装器按需获取而非提交 |
| `tools/_downloads/LibreOffice_26.8.0_Win_x86-64.msi` | ~400MB | LibreOffice 安装包缓存（tools/ 本身被 .gitignore 忽略，但如果有人忘了忽略 _downloads 子目录就会被提交） |
| `tools/libreoffice/LibreOffice_26.8.0_Win_x86-64.msi` | ~400MB | 同一 msi 的第二份拷贝（已解压出了完整 LibreOffice 所以这个应该删除） |
| `resources/shanhe12.zip` | ~数 MB | `resources/shanhe12/` 目录已经解压存在，zip 是冗余归档 |

---

## 二、中等问题（🟡 建议清理）

### 2.1 插件目录内运行时缓存/状态文件（未被 .gitignore 覆盖）

| 文件 | 说明 |
|------|------|
| `plugins/home/home_state.json` | home 插件的运行时状态（打卡记录、任务列表），.gitignore 有 `plugins/*/.cache/` 但 home_state.json 不在 .cache 子目录里 |
| `plugins/color_pick/.cache/live.json` | 颜色拾取器缓存 |
| `plugins/md/.cache/config.json` | md 编辑器缓存 |
| `plugins/translation/store/screen_region.json` | 识别框配置（store/ 在根 .gitignore 被忽略，但若 plugins/*/store 未被匹配则会被提交） |
| `plugins/translation/store/translator.json` | 翻译器配置 |
| `plugins/translation/store/models.json` | 模型路径配置 |
| `plugins/translation/store/stt.json` | STT 配置 |
| `plugins/conversion/store/models.json` | 转换模型路径配置 |

**注意**：根 `.gitignore` 只覆盖了根目录的 `store/`，但 `plugins/*/store/` 子目录**没有被排除**。这是一个常见的 gitignore 遗漏。

---

### 2.2 插件目录内测试/临时脚本

| 文件 | 说明 |
|------|------|
| `plugins/conversion/_nor_pandoc_test.py` | 临时测试脚本（注释明确写"用完即删"），用绝对路径 `d:\Project\ElectronProject\OCTplugin\plugins\conversion`，硬编码路径 |
| `plugins/conversion/.agent_harnesses.json` | 一个外部工具（agent 环境检测库）的配置文件，不属于插件代码。内容列了 Cline/Cursor/Trae/Devin 等 AI IDE，是 **pytest-agent-eval 或类似工具的产物** |

---

### 2.3 resources/ 目录下生成工具残留

| 文件 | 说明 |
|------|------|
| `resources/shanhe12/gen_cycle.py` | 生成 `white_1~6.svg` 和 `black_1~6.svg` 的脚本，一次性工具，产物已提交但脚本残留 |
| `resources/shanhe12/preview.png` | SVG 山水画的预览图，非运行时必需 |

---

### 2.4 host/package.json 引用了不存在的脚本

```json
"scripts": {
    "start": "electron .",
    "smoke": "node smoke.js"
}
```

`host/smoke.js` **不存在**。运行 `npm run smoke` 会报 `ENOENT`。

---


---

### 2.5 kernel .gitignore 覆盖不完整

根 `.gitignore` 有：
```
*.exe                    # 覆盖所有 exe
kernel/*.exe             # 内核目录下再覆盖一次（重复但无害）
*.bak                    # 缺失！没有 *.bak 规则
```

**缺失规则建议补充**：
```
*.bak
*.tmp
*.temp
_k*fert*
_k*.log
_ve*.txt
_auth*.txt
.tmp_kd*
```

---

### 2.6 plugins/conversion/hub/ 目录存在但被 gitignore 排除

`.gitignore` 最后一行：`plugins/conversion/hub/`

该目录是 HuggingFace modelscope 的模型缓存（Kokoro-82M 语音合成模型的 .pth 权重文件）。虽然被 gitignore 排除了，但**目录本身存在于仓库物理路径中**，会让新贡献者困惑"这是什么？要提交吗？"

---

### 2.7 根目录文档（.md）的"归档 vs 源码"定位模糊

以下 md 文件位于项目根，看起来是设计文档/任务跟踪文档，但没有集中到 `docs/` 目录：

| 文件 | 内容推断 |
|------|---------|
| `M1-WS消息Schema.md` | WS 协议 Schema |
| `environment.md` | 环境搭建说明 |
| `架构落地拆分.md` | 架构设计 |
| `要求.md` | 需求清单 |
| `设计要点细化.md` | 设计细节 |
| `进程说明.md` | 进程模型 |
| `首要任务.md` | 首版任务列表 |
| `任务列表.md` | 一般任务列表 |
| `uv.toml` | 但这是 uv 配置文件，应保留 |
| `插件生成规定与注意点.md` | 插件开发指南 |
| `.trae/documents/` 下 4 个 md | Trae IDE 自动生成的 AI 对话文档（merge_plugin_replication.md / migrate-octools-plugins.md / model-manager-plan.md / translation-plugin-1-1.md） |

**建议**：要么集中到 `docs/` 目录，要么用 gitignore 排除 AI 自动生成的 `.trae/documents/`。

---

## 三、已确认的 Bug（🟠 功能异常）

### 3.1 插件启动崩溃循环（有历史记录）

`_kerr.txt` 日志显示 2026-09-16 conversion 插件启动即因 `ModuleNotFoundError: No module named 'docx'` 崩溃，内核进入 crash→restart 循环（500ms → 1s → 2s）直至 exhausted。

**根因**：`plugins/conversion/requirements.txt` 里的依赖（`python-docx` 等）未安装进 `deps/conversion/.venv/`。

---

### 3.2 `.gitignore` 不覆盖 `plugins/*/store/` 导致运行时配置可能被提交

根 `.gitignore` 只有一行 `store/`，匹配根目录的 store，但 `plugins/xxx/store/` 嵌套目录**不被匹配**。

这意味着用户运行时配置（模型路径、翻译器设置等）可能被 git 追踪和意外提交。

---

### 3.3 `.gitignore` 不覆盖 `plugins/*/home_state.json` 等散落在根的运行时 JSON

类似 3.2 的问题，运行时产物分散在各处，gitignore 没有一个统一规则。

---

### 3.4 demo_* 插件引用了不存在的 deps 目录（已废弃的插件）

内核启动时会尝试 RegisterAll() 遍历 plugins/ 目录，不存在 demo_hello/np1/np2 源码所以这个问题已经不会触发。但 `deps/` 下的孤儿 venv 目录**浪费磁盘空间**且暗示混乱的迭代历史。

---

## 四、信息：正常文件（🔵 归档参考）

以下文件**存在且有效**，但可能让新贡献者困惑"要不要动"：

| 路径 | 说明 | 状态 |
|------|------|------|
| `kernel/cmd/kerneld/main.go` | 内核入口 | ✅ 核心代码 |
| `kernel/internal/ipc/ws.go` | WS 服务器 + 全部 RPC 方法 | ✅ 核心代码 |
| `kernel/internal/supervisor/*.go` | 插件生命周期管理 | ✅ 核心代码 |
| `kernel/internal/perms/perms.go` | 权限 Gate | ✅ 核心代码 |
| `kernel/internal/protocol/protocol.go` | JSON-RPC 协议定义 | ✅ 核心代码 |
| `kernel/internal/deps/deps.go` | 依赖安装器 | ✅ 核心代码 |
| `host/main.js` | Electron 主进程 | ✅ 核心代码 |
| `host/index.html` | 宿主界面 | ✅ 核心代码 |
| `host/package.json` | ✅ 但 smoke 脚本指向不存在的 smoke.js | ⚠️ 见 2.4 |
| `plugins/*/manifest.json` | 每个插件的 manifest | ✅ 核心代码 |
| `plugins/*/main.py` | 每个插件的 Python 入口 | ✅ 核心代码 |
| `plugins/*/ui/*.{html,js}` | 每个插件的 UI | ✅ 核心代码 |
| `plugins/translation/registry.py` | 插件内部分发表 | ✅ 可能被 main.py 引用 |
| `plugins/translation/preload.py` | 插件预加载脚本 | ✅ 可能被 main.py 引用 |
| `plugins/conversion/resources.py` | 插件资源管理 | ✅ 可能被 main.py 引用 |
| `resources/theme/plugin.css` | 共享主题 | ✅ 核心资源 |
| `resources/icons/*.svg` | Bootstrap Icons 风格图标 | ✅ 核心资源 |
| `resources/shanhe12/white_1~6.svg`, `black_1~6.svg` | 网络加速滑块山水画 | ✅ 核心资源 |
| `kernel/go.mod`, `go.sum` | Go 模块定义 | ✅ 核心 |
| `uv.toml` | uv 项目配置 | ✅ 核心 |

---

## 五、清理优先级建议

### P0（立即删除，含安全风险）
1. 根目录 `_auth.txt` / `_kerr.txt` / `_ve.txt`（含真实 token）
2. `host/_extra.js` / `host/_startkern.js`（含真实 token）
3. `kernel/kerneld.exe.bak` / `kernel/.tmp_kd.exe` / `kernel/.tmp_kd.err.log` / `kernel/.tmp_kd.out.log`

### P1（尽快清理，避免提交运行时数据）
4. `plugins/conversion/_nor_pandoc_test.py`
5. `plugins/conversion/.agent_harnesses.json`
6. `plugins/home/home.bat` / `plugins/home/net.ps1` / `plugins/home/open_work_noon_temp.bat`
7. `plugins/home/home_state.json`
8. `plugins/md/md/` 整个目录（含 .lnk 快捷方式 + 用户笔记）
9. `deps/demo_hello/` / `deps/demo_np1/` / `deps/demo_np2/`
10. `plugins/conversion/pandoc-3.11-windows-x86_64.msi`
11. `tools/libreoffice/LibreOffice_26.8.0_Win_x86-64.msi`（已解压，冗余）
12. `resources/shanhe12.zip`

### P2（gitignore 补全 + 结构整理）
13. 根 `.gitignore` 补充 `plugins/*/store/`、`plugins/*/home_state.json`、`_*.txt`、`*.bak`、`.tmp_*`
14. `host/package.json` 移除 `"smoke": "node smoke.js"` 或创建 smoke.js
15. 统一 logo 资源到一处（`resources/logo/` 或 `resources/icons/`）
16. 根目录设计文档集中到 `docs/`（或确认不需要）
17. 补充 `.gitignore` 排除 `.trae/documents/`（AI IDE 自动产物）

---

## 六、统计

| 分类 | 数量 | 典型示例 |
|------|------|---------|
| 🔴 必须删除的文件 | ~22 个 | `_auth.txt`, `_extra.js`, `home.bat`, `.bak` |
| 🟡 建议清理的文件 | ~15 个 | `_nor_pandoc_test.py`, `.agent_harnesses.json`, 3 个 .msi |
| 🟠 运行时配置未被 gitignore 覆盖 | 8 个 json | `plugins/*/store/*.json` |
| 孤儿目录（无对应 plugin） | 3 个 | `deps/demo_hello/np1/np2/` |
| 根目录设计/任务 md | ~10 个 | 建议集中到 `docs/` |
| 重复 logo 资源 | 2 套 | logo16/32/48/128.png 各存两处 |

---

**结论**：这个项目作为开发中的 MVP，留下了大量**运行时快照**、**临时测试文件**和**已删除插件的残留 venv**。建议按 P0→P1→P2 顺序清理一轮，再重新审视哪些文件是真正应该提交到版本库的。

# OCTplugin 真实 Bug 审计报告

> 生成时间：2026-09-18
> 说明：**只列真实能复现的运行时 bug**，不涉及"垃圾文件/冗余"类条目。

---

## 🔴 Bug 0（致命级）：kernel normalizeImport 漏映射 sherpa-onnx → sherpa_onnx，translation 插件永远不启动

**位置**：`kernel/internal/deps/deps.go` 第 292-304 行

```go
func normalizeImport(name string) string {
    switch strings.ToLower(name) {
    case "python-docx":
        return "docx"
    case "pillow":
        return "PIL"
    case "python-pptx":
        return "pptx"
    case "beautifulsoup4":
        return "bs4"
    }
    return name // ← sherpa-onnx 走到了这里
}
```

**缺失映射**：`sherpa-onnx`（发行包名）→ `sherpa_onnx`（Python import 名）

**完整故障链**：
1. `parsePackageName("sherpa-onnx>=1.9.0")` 解析出包名 `sherpa-onnx`
2. `normalizeImport("sherpa-onnx")` 直接返回原名字（没映射）
3. `probeImports` 生成探测脚本：
   ```python
   try:
       import sherpa-onnx    # ← 非法 Python 语法！连字符不能作模块名
   except Exception:
       _ok=False
   ```
4. **`import sherpa-onnx` 直接是 SyntaxError**（不是 ModuleNotFoundError），异常被 except 捕获，`_ok=False`
5. probeTimeout 120 秒后返回 false → `RequirementsSatisfied` 返回 false
6. translation manifest 是 `"load_mode": "always"` → StartAll 里 `startAlways` → `pluginReady` 返回 false → `depsPending=true` → **translation 永远不启动**

**额外证据**：`plugins/translation/core/engines/speech_engine.py` 第 51 行和 69 行明确 `import sherpa_onnx`（下划线），确认发行包名和 import 名的差异。

**修复方向**：在 normalizeImport 的 switch 里加 `case "sherpa-onnx": return "sherpa_onnx"`。

---

## 🔴 Bug 1：translation 插件硬编码 D 盘缓存目录，Hy 引擎依赖安装会失败

**位置**：`plugins/translation/main.py` 第 146 行

```python
def _install_hy_dep():
    ...
    cmd = ["uv", "pip", "install", "-q", "--python", py,
           "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
           "--cache-dir", "D:/x/.cache", "llama-cpp-python"]
```

**问题**：`--cache-dir D:/x/.cache` 硬编码。用户机器没有 D 盘、或 D 盘没有 `x` 目录时，uv 会报 `directory does not exist`，Hy 引擎依赖安装必然失败。

**复现**：引擎切 Hy → 点"一键安装 Hy 引擎依赖" → 无 D:\x\.cache 时失败。

---

## 🔴 Bug 2：conversion 插件 document_engine.py 裸 import 大量重库，venv 未就绪时立即崩溃

**位置**：`plugins/conversion/core/engines/document_engine.py` 第 32-53 行

```python
# pypandoc 有 try-except 保护（但 requirements.txt 里也强制声明了，矛盾，见 Bug 3）
try:
    import pypandoc
except Exception:
    pypandoc = None

# 以下全是裸 import，无任何保护：
from docx import Document              # python-docx
from markdown import md_lib            # markdown
from bs4 import BeautifulSoup          # beautifulsoup4
from PIL import Image, ImageDraw       # Pillow
import img2pdf
from pypdf import PdfWriter, PdfReader # pypdf
import pdfplumber
from reportlab.lib.pagesizes import A4 # reportlab
# ... 还有 reportlab 的 platypus / styles / units / pdfmetrics / ttfonts
```

**崩溃发生条件**：当 venv 还没装好或装到一半时，插件进程启动 → `ModuleNotFoundError` → 进程 exit 1 → kernel crash→restart 循环。

**真实历史证据**：根目录 `_kerr.txt` 第 29-45 行是**旧版本日志**（当时 conversion manifest 是 `load_mode: always`，现在已改为 lazy）：

```
2026/09/16 15:27:06 [kernel] plugin conversion crashed (exit 1); restarting in 1s
Traceback (most recent call last):
  ...
  File "...\document_engine.py", line 36, in <module>
    from docx import Document
ModuleNotFoundError: No module named 'docx'
```

**当前版本的触发路径**：conversion 已改为 `load_mode: lazy`，正常情况下 `pluginReady` 会在启动前拦截（deps 探测）。但有两条旁路仍可能触发：
1. 内核 deps 探测被跳过（若注入的 depsReady 函数为 nil，则 pluginReady 直接返回 true）
2. venv 存在但**不完整**——探测脚本能跑通（顶层包都在），但某个子 import 路径崩（比如 `from docx import Document` 走的是已装包，但 `from reportlab.pdfbase.ttfonts import TTFont` 因 reportlab 的底层 C 扩展缺失而崩）

**修复方向**：要么所有重库都 try-except 保护 + 降级提示，要么 kernel 在 RequirementsSatisfied 里做更彻底的探测（不只顶层 import，还要关键子模块）。

---

## 🟡 Bug 3：conversion 的 requirements.txt 与 import 声明矛盾

**位置**：`plugins/conversion/requirements.txt` 第 16 行 vs `document_engine.py` 第 32-35 行

requirements.txt 强制声明了 `pypandoc==1.14`，但 document_engine.py 里：
```python
try:
    import pypandoc
except Exception:
    pypandoc = None
```

**矛盾**：requirements.txt 强制装 → pypandoc 一定会可用 → 那为什么要 try-except？结果就是 uv 花时间装了包，运行时代码又要做一堆 `pypandoc is None` 的判空逻辑（`_ensure_pandoc()` 里约 10 行）。

**更严重的是同文件其他重库（python-docx、markdown、bs4、Pillow 等）全是裸 import，try-except 只保护了 pypandoc 一个——要么全保护要么全不保护，半保护状态逻辑不一致**。

---

## 🟡 Bug 4：kernel deps.go 的 subProcessTimeout 15s 对重型依赖安装过短

**位置**：`kernel/internal/deps/deps.go` 第 29 行

```go
const subProcessTimeout = 15 * time.Second   // uv venv 创建 + pip install 全部走这个
const probeTimeout      = 120 * time.Second  // 但 import 探测给了 120s
```

**问题**：安装比探测 import **更重**——要下载几 MB 到几十 MB 的包、编译 C 扩展（paddleocr、onnxruntime、kokoro 都带），但安装只给 15s，探测反而给了 120s。逻辑反了。

**实际影响**：`installPython` 里的每一步都走 `i.run()` → `cmdOutputTimeout(uv, ..., subProcessTimeout)`：
1. `uv venv` 创建 → 15s 内一般能完成（空 venv 很快）
2. `uv pip install -r requirements.txt` → **15s 大概率截断**（首次下载网络慢时，pytorch/onnxruntime 单个包就超过 15s 下载不了）
3. 安装失败暂存目录被清理 → 用户看到"安装失败"

**对比**：同一个文件 `probeImports` 给了 120s 做探测（import 几秒就好），而安装反而只给 15s。

---

## 🟡 Bug 5：document_engine.py 硬编码 C:\Windows\Fonts + _register_cjk_font 找不到字体时直接崩溃

**位置**：`plugins/conversion/core/engines/document_engine.py` 第 101 行 和 第 121-124 行

```python
win_fonts = os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts"  # 第 101 行

# 第 121-124 行：找不到字体时直接抛 RuntimeError → 所有 PDF 转换路径崩溃
raise RuntimeError(
    "❌ 未找到 Windows 中文字体，无法生成 PDF。"
    "请确认 C:\\Windows\\Fonts 下有 simsun.ttc 或 msyh.ttc"
)
```

**问题**：
1. Fallback 硬编码了 `C:\Windows`，非常见安装路径就会找不到
2. `_register_cjk_font` 在找不到任何中文字体时直接抛异常，**没有 fallback 走英文 reportlab 默认字体**。这意味着任何 PDF 生成（MD→PDF、DOCX→PDF via reportlab、TXT→PDF）都会崩溃，而不是退化成无中文字体的英文 PDF

**对比同文件**：`_pil_cjk_font()`（txt→图片路径）做了三层 fallback（Windows → Linux noto → Linux dejavu → default），reportlab 那条路完全没 fallback。

---

## 🟡 Bug 6：host/package.json 的 smoke 脚本指向不存在的文件

**位置**：`host/package.json` 第 8 行

```json
"scripts": {
    "start": "electron .",
    "smoke": "node smoke.js"   // ← host/smoke.js 不存在
}
```

运行 `npm run smoke` 会报 ENOENT。

---

## 统计

| 级别 | 数量 | 说明 |
|------|------|------|
| 🔴 致命 | 2 | **Bug 0（translation 永远不启动，核心功能全废）**、Bug 1（Hy 引擎装不上） |
| 🔴 严重 | 1 | Bug 2（conversion 裸 import + venv 未就绪 = 崩溃循环，虽当前 lazy 模式降低触发概率但架构问题存在） |
| 🟡 中等 | 4 | Bug 3（声明/实现矛盾）、Bug 4（安装超时 15s）、Bug 5（字体硬编码+无 fallback）、Bug 6（smoke 脚本） |

**最关键的就是 Bug 0**——`normalizeImport` 漏了 `sherpa-onnx → sherpa_onnx` 这一行映射，直接导致 translation 插件（always 模式）**永远 depsPending 永远不启动**，用户打开软件就看不到翻译功能。这个是一行代码就能修的致命 bug。
