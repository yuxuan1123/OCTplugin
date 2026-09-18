// ══ 翻译 · 交互逻辑 ══
// OCTools 翻译页复刻：方向/引擎 → 屏幕翻译 → 语音翻译 → 原文 → 开始 → 译文 → 日志。
// 事件经内核广播给宿主，iframe 前端收不到 → 统一用 600ms 轮询 translation.app.status 刷新 5 应用行。
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // ── 统一 RPC（客户端 + 内核 timeoutMs 都要长超时：模型冷启动/首次翻译较慢）
  const apply = async (method, params, timeoutMs = 600000) => {
    if (!window.OCT) throw new Error("内核未连接");
    const wrap = await window.OCT.rpc("plugin.call", { pluginId: "translation", method, params: params || {}, timeoutMs }, timeoutMs);
    const payload = (wrap && wrap.result) || wrap || {};
    if (payload.error) throw Object.assign(new Error(payload.error), { detail: payload });
    return payload;
  };

  // ── 原生对话框（经宿主 postMessage 中转；监听 oct:dialog:result 回执）
  function nativeDialog(action, filters) {
    return new Promise((resolve) => {
      const reqId = "dlg" + Date.now() + Math.random().toString(36).slice(2, 8);
      const onMsg = (ev) => {
        const d = ev.data || {};
        if (d.type === "oct:dialog:result" && d.reqId === reqId) {
          window.removeEventListener("message", onMsg);
          resolve(d.result || { path: "", canceled: true });
        }
      };
      window.addEventListener("message", onMsg);
      try {
        window.parent.postMessage({ type: "oct.dialog", action, reqId, filters: filters || [] }, "*");
      } catch (e) { resolve({ path: "", canceled: true, error: String(e) }); }
    });
  }

  // ── Toast ──
  let toastTimer = null;
  function toast(msg, kind = "ok") {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast"; }, 2400);
  }

  // ── 状态 ──
  let DIR_LABELS = { auto: "自动检测", zh2en: "中→英", en2zh: "英→中" };
  let ENGINE_LABELS = {};   // engine -> label
  let ENGINE_ORDER = ["hy", "opusmt"];
  let selectedEngine = "hy";
  let DIR_MODE = "auto";

  // ── ① 方向 / 引擎 ──
  async function loadDirections() {
    try {
      const res = await apply("translation.directions");
      if (!res.ok) return;
      const sel = $("dir-select");
      sel.innerHTML = "";
      (res.result.order || ["auto", "zh2en", "en2zh"]).forEach((k) => {
        DIR_LABELS[k] = (res.result.labels && res.result.labels[k]) || DIR_LABELS[k] || k;
        const o = document.createElement("option"); o.value = k; o.textContent = DIR_LABELS[k];
        sel.appendChild(o);
      });
      sel.value = DIR_MODE;
    } catch (e) { /* 免崩，保留默认 */ }
  }

  async function loadEngine() {
    try {
      const res = await apply("translation.engine.get");
      if (res.ok) {
        if (res.result.order) ENGINE_ORDER = res.result.order;
        if (res.result.labels) ENGINE_LABELS = res.result.labels;
        selectedEngine = res.result.engine || ENGINE_ORDER[0];
      }
    } catch (e) { /* 免崩 */ }
    const sel = $("engine-select");
    sel.innerHTML = "";
    ENGINE_ORDER.forEach((k) => {
      const o = document.createElement("option");
      o.value = k; o.textContent = ENGINE_LABELS[k] || k;
      sel.appendChild(o);
    });
    sel.value = selectedEngine;
    updateEngineHint();
    await loadModelsStatus();
  }

  function engineHintOf(engine) {
    if (engine === "hy") return "Hy-MT2-1.8B：本地 llama_cpp 大模型，翻译质量高、可离线，需在「模型参数」中指定 GGUF 路径。";
    if (engine === "opusmt") return "Opus-MT：CTranslate2 轻量模型，速度快、占用低，适合轻量任务，需配置模型目录。";
    return "";
  }
  function updateEngineHint() {
    $("engine-hint").textContent = engineHintOf(selectedEngine);
  }

  async function onEngineChange() {
    const eng = $("engine-select").value;
    const prev = selectedEngine;
    selectedEngine = eng;
    updateEngineHint();
    try {
      const res = await apply("translation.engine.set", { engine: eng });
      if (!res.ok || (res.result && res.result.engine) === undefined) {
        // 引擎写回失败则回退选择
        selectedEngine = prev; $("engine-select").value = prev; updateEngineHint();
        toast("切换引擎失败：" + (res.error || "未知错误"), "warn");
      } else {
        toast("翻译引擎：已切换为 " + (ENGINE_LABELS[eng] || eng), "ok");
      }
    } catch (e) {
      selectedEngine = prev; $("engine-select").value = prev; updateEngineHint();
      toast("切换引擎失败：" + (e.message || e), "warn");
    }
    await loadModelsStatus();
  }

  // ── 模型状态 → 缺模型警告条 ──
  async function loadModelsStatus() {
    try {
      const res = await apply("translation.models.status");
      const bar = $("model-warn");
      if (!res.ok || !res.result) { bar.style.display = "none"; return; }
      const r = res.result;
      let warnings = r.warnings || [];
      // 按当前引擎突出最相关缺模型提示
      if ((selectedEngine === "hy") && !r.hy_ready) {
        warnings = ["Hy-MT2-1.8B 模型未配置（请到「模型参数」指定 GGUF 路径后使用）。"];
      } else if ((selectedEngine === "opusmt") && !r.opus_ready) {
        warnings = ["Opus-MT 模型目录未配置（请到「模型参数」指定目录后使用）。"];
      }
      if (warnings.length) {
        $("model-warn-text").textContent = warnings.join(" ");
        bar.style.display = "block";
      } else {
        bar.style.display = "none";
      }
    } catch (e) { /* 免崩，隐藏 */ $("model-warn").style.display = "none"; }
  }

  // ── ②③ 应用行渲染（屏幕翻译 / 语音翻译 / 屏幕字幕 / 语音翻译）┐
  const APP_ICON_GLYPH = {
    scan: "⌕", bolt: "⚡", bulb: "☼", microphone: "◉", music: "♪",
  };

  // 卡片只构建一次，之后按 key 原位刷新，避免 600ms 轮询反复重建导致闪烁。
  const CARD_KEYS = {};  // containerId -> [key,...]
  function buildAppCard(containerId, keys) {
    const box = $(containerId);
    if (!box || box.hasAttribute("data-built")) return;
    box.setAttribute("data-built", "1");
    CARD_KEYS[containerId] = keys.slice();
    keys.forEach((key) => {
      const row = document.createElement("div"); row.className = "app-row"; row.dataset.key = key;
      const ic = document.createElement("div"); ic.className = "app-ic"; ic.id = "ic-" + key;
      const body = document.createElement("div"); body.className = "app-body";
      const name = document.createElement("div"); name.className = "app-name"; name.id = "name-" + key;
      const hint = document.createElement("div"); hint.className = "app-hint"; hint.id = "hint-" + key;
      body.appendChild(name); body.appendChild(hint);
      const side = document.createElement("div"); side.className = "app-side";
      const dot = document.createElement("span"); dot.className = "status-dot"; dot.id = "dot-" + key;
      const st = document.createElement("span"); st.className = "st"; st.id = "st-" + key;
      const btn = document.createElement("button"); btn.className = "btn mini"; btn.id = "go-" + key;
      btn.onclick = () => toggleApp(key, btn);
      side.appendChild(dot); side.appendChild(st); side.appendChild(btn);
      row.appendChild(ic); row.appendChild(body); row.appendChild(side);
      box.appendChild(row);
    });
  }

  function applyRowState(key, running) {
    const dot = $("dot-" + key), st = $("st-" + key), btn = $("go-" + key);
    if (!dot || !st || !btn) return;
    dot.classList.toggle("on", running);
    st.textContent = running ? "运行中" : "未启动";
    btn.textContent = running ? "停止" : "启动";
  }

  function refreshAppCard(containerId, keys, states) {
    buildAppCard(containerId, keys || []);
    (CARD_KEYS[containerId] || []).forEach((key) => {
      const state = (states || []).find((a) => a.key === key) || {};
      const ic = $("ic-" + key), nm = $("name-" + key), hn = $("hint-" + key);
      if (ic) ic.textContent = APP_ICON_GLYPH[state.icon] || APP_ICON_GLYPH[key] || "•";
      if (nm) nm.textContent = state.label || key;
      if (hn) hn.textContent = state.hint || "";
      applyRowState(key, !!state.running);
    });
  }

  // ── 启动 / 停止 ──
  async function toggleApp(key, btn) {
    const wasRunning = !!($("dot-" + key) && $("dot-" + key).classList.contains("on"));
    btn.disabled = true;
    const method = wasRunning ? "translation.app.stop" : "translation.app.start";
    try {
      const res = await apply(method, { name: key }, 120000);
      if (res.ok) { toast(wasRunning ? "已停止" : "已启动", "ok"); }
      else toast((wasRunning ? "停止失败：" : "启动失败：") + (res.error || "未知错误"), "warn");
    } catch (e) {
      toast((wasRunning ? "停止失败：" : "启动失败：") + (e.message || e), "warn");
    } finally {
      btn.disabled = false;
      await pollStatus(); // 立即刷新以同步后端真实状态
    }
  }

  // ── 600ms 轮询 translation.app.status（事件 frame 收不到，靠问卷兜底）┐
  let screens = [], voices = [];
  async function pollStatus() {
    try {
      const res = await apply("translation.app.status");
      if (!res.ok) return;
      const r = res.result || {};
      const states = r.apps || [];
      if (!screens.length && (r.screen || []).length) screens = r.screen.slice();
      if (!voices.length && (r.voice || []).length) voices = r.voice.slice();
      refreshAppCard("screen-apps", screens, states);
      refreshAppCard("voice-apps", voices, states);
    } catch (e) { /* 内核暂未就绪时静默跳过，保持上一帧 */ }
  }

  // ── ④ 原文 / 翻译 ──
  function readSource() { return $("src").value || ""; }

  async function doTranslate() {
    const text = readSource().trim();
    if (!text) { toast("请输入要翻译的文本", "warn"); $("src").focus(); return; }
    const btn = $("translate-btn");
    btn.disabled = true; const old = btn.textContent; btn.textContent = "翻译中…";
    try {
      const res = await apply("translation.translate", { text, direction: $("dir-select").value });
      if (!res.ok) {
        $("res").value = "";
        $("res-muted").textContent = "翻译失败";
        toast("翻译失败：" + (res.error || "请检查模型配置"), "warn");
        return;
      }
      $("res").value = res.result.text || "";
      const d = res.result.direction || DIR_MODE;
      $("src-tag").innerHTML = '<span class="dot"></span>' + esc(DIR_LABELS[d] || d);
      $("res-muted").textContent = "翻译完成";
      $("log-state").textContent = "✔ 翻译完成";
      toast("翻译完成");
    } catch (e) {
      $("res-muted").textContent = "翻译出错";
      toast("翻译出错：" + (e.message || e), "warn");
    } finally {
      btn.disabled = false; btn.textContent = old;
    }
  }

  function clearSource() { $("src").value = ""; $("res").value = ""; $("src-tag").innerHTML = '<span class="dot"></span>自动检测'; $("res-muted").textContent = "翻译结果将显示在这里…"; }

  // ── 载入文件 / 保存译文 ──
  async function loadFile() {
    const r = await nativeDialog("open", [{ name: "文本文件", extensions: ["txt", "md", "text", "log", "*"] }]);
    if (!r || r.canceled || !r.path) { toast("已取消载入", "ok"); return; }
    try {
      const wrap = await window.OCT.rpc("gate.file_read", { path: r.path }, 15000);
      const payload = (wrap && wrap.result) || wrap || {};
      const content = payload.content ?? payload.text ?? (typeof payload === "string" ? payload : "");
      if (content === "") throw new Error("未读取到文本内容（可能缺少文件读取授权）");
      $("src").value = content;
      toast("已载入文件", "ok");
    } catch (e) {
      toast("载入失败：" + (e.message || e) + "；请手动粘贴文件内容", "warn");
    }
  }

  async function saveText() {
    const text = ($("res").value || "").trim();
    if (!text) { toast("译文为空，无可保存", "warn"); return; }
    const r = await nativeDialog("save", [{ name: "文本文件", extensions: ["txt"] }]);
    if (!r || r.canceled || !r.path) { toast("已取消保存", "ok"); return; }
    const target = String(r.path).replace(/[\\/]+$/, "") + "\\翻译结果.txt";
    try {
      const wrap = await window.OCT.rpc("gate.file_write", { path: target, content: text }, 15000);
      const payload = wrap && wrap.result;
      if (payload && payload.error) throw new Error(payload.error);
      toast("译文已保存", "ok");
    } catch (e) {
      toast("保存失败：" + (e.message || e), "warn");
    }
  }

  function copyResult() {
    const text = $("res").value || "";
    if (!text) { toast("译文为空", "warn"); return; }
    const done = () => toast("译文已复制", "ok");
    const exec = () => {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch { toast("复制失败", "warn"); }
      ta.remove();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done).catch(exec);
    else exec();
  }

  // ── ⑦ 日志 ──
  let logLen = 0; let lastLogState = "";
  function appendLogs(lines) {
    const box = $("log"); if (!box) return;
    lines.forEach((msg) => {
      const div = document.createElement("div");
      div.className = "ln";
      div.textContent = msg;
      if (/✘|错误|失败|❌|异常/i.test(String(msg))) div.classList.add("err");
      box.appendChild(div);
    });
    box.scrollTop = box.scrollHeight;
  }
  async function pollLogs() {
    try {
      const res = await apply("translation.logs");
      if (!res.ok) return;
      const logs = (res.result && res.result.logs) || [];
      if (logs.length < logLen) { $("log").innerHTML = ""; logLen = 0; } // 已清空
      if (logs.length > logLen) {
        appendLogs(logs.slice(logLen));
        logLen = logs.length;
      }
      const st = $("log-state");
      const last = logs[logs.length - 1];
      if (last && last !== lastLogState) { lastLogState = last; st.textContent = last; }
    } catch (e) { /* 免崩 */ }
  }
  async function clearLogs() {
    try {
      const res = await apply("translation.logs.clear");
      if (!res.ok) return;
      $("log").innerHTML = ""; logLen = 0; lastLogState = "";
      $("log-state").textContent = "等待翻译…";
      toast("日志已清空", "ok");
    } catch (e) { toast("清空失败：" + (e.message || e), "warn"); }
  }

  // ── 打开「模型参数」新窗（settings.html 经 window.open 自连内核）┐
  function openSettings() {
    const q = new URLSearchParams();
    q.set("standalone", "1");
    if (window.OCT && window.OCT.port) { q.set("kport", window.OCT.port); q.set("auth", window.OCT.token || ""); }
    window.open("settings.html?" + q.toString(), "_blank", "width=780,height=680,resizable=yes,scrollbars=yes");
  }

  // ── 事件绑定 ──
  function bind() {
    $("dir-select").addEventListener("change", () => { DIR_MODE = $("dir-select").value; });
    $("engine-select").addEventListener("change", onEngineChange);
    $("params-btn").onclick = openSettings;
    $("translate-btn").onclick = doTranslate;
    $("clear-btn").onclick = clearSource;
    $("load-btn").onclick = loadFile;
    $("copy-btn").onclick = copyResult;
    $("save-btn").onclick = saveText;
    $("log-clear").onclick = clearLogs;
    $("src").addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "Enter")) { e.preventDefault(); doTranslate(); }
    });
  }

  // 统一等 WS 真连（oct.ready）再初始化，避免提前调用后端失败。
  window.addEventListener("oct.ready", async () => {
    bind();
    loadDirections();
    await loadEngine();
    pollStatus();
    pollLogs();
    // iframe 前端收不到本插件事件 → 600ms 轮询兜底（即使收到事件也轮询）
    setInterval(pollStatus, 600);
    setInterval(pollLogs, 1500);
  });
})();