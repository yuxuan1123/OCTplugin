"""
translation/apps/app_controller.py
──────────────────────────────────
最终应用控制：创建应用实例、启动/停止/切换、状态查询、日志追加（1:1 复刻
OCTools app_controller，去掉 Qt objectName/icon 重抛光，改为返回稳定 JSON
状态供前端渲染；悬浮窗被关闭时自动复位对应行）。

启动前不做主窗口最小化（截图像类是置顶原生悬浮窗 + 全屏框选覆盖层，宿主
iframe 本身不构成遮挡；语音类同样）。
"""

from registry import _APP_ROWS, _SCREEN_APP_CARD, _VOICE_APP_CARD


def init_apps(host):
    """创建最终应用实例；悬浮窗被关闭（stopped）时自动复位行状态"""
    apps = {}
    for key, (cls, *_rest) in _APP_ROWS.items():
        app = cls(host)
        app.set_stopped_callback(lambda _k, hst=host: set_app_running(hst, _k))
        apps[key] = app
    host._apps = apps
    return apps


def app(host, key):
    """按 key 获取最终应用实例"""
    return (host._apps or {}).get(key)


def is_app_running(host, key) -> bool:
    a = app(host, key)
    return bool(a is not None and a.is_running())


def toggle_app(host, key):
    a = app(host, key)
    if a is None:
        return
    a.toggle()


def start_app(host, key):
    a = app(host, key)
    if a is None:
        return
    label = _APP_ROWS[key][1]
    try:
        a.start()
    except Exception as e:
        host.log(f"❌ {label} 启动失败: {e}")
        set_app_running(host, key)
        return
    set_app_running(host, key)


def stop_app(host, key):
    a = app(host, key)
    if a is None:
        return
    try:
        a.stop()
    except Exception:
        pass
    set_app_running(host, key)


def stop_all_apps(host):
    """退出前停止全部最终应用（幂等）"""
    for key in list((host._apps or {})):
        try:
            host._apps[key].stop()
        except Exception:
            pass


def app_states(host):
    """返回全部应用状态表（供 translation.app.status 查询）"""
    rows = []
    apps = getattr(host, "_apps", None) or {}
    for key, (cls, label, icon, hint, _hk) in _APP_ROWS.items():
        a = apps.get(key)
        rows.append({
            "key": key, "label": label, "icon": icon, "hint": hint,
            "running": bool(a is not None and a.is_running()),
        })
    return rows


def set_app_running(host, key, running=None):
    """推送应用运行状态给前端（running 缺省按实际查询）。
    悬浮窗被关闭时自动复位为 False。"""
    if running is None:
        running = is_app_running(host, key)
    if host._event_pump is not None:
        try:
            host._event_pump("app.state", {"key": key, "running": bool(running)})
        except Exception:
            pass


def append_log(host, msg):
    """追加一行日志（经事件泵推给前端）"""
    if host._event_pump is not None:
        try:
            host._event_pump("log_line", {"key": "log", "message": msg})
        except Exception:
            pass