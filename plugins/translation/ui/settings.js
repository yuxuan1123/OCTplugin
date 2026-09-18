// ══ 模型参数 · settings.js ══
// 独立窗口「模型参数…」（app.js 经 ?standalone=1&kport=&auth= 自连内核）。
// 引擎下拉选中即写 engine.set；保存回写 translation.config.set + stt.config.set。
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const apply = async (method, params, timeoutMs = 60000) => {
    if (!window.OCT) throw new Error("内核未连接");
    const wrap = await window.OCT.rpc("plugin.call", { pluginId: "translation", method, params: params || {}, timeoutMs }, timeoutMs);
    const payload = (wrap && wrap.result) || wrap || {};
    if (payload.error) throw Object.assign(new Error(payload.error), { detail: payload });
    return payload;
  };

  let toastTimer = null;
  function toast(msg, kind) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = "toast"), 2400);
  }

  let cfg = {};        // 当前翻译配置（translation.config.get）
  let stt = {};        // 当前语音配置（translation.stt.config.get）
  let engine = "hy";   // 当前引擎

  // 表单字段 → 配置键（翻译配置部分）
  const TR_FIELDS = {
    preload_models: "bool", preload_timing: "str",
    overlay_font_size: "int", overlay_mode: "str", overlay_bg_color: "str",
    overlay_alpha: "float", overlay_pin_default: "bool",
    hy_model_path: "str", hy_n_ctx: "int", hy_n_threads: "int", hy_n_gpu_layers: "int",
    hy_max_tokens: "int", hy_temperature: "float", hy_top_p: "float",
    hy_top_k: "int", hy_repeat_penalty: "float",
    opusmt_base: "str",
  };
  // 表单字段 → 语音配置键
  const STT_FIELDS = {
    stt_model_dir: ["stt", "model_dir", "str"],
    stt_device: ["stt", "device", "str"],
    stt_language: ["stt", "language", "str"],
    stt_sherpa_num_threads: ["stt", "sherpa_num_threads", "int"],
  };

  const num = (v, d) => { const x = parseFloat(v); return isFinite(x) ? x : d; };

  function setField(id, val, type) {
    const el = $(id); if (!el) return;
    if (type === "bool") el.checked = !!val;
    else el.value = String(val ?? "");
  }
  function readField(id, type) {
    const el = $(id); if (!el) return undefined;
    if (type === "bool") return el.checked;
    if (type === "int") return Math.round(num(el.value, 0));
    if (type === "float") return num(el.value, 0);
    return el.value;
  }

  function applyCfg() {
    for (const k in TR_FIELDS) setField(k, cfg[k], TR_FIELDS[k]);
    for (const k in STT_FIELDS) {
      const [, key, type] = STT_FIELDS[k];
      setField(k, stt[key], type);
    }
    $("engine-select").value = engine;
    toggleGroups();
  }

  function toggleGroups() {
    const eng = $("engine-select").value;
    $("hy-group").style.display = eng === "hy" ? "" : "none";
    $("opus-group").style.display = eng === "opusmt" ? "" : "none";
  }

  async function loadAll() {
    try {
      const [c, s, e] = await Promise.all([
        apply("translation.config.get"),
        apply("translation.stt.config.get"),
        apply("translation.engine.get"),
      ]);
      cfg = (c.result) || {};
      stt = (s.result) || {};
      if (e.ok && e.result) engine = e.result.engine || engine;
      applyCfg();
    } catch (err) {
      toast("加载失败：" + (err.message || err), "warn");
    }
  }

  async function onEngineChange() {
    const eng = $("engine-select").value;
    toggleGroups();
    try {
      const res = await apply("translation.engine.set", { engine: eng });
      if (res.ok) { engine = eng; toast("已切换引擎", "ok"); }
      else toast("切换失败：" + (res.error || ""), "warn");
    } catch (e) { toast("切换失败：" + (e.message || e), "warn"); }
  }

  function collectTR() {
    const out = {};
    for (const k in TR_FIELDS) out[k] = readField(k, TR_FIELDS[k]);
    out.engine = $("engine-select").value; // 一并回写当前选中引擎
    return out;
  }
  function collectSTT() {
    const out = {};
    for (const k in STT_FIELDS) { const [, key, type] = STT_FIELDS[k]; out[key] = readField(k, type); }
    return out;
  }

  async function save() {
    const saveBtn = $("save-btn");
    saveBtn.disabled = true; const old = saveBtn.textContent; saveBtn.textContent = "保存中…";
    try {
      await apply("translation.config.set", { config: collectTR() });
      await apply("translation.stt.config.set", { config: collectSTT() });
      toast("已保存，配置生效", "ok");
    } catch (e) {
      toast("保存失败：" + (e.message || e), "warn");
    } finally {
      saveBtn.disabled = false; saveBtn.textContent = old;
    }
  }

  function restoreDefaults() {
    applyCfg(); // 恢复为后端当前已保存（含默认值）的配置
    toggleGroups();
    toast("已恢复为默认配置（未保存状态）", "ok");
  }

  window.addEventListener("oct.ready", () => {
    $("engine-select").addEventListener("change", onEngineChange);
    $("save-btn").onclick = save;
    $("default-btn").onclick = restoreDefaults;
    loadAll();
  });
})();