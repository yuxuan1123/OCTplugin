# Environment · OCTplugin

本机环境创建与配置说明。**以本文件为准**，用于首次搭建项目开发环境。

---

## 1. 必修组件

| 组件 | 要求版本 | 本机当前 | 用途 |
|---|---|---|---|
| Go | ≥ 1.22 | 1.27.1 | 内核 daemon |
| Node.js | ≥ 20 | v24.20.0 | Electron 宿主 |
| npm | ≥ 10 | 11.19.0 | 宿主依赖 |
| uv | ≥ 0.5 | 0.12.13 | Python 插件运行时/依赖（托管版） |
| git | ≥ 2.30 | 2.55.0 | 版本管理 |

> Go 为 `windows/386`（32 位）安装，仍可编译 amd64/386 内核，不影响开发。

---

## 2. 安装 uv（唯一缺失组件）

uv 负责 Python 插件运行时与依赖隔离，**只能使用托管版 Python，绝不使用系统 Python**（见 `uv-preference` 配置）。

```powershell
# 官方安装脚本（装到 %USERPROFILE%\.local\bin）
irm https://astral.sh/uv/install.ps1 | iex

# 加入 PATH（每次新 shell 需重复，或写入用户环境变量持久化）
$env:Path = "C:\Users\zhangyuheng\.local\bin;$env:Path"
uv --version   # 期望 0.12.13
```

**持久化 PATH（推荐，第一次做好后长期有效）**

```powershell
[Environment]::SetEnvironmentVariable(
  "Path",
  "$([Environment]::GetEnvironmentVariable('Path','User'));C:\Users\zhangyuheng\.local\bin",
  'User')
```

---

## 3. uv 配置：只用托管 Python（D3/D4）

在项目根 `uv.toml`：

```toml
# uv.toml（本仓库根）
python-preference = "only-managed"   # 只用托管版，绝不回退系统 Python
```

准备内嵌托管 3.12（首次下载，约 30MB；之后命中本地缓存不再联网）：

```powershell
uv python install 3.12
uv python find 3.12
# 期望路径：%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe
```

> 内核定位解释器的方式即 `uv python find 3.12`（仅托管，自动满足 only-managed）。

> 用途：所有 Python 插件 venv 都基于这个托管 3.12 创建，`uv venv --python 3.12`。
> 系统 Python（`D:\language\python`、系统 `py -3.12`）仅供 uv 检测，**不得**作为插件解释器。

---

## 4. 依赖下载源与缓存（阶段B 可选配置）

默认从 **PyPI 官方源**（`https://pypi.org/simple`）下载，缓存走 **uv 全局缓存**（跨插件共享，默认 `%LOCALAPPDATA%\uv\cache`）。

如需走**国内镜像源**或**自定义缓存目录**，通过环境变量注入内核：

```powershell
# 下载镜像源（可多个，逗号/空格/分号分隔，首个最优先；不设则用 PyPI 官方源）
$env:OCTPY_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

# 自定义 uv 下载缓存目录（不设则用 uv 全局默认）
$env:OCTUV_CACHE_DIR = "D:\Project\ElectronProject\OCTplugin\.cache"

cd host; npm start     # 或 npm run smoke
```

常用国内源（`OCTPY_INDEX` 填入其一即可）：

| 源 | URL |
|---|---|
| 清华 TUNA | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple/` |
| 中科大 USTC | `https://pypi.mirrors.ustc.edu.cn/simple/` |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple` |
| 豆瓣 | `https://pypi.douban.com/simple/` |

> 说明：首个镜像作 `--index-url`，其余作 `--extra-index-url`（回退冗余）。自定缓存目录用 uv `--cache-dir`，且 venv 创建与 pip 安装共用，避免重复下载。

---

## 5. Go 内核

```powershell
cd kernel
go mod init github.com/octplugin/kernel
go mod tidy                 # 拉取依赖（WS 等）
go build ./cmd/kerneld      # → kernel/kerneld.exe
```

对外协议：`kernel  ↔  host` 用 **localhost WebSocket**（D1/D8，随机端口 + token）；`kernel  ↔  plugin` 用 **stdio JSON-RPC**。

---

## 6. Electron 宿主

```powershell
cd host
npm install                 # 安装 Electron 及依赖
npm start                   # 拉起内核 daemon、WS 连接、显示主窗口
npm run smoke               # 无 GUI 端到端冒烟（内核→插件→WS 调用 hello）
```

---

## 7. 插件

一个插件 = `plugins/<plugin_id>/` 目录，含 `manifest.json` + `main.py`（Python）。

```powershell
# 手动为单个插件建 venv（由内核自动完成亦可）
uv venv --python 3.12 plugins/demo_hello/.venv
```

---

## 8. 目录约定

```
OCTplugin/
├── kernel/            Go 内核 daemon（cmd + internal）
├── host/              Electron 宿主
├── plugins/<id>/      插件目录（含 manifest.json + main.py）
	├── deps/<id>/         插件依赖隔离目标（运行时生成）
	├── .cache/            uv 下载缓存（可自定，见第 4 节）
	├── runtime/           （可选）本地预置托管 Python 存放处（D12）
├── logs/              内核/插件/崩溃日志（运行时生成）
└── store/             快照 + WAL 事务日志（运行时生成）
```

---

## 9. 常见问题

- **构建脚本找不到 `uv`** → 先执行第 2 节 PATH 设置，或用全路径 `C:\Users\zhangyuheng\.local\bin\uv.exe`。
- **`uv python list` 全是 `download available`** → 首次未缓存，先 `uv python install 3.12`。
- **绝不使用系统 Python**：若在日志看到 `python.exe` 来自 `D:\language` 或系统 `py`，是回归 bug，需修。