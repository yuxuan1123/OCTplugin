# -*- coding: utf-8 -*-
"""home —— 从 OCTools 迁移的无界面后端（严禁 import PySide6）。

stdin/stdout JSON-RPC：
- 打卡：am/pm 状态判定 + 持久化（home_state.json）
- 任务：增/勾选/删，持久化同文件
- 工作模式：静默启动 home.bat
- 网络加速：探测状态 + 切换 net.ps1（PowerShell 静默执行）
前端（iframe）经内核 plugin.call 调本插件。
"""
import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "home_state.json")
BAT_FILE = os.path.join(HERE, "home.bat")
NET_PS1 = os.path.join(HERE, "net.ps1")
NET_STATIC_IP = "172.17.174.5"

AM_DEADLINE = 9
AM_FIX_MIN = 9 * 60 + 5
PM_START = 18

DEFAULT_TASKS = [
    {"id": 1, "text": "每日站会", "done": False},
    {"id": 2, "text": "代码 Review", "done": False},
    {"id": 3, "text": "提交工时", "done": False},
]

_NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# ── 状态持久化 ────────────────────────
def _load():
    data = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    tasks = data.get("tasks") or [dict(t) for t in DEFAULT_TASKS]
    max_id = max((t["id"] for t in tasks), default=0)
    return {
        "tasks": tasks,
        "next_id": data.get("next_id") or max_id + 1,
        "am_date": data.get("am_date", ""),
        "am_time": data.get("am_time", ""),
        "pm_date": data.get("pm_date", ""),
        "pm_time": data.get("pm_time", ""),
    }


def _save(st):
    data = {**st, "tasks": st["tasks"]}
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _today():
    return date.today().isoformat()


# ── 打卡（单按钮·按时段自动判别）────────
def clockin_state():
    """返回 {slot, done, hint}：
    slot 'am'/'pm'/'none'：此刻应打卡的时段
    done 该时段今日是否已打（记录日期非今日即视为未打，昨日记录自动作废）
    hint 一行说明
    """
    st = _load()
    today = _today()
    now = datetime.now()
    m = now.hour * 60 + now.minute
    secs = now.hour * 3600 + now.minute * 60 + now.second

    if m < AM_FIX_MIN:  # 09:05 前：上午时段（含补卡）
        slot, done = "am", (st.get("am_date") == today)
        if done:
            hint = "今日上午已打卡 · " + (st.get("am_time", "")[11:19] or "—")
        else:
            rem = AM_DEADLINE * 3600 - secs
            if rem >= 0:
                hint = "上午待打卡 · 截止 09:00（剩 {:02d}:{:02d}）".format(rem // 3600, rem % 3600 // 60)
            else:
                over = int(-rem)
                hint = "请补卡 · 已超时 {:02d}分{:02d}秒".format(over // 60, over % 60)
        return {"slot": slot, "done": done, "hint": hint}

    if m >= PM_START * 60:  # 18:00 后：下午时段
        slot, done = "pm", (st.get("pm_date") == today)
        hint = ("今日下午已打卡 · " + (st.get("pm_time", "")[11:19] or "—")) if done else "下午待打卡 · 18:00 后进入下午时段"
        return {"slot": slot, "done": done, "hint": hint}

    # 中间时段：无可打卡动作
    done_am = st.get("am_date") == today
    hint = ("今日上午已完成 · 等待 18:00 后打下午卡" if done_am else "上午时段已过 · 等待 18:00 后打下午卡")
    return {"slot": "none", "done": False, "hint": hint}


def punch(slot):
    """写入 slot 时段的今日打卡时间，返回 ISO 串。"""
    st = _load()
    iso = datetime.now().isoformat(timespec="seconds")
    today = _today()
    if slot == "am":
        st["am_date"], st["am_time"] = today, iso
    else:
        st["pm_date"], st["pm_time"] = today, iso
    _save(st)
    return iso


# ── 任务 ──────────────────────────────
def task_add(text):
    text = (text or "").strip()
    if not text:
        return None
    st = _load()
    t = {"id": st["next_id"], "text": text, "done": False}
    st["tasks"].append(t)
    st["next_id"] += 1
    _save(st)
    return t


def task_toggle(tid, done):
    st = _load()
    for t in st["tasks"]:
        if t["id"] == int(tid):
            t["done"] = done
            break
    _save(st)


def task_remove(tid):
    st = _load()
    st["tasks"] = [t for t in st["tasks"] if t["id"] != int(tid)]
    _save(st)


# ── 工作模式 ──────────────────────────
def workmode():
    if not os.path.exists(BAT_FILE):
        return {"ok": False, "error": "home.bat 不存在"}
    try:
        subprocess.Popen(["cmd", "/c", BAT_FILE], cwd=HERE,
                         creationflags=_NOWIN, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 网络加速 ──────────────────────────
_PROBE = f"@(Get-NetIPAddress -AddressFamily IPv4 -IPAddress {NET_STATIC_IP} -ErrorAction SilentlyContinue).Count"


def net_status():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", _PROBE],
                           capture_output=True, text=True, timeout=6, creationflags=_NOWIN)
        s = r.stdout.strip()
        return "on" if s and s != "0" else "off"
    except Exception:
        return "unknown"


def net_toggle(turn_on):
    if not os.path.exists(NET_PS1):
        return {"ok": False, "error": "net.ps1 不存在"}
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-NonInteractive",
                          "-ExecutionPolicy", "Bypass", "-File", NET_PS1, "-Action", "on" if turn_on else "off"],
                         cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_NOWIN)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── RPC 分发 ──────────────────────────
def handle(req_id, method, params):
    params = params or {}
    if method == "state":
        st = _load()
        today = _today()
        last = (st.get("pm_time") if st.get("pm_date") == today
                else st.get("am_time") if st.get("am_date") == today else "")
        return {"clockin": clockin_state(), "tasks": st["tasks"],
                "today": today, "net": net_status(),
                "am_done": st.get("am_date") == today,
                "pm_done": st.get("pm_date") == today,
                "am_time": st.get("am_time", ""),
                "pm_time": st.get("pm_time", ""),
                "last_iso": last}
    if method == "clockin":
        return {"slot": params.get("slot", "am"), "time": punch(params.get("slot", "am"))}
    if method == "task.add":
        return {"task": task_add(params.get("text", ""))}
    if method == "task.toggle":
        task_toggle(params.get("id"), params.get("done", False))
        return {"ok": True}
    if method == "task.remove":
        task_remove(params.get("id"))
        return {"ok": True}
    if method == "workmode":
        return workmode()
    if method == "net.status":
        return {"state": net_status()}
    if method == "net.toggle":
        return net_toggle(bool(params.get("on")))
    if method == "ping":
        return {"pong": True}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            sys.stdout.write(json.dumps({"v": 1, "jsonrpc": "2.0", "id": 0,
                                         "error": {"code": -32700, "message": str(e)}}) + "\n")
            sys.stdout.flush()
            continue
        req_id = req.get("id", 0)
        result = handle(req_id, req.get("method"), req.get("params"))
        out = {"v": 1, "jsonrpc": "2.0", "id": req_id}
        if result is None:
            out["error"] = {"code": -32601, "message": "method not found"}
        else:
            out["result"] = result
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()