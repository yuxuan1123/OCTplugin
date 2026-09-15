# -*- coding: utf-8 -*-
"""translation —— 翻译插件迁移后的无界面 Python 后端（自包含 stdin/stdout JSON-RPC）。

MVP 迁移仅保留「文本 → 翻译 → 结果」：通过免费的 Google 翻译公开接口
(translate.googleapis.com，无需 key) 完成翻译。屏幕取词 / 语音 / 外挂 App
等高级功能已在迁移中舍弃（无网络与 UI 无关的本地能力也一并移除）。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://translate.googleapis.com/translate_a/single"

# 常见语种：auto 表示自动检测源语种，其余为可选的「译成」目标语种
LANGS = {
    "auto": "自动检测",
    "zh": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "fr": "法语",
    "de": "德语",
    "ru": "俄语",
    "es": "西班牙语",
}

# 目标语种为 auto 时的兜底（无明确目标时默认译成中文）
DEFAULT_TO = "zh"


def do_translate(text, to):
    """调用 Google 翻译公开接口。任何异常都返回明确 error，绝不崩。"""
    if text is None or not str(text).strip():
        return {"ok": False, "error": "原文为空，请输入要翻译的文本"}
    if not to or to == "auto":
        to = DEFAULT_TO
    q = urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": to, "dt": "t", "q": str(text),
    })
    url = API_URL + "?" + q
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://translate.google.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status != 200:
                return {"ok": False, "error": "HTTP %d" % r.status}
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %d" % (e.code or 0)}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "网络错误: %s" % (e.reason or e)}
    except Exception as e:
        return {"ok": False, "error": "请求失败: %s" % e}

    try:
        data = json.loads(body)
    except ValueError as e:
        return {"ok": False, "error": "响应解析失败: %s" % e}

    try:
        segments = data[0]
        text_out = "".join(seg[0] for seg in segments if isinstance(seg, list) and seg and seg[0])
        source = data[2] if len(data) > 2 and data[2] else to
    except Exception as e:
        return {"ok": False, "error": "响应结构异常: %s" % e}

    if not text_out:
        return {"ok": False, "error": "未得到译文，请检查文本是否为有效语种"}
    return {"ok": True, "result": {"source": source, "text": text_out}}


def handle(req_id, method, params):
    if method == "ping":
        return {"pong": True}
    if method == "translation.langs":
        return {"ok": True, "result": LANGS}
    if method == "translation.translate":
        params = params or {}
        return do_translate(params.get("text"), params.get("to"))
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