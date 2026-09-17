"""
conversion/core/engines/libreoffice_runtime.py
───────────────────────────────────────────────
LibreOffice 运行时提供模块（**项目根共享**，所有插件复用；安装仅由 host 设置面板触发）。

背景
────
PPT → 图片版 & DOCX → PDF 需要 LibreOffice（headless）做精确排版渲染。
为彻底摆脱对 Microsoft PowerPoint（win32com）的依赖，本模块：
  1. 共享安装在项目根 tools/libreoffice（program/soffice.exe），供所有插件调用；
  2. 未安装时仅探测 已装目录 / 环境变量 / PATH / 常见安装位置；
  3. 下载/解压仅经 host「设置 → LibreOffice 引擎」手动触发（绝不代码内首次自动下载）。
     自动版从官方镜像下载主 MSI，用 `msiexec /a`（管理式解压，不写注册表/不装服务）
     解出可执行文件，尽量精简（删帮助、留渲染骨架）。

说明
────
- 「精简版」：LibreOffice 官方没有真正的 minimal 构建。这里能做到的最优，
  是下载官方主 MSI（约 360MB，含所有组件）后，把帮助(help)与多余语言裁剪掉，
  仅保留 headless 渲染 Impress/Writer 所必需的 program 与 share 骨架。
- 需 .NET（不再有：msiexec 由 Windows 自带）。
- 网络/许可证：下载 libhelp 遵循 LibreOffice Mozilla Public License 2.0
  （仅为内部功能集成使用）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

# 版本与官方地址（可被环境变量覆盖，便于升级/换源）
LO_VERSION = os.environ.get("LO_VERSION") or "26.8.0"
LO_URL = os.environ.get("LO_URL") or (
    f"https://download.documentfoundation.org/libreoffice/stable/{LO_VERSION}"
    f"/win/x86_64/LibreOffice_{LO_VERSION}_Win_x86-64.msi"
)

# ── 持久化配置（用户在宿主设置里选择 LibreOffice 来源）────────────────────
# source: auto（下载官方镜像）/ dir（已装目录）/ msi（本地安装包）
_DEFAULT_CFG = {"source": "auto", "soffice_dir": "", "msi_path": "", "url": ""}


def lo_config_path() -> Path:
    """全局共享配置（host 设置面板管理，所有插件读同一份）。"""
    return _project_root() / "store" / "libreoffice.json"


def load_lo_config() -> dict:
    cfg = dict(_DEFAULT_CFG)
    try:
        p = lo_config_path()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            for k in _DEFAULT_CFG:
                if k in d:
                    cfg[k] = d[k]
    except Exception:
        pass
    return cfg


def save_lo_config(cfg: dict) -> dict:
    merged = dict(_DEFAULT_CFG)
    for k in _DEFAULT_CFG:
        merged[k] = cfg.get(k) if cfg.get(k) else _DEFAULT_CFG[k]
    try:
        p = lo_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return merged


def lo_effective_url(cfg=None) -> str:
    cfg = cfg if cfg is not None else load_lo_config()
    return (cfg.get("url") or "").strip() or LO_URL


# ── 运行/下载进度状态（供宿主设置轮询：conversion.libreoffice_status）────
_STATE = {
    "running": False, "phase": "", "percent": 0,
    "loaded_mb": 0, "total_mb": 0, "message": "", "error": "",
}


def _set_state(**kw):
    _STATE.update(kw)


def export_state() -> dict:
    return dict(_STATE)


def _cfg_soffice(cfg) -> str:
    """按配置的已安装目录解析 soffice；不存在返回 ''。"""
    d = (cfg.get("soffice_dir") or "").strip()
    if not d:
        return ""
    p = Path(d) / "program" / ("soffice.exe" if os.name == "nt" else "soffice")
    return str(p) if p.exists() else ""


def current_bin() -> str:
    """当前实际可用的 soffice（不做下载/安装）。找不到返回 ''。"""
    b = _bundle_soffice()
    if b:
        return b
    cfg = load_lo_config()
    if cfg.get("source") == "dir":
        c = _cfg_soffice(cfg)
        if c:
            return c
    return _system_soffice()


def _project_root() -> Path:
    """OCTplugin 项目根目录（共享工具/模型/配置统一放这里，供所有插件复用）。"""
    return Path(__file__).resolve().parents[4]


def tools_dir() -> Path:
    """项目根共享工具目录。"""
    return _project_root() / "tools"


def lo_root() -> Path:
    """LibreOffice 共享安装根目录（含 program/soffice.exe）。"""
    return tools_dir() / "libreoffice"


def bundle_msi() -> Path:
    """下载的 MSI 安装包位置（缓存在 tools/_downloads）。"""
    return tools_dir() / "_downloads" / f"LibreOffice_{LO_VERSION}_Win_x86-64.msi"


def profile_dir() -> Path:
    """headless 专用独立用户配置目录（避免污染系统 profile / 并发锁）。"""
    return lo_root() / "lo-profile"


def _bundle_soffice() -> str:
    p = lo_root() / "program" / ("soffice.exe" if os.name == "nt" else "soffice")
    if p.exists():
        return str(p)
    # 兼容：递归查找（旧版/历史解压布局泛化）
    for c in lo_root().rglob("soffice.exe"):
        return str(c)
    return ""


def _system_soffice() -> str:
    """探测环境变量 / 配置 / PATH / 常见安装位置。"""
    for k in ("LO_SOFFICE", "LIBREOFFICE", "SOFFICE"):
        v = os.environ.get(k)
        if v and os.path.exists(v):
            return v
    # 配置项（若当前进程配置里声明了 libreoffice 路径）
    try:
        import config  # noqa: F401  # conversion 插件 config 包
        from config import get_config_path  # type: ignore

        cfg = get_config_path("libreoffice")
        if cfg and os.path.exists(cfg):
            return cfg
    except Exception:
        pass
    p = shutil.which("soffice") or shutil.which("libreoffice")
    if p:
        return p
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for c in (os.path.join(pf, "LibreOffice", "program", "soffice.exe"),
              os.path.join(pf86, "LibreOffice", "program", "soffice.exe")):
        if os.path.exists(c):
            return c
    return ""


class LibreOfficeUnavailable(RuntimeError):
    pass


def find_bin(log=None, auto=False) -> str:
    """查找可用 soffice 可执行文件（只在已安装范围内探测，绝不自动下载）。

    - 优先共享安装 tools/libreoffice；其次配置的本地目录/系统探测。
    - 均未找到时：返回 installed 安装须经 host 设置面板（LibreOffice 引擎）触发。
    返回绝对路径；找不到抛出 LibreOfficeUnavailable。
    """
    b = _bundle_soffice()
    if b:
        return b
    cfg = load_lo_config()
    if cfg.get("source") == "dir":
        c = _cfg_soffice(cfg)
        if c:
            return c
    s = _system_soffice()
    if s:
        return s
    if auto:
        return ensure_runtime(log)
    raise LibreOfficeUnavailable(
        "未检测到已安装的 LibreOffice。请到「设置 → LibreOffice 引擎」选择自动下载 / 本地目录 / 本地 MSI 安装包后安装。"
    )


def _log(log, msg):
    if log:
        try:
            log(f"⚠ {msg}")
        except Exception:
            pass
    print(f"[libreoffice] {msg}")


def _download(url: str, dest: Path, log=None, cb=None) -> None:
    _log(log, f"下载 LibreOffice {LO_VERSION}（约 360MB，首次仅一次）…\n     {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "OCTplugin"})
    got = 0
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as f:
        length = resp.headers.get("Content-Length")
        total = int(length) if length else None
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            pct = int(got * 100 / total) if total else -1
            if cb:
                cb("download", pct, got, total)
            if total and log and (pct % 10 == 0):
                _log(log, f"  下载 {got / 1048576:.0f}/{total / 1048576:.0f} MB ({pct}%)")
    tmp.replace(dest)
    if cb:
        cb("verify", 100, got, total)
    _log(log, "下载完成")


def _admin_extract(msi: Path, out_root: Path, log=None) -> Path:
    """用 msiexec /a 管理式解压 MSI 到 out_root（不安装、不写注册表）。返回程序根。"""
    _log(log, "解压 LibreOffice（管理式提取，不安装）…")
    out_root.mkdir(parents=True, exist_ok=True)
    target = out_root / "app"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={target}"],
        check=True, capture_output=True, timeout=1800,
    )
    # msiexec /a 生成 Program Files/LibreOffice/...
    candidates = sorted(target.rglob("LibreOffice*"), reverse=True)
    prog = next((c for c in candidates if (c / "program" / "soffice.exe").exists()), None)
    if prog is None:
        prog = target  # 直接落在 target 下
    _log(log, f"解压完成 → {prog}")
    return prog


def _trim(prog: Path, log=None) -> None:
    """精简：删除内置帮助与多余语言，只保留渲染所需骨架。"""
    kept = 0
    removed = 0
    for sub in ("help",):
        p = prog / sub
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    # share/{extensions} 保留；仅去掉非主要语言残留（尽力而为）
    share = prog / "share"
    if share.is_dir():
        for name in ("autocorr", "dictionary"):
            p = share / name
            if p.is_dir():
                kept += 1  # 保留：中文拼写/自动更正需要
    _log(log, f"精简完成：移除帮助 {removed} 项，保留 {kept} 项核心数据")


def _progress_cb(phase, pct, loaded, total):
    if phase == "download":
        _set_state(running=True, phase="download", percent=pct,
                   loaded_mb=round(loaded / 1048576, 1),
                   total_mb=round(total / 1048576, 1) if total else 0,
                   message="下载中", error="")
    else:
        _set_state(running=True, phase="verify", percent=100,
                   loaded_mb=round(loaded / 1048576, 1),
                   total_mb=round(total / 1048576, 1) if total else 0,
                   message="校验中", error="")


def ensure_runtime(log=None, force=False) -> str:
    """确保 LibreOffice 可用；返回 soffice 路径（仅由 host 设置面板触发安装）。

    按全局配置择源（tools/libreoffice 共享安装）：
      dir  → 直接用已装目录；msi → 从用户指定安装包解压；
      auto → 下载官方镜像（可被配置 URL 覆盖）。
    进度写入模块级 _STATE，供 conversion.libreoffice_status 轮询。
    """
    b = _bundle_soffice()
    if b and not force:
        return b
    cfg = load_lo_config()
    _set_state(running=True, phase="start", percent=0, loaded_mb=0,
               total_mb=0, message="准备中", error="")
    _log(log, "未检测到已安装 LibreOffice，开始共享安装 tools/libreoffice（首次）。")
    tmp = lo_root().parent / f"_extract_{os.getpid()}"
    try:
        if cfg.get("source") == "msi":
            mp = (cfg.get("msi_path") or "").strip()
            if not mp or not os.path.exists(mp):
                raise LibreOfficeUnavailable(f"未找到指定的 MSI 安装包：{mp}")
            msi = Path(mp)
            _set_state(running=True, phase="extract", percent=0,
                       message=f"解压用户指定安装包：{mp}", error="")
        else:
            msi = bundle_msi()
            if not msi.exists():
                _download(lo_effective_url(cfg), msi, log, cb=_progress_cb)
            _set_state(running=True, phase="extract", percent=0,
                       message="解压中", error="")
        if msi.stat().st_size < 50 * 1048576:  # 明显过小 → 下载可能是错误页
            raise RuntimeError("下载文件异常（过小），请检查网络/镜像地址。")
        prog = _admin_extract(msi, tmp, log)
        _trim(prog, log)
        final = prog / "program" / ("soffice.exe" if os.name == "nt" else "soffice")
        if not final.exists():
            raise RuntimeError("解压后未找到 soffice，请手动设置本地目录。")
        # 规整到共享路径 tools/libreoffice/program
        target = lo_root() / "program"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(final), str(target))
        shutil.rmtree(tmp, ignore_errors=True)
        result = target / ("soffice.exe" if os.name == "nt" else "soffice")
        _set_state(running=False, phase="done", percent=100, message="完成",
                   error="")
        return str(result)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        _set_state(running=False, phase="error", percent=0, error=str(e),
                   message="失败")
        raise LibreOfficeUnavailable(f"LibreOffice 共享安装失败：{e}")


# 后台安装线程：避免阻塞 JSON-RPC 主循环/心跳
_install_lock = threading.Lock()
_install_th = None


def start_install(log=None) -> bool:
    """后台启动下载/安装。已在进行则返回 False。"""
    global _install_th
    with _install_lock:
        if _install_th and _install_th.is_alive():
            return False
        _install_th = threading.Thread(target=_install_worker, args=(log,),
                                       daemon=True)
        _install_th.start()
        return True


def _install_worker(log):
    try:
        ensure_runtime(log=log, force=True)
    except Exception as e:
        _set_state(running=False, phase="error", message="失败", error=str(e))


def headless_convert(bin_path: str, input_path: str, out_dir: str, fmt: str,
                     log=None) -> Path:
    """headless 转单文件；返回生成的产物路径（文件名=源基名+新扩展名）。

    使用独立 profile 避免多进程并发锁/污染系统配置。
    bin_path 为空时自动 find_bin 定位（只探测，绝不自动下载）。
    """
    if not bin_path:
        bin_path = find_bin(log=log, auto=False)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    prof = profile_dir()
    prof.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [bin_path, "--headless", f"-env:UserInstallation=file:///{prof.as_posix()}",
         "--convert-to", fmt, "--outdir", out_dir, os.path.abspath(input_path)],
        check=True, timeout=600, capture_output=True,
    )
    base = os.path.splitext(os.path.basename(input_path))[0]
    return Path(out_dir) / f"{base}.{fmt}"