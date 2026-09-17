// ══ 拼接 · 交互逻辑 ══
// 1:1 复刻 OCTools 拼接页：
//   标题 → 输入(源文件/源文件夹二选一) → 目标格式(二级选择器+可达性联动)
//   → 预设区 → 输出 → 开始拼接(单文件走 conversion 直连 / 多文件走 merge.concat) → 日志。
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // 调用本插件（merge）后端：必须经 plugin.call（否则内核 dispatch 不认 raw 方法名，
  // 报 ErrMethodNotFound → 「刷新可达目标失败」；且 plugin.call 自带 GetOrStart 懒启动懒加载进程）。
  // plugin.call 返回 {ok, result}，解出内层 handler 载荷 {ok, ...}。长超时给合并预留。
  const rpc = (method, params, timeout = 600000) => {
    if (!window.OCT) throw new Error("内核未连接");
    return window.OCT.rpc("plugin.call",
      { pluginId: "merge", method, params: Object.assign({ timeoutMs: timeout }, params || {}) },
      timeout).then((r) => r && r.result);
  };
  // 跨插件：conversion 直连（单文件转换一律走这条，传大超时，避免 kokoro 慢路径超时）
  const callConversion = (params) =>
    window.OCT.callPlugin("conversion", "conversion.convert",
      Object.assign({ timeoutMs: 600000 }, params || {}));

  let CATS = [];            // 分类：{key,name,formats:{values,labels,icons}}
  let ALL_SOURCE = [];      // 可作为源的格式 id
  let srcFmt = "";          // 当前源格式（文件夹模式手动指定 / 单文件模式由扩展名推断）
  let dstFmt = "";          // 当前目标格式
  let reachable = [];       // 当前源的可达目标（扁平数组，未选 src 时空）
  let busy = false;
  let outManual = false;    // 输出路径是否由用户手动填写（手动则不再自动重算）
  let toastTimer = null;
  function toast(msg, kind = "ok") {
    const t = $("toast"); t.textContent = msg;
    t.className = "toast show " + kind;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = "toast"; }, 2600);
  }

  // ── 原生对话框（经宿主 postMessage 中转） ──
  function nativeDialog(action) {
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
        window.parent.postMessage({ type: "oct.dialog", action, reqId }, "*");
      } catch (e) { resolve({ path: "", canceled: true, error: String(e) }); }
    });
  }

  // ── 集合（按后端 categories 动态取，另有硬编码兜底） ──
  let AUD = (f) => ["mp3", "aac", "wav", "flac", "ogg", "opus", "wma", "m4a", "amr", "ac3", "aiff"].indexOf(f) >= 0;
  let IMG = (f) => ["jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "heic", "svg"].indexOf(f) >= 0;

  // ── 加载格式元数据 ──
  async function loadFormats() {
    try {
      const res = await rpc("merge.formats", { src: srcFmt });
      if (!res || !res.ok) { toast("加载格式失败", "warn"); return; }
      CATS = res.categories || [];
      ALL_SOURCE = res.source_formats || [];
      // 从 categories 提取音频 / 图片集合
      const aud = CATS.find((c) => c.key === "aud");
      if (aud) AUD = (f) => (aud.formats && aud.formats.values).indexOf(f) >= 0;
      const img = CATS.find((c) => c.key === "img");
      if (img) IMG = (f) => (img.formats && img.formats.values).indexOf(f) >= 0;
      if (res.reachable && res.reachable.length) reachable = res.reachable;
      // 源格式下拉
      const list = $("src-fmt-list"); list.innerHTML = "";
      ALL_SOURCE.forEach((f) => { const o = document.createElement("option"); o.value = f; list.appendChild(o); });
      buildPicker();
      renderChips();
    } catch (e) { toast("加载格式失败：" + (e.message || e), "warn"); }
  }

  const chipsOf = (cat) => (cat && cat.formats && cat.formats.values) || [];

  // ── 目标格式两级选择器（一级分类按钮 + 二级芯片） ──
  function buildPicker() {
    const box = $("dst-picker");
    box.innerHTML = "";
    const catsRow = document.createElement("div"); catsRow.className = "cats";
    const chipsBox = document.createElement("div"); chipsBox.className = "chips";
    box.appendChild(catsRow); box.appendChild(chipsBox);

    CATS.forEach((cat, ci) => {
      const b = document.createElement("button");
      b.className = "cat-btn" + (ci === 0 ? " active" : "");
      b.textContent = cat.name;
      b.dataset.cat = cat.key;
      b.onclick = () => {
        catsRow.querySelectorAll(".cat-btn").forEach((x) => x.classList.remove("active"));
        b.classList.add("active"); renderChips();
      };
      catsRow.appendChild(b);
    });
  }

  const activeCat = () => {
    const box = $("dst-picker");
    const act = box && box.querySelector(".cat-btn.active");
    return CATS.find((c) => c.key === (act && act.dataset.cat)) || CATS[0] || null;
  };

  function canReach(fmt) {
    if (!srcFmt) return true;
    return reachable.indexOf(fmt) >= 0;
  }

  function renderChips() {
    if (!CATS.length) return;
    const cat = activeCat();
    const chipsBox = $("dst-picker").querySelector(".chips");
    chipsBox.innerHTML = "";
    if (!cat) return;
    const labels = (cat.formats && cat.formats.labels) || [];
    chipsOf(cat).forEach((fmt, i) => {
      const c = document.createElement("button");
      c.className = "chip";
      c.dataset.fmt = fmt;
      c.textContent = labels[i] || fmt.toUpperCase();
      if (dstFmt === fmt) c.classList.add("active");
      c.disabled = !canReach(fmt);
      if (c.disabled && dstFmt === fmt) dstFmt = "";
      c.onclick = () => selectDst(fmt);
      chipsBox.appendChild(c);
    });
    const hint = $("dst-hint");
    const hasAny = chipsOf(cat).length > 0;
    const anyInCat = chipsOf(cat).some((f) => canReach(f));
    hint.style.display = (hasAny && !anyInCat) ? "block" : "none";
    if (hasAny && !anyInCat) hint.textContent = "当前源没有可选的目标格式";
  }

  // ── 目标格式选择 ──
  function selectDst(fmt) {
    dstFmt = fmt;
    $("dst-picker").querySelectorAll(".chip").forEach((x) =>
      x.classList.toggle("active", !x.disabled && x.dataset.fmt === fmt));
    updatePresetArea();
    autoOutputName();
  }

  const DST_EXT = (f) => ({ "txt-ocr": "txt", "txt_ocr": "txt", "pptx-img": "pptx" }[f] || f);

  // ── 刷新目标可达性（带 src 请求 merge.formats） ──
  async function refreshDst() {
    try {
      const res = await rpc("merge.formats", { src: srcFmt });
      if (!res || !res.ok) return;
      reachable = res.reachable || [];
      renderChips();
    } catch (e) { toast("刷新可达目标失败", "warn"); }
  }

  // ── 源格式变更 ──
  function applySrc(fmt) {
    srcFmt = fmt.trim().toLowerCase();
    updatePresetArea();
    refreshDst();
    autoOutputName();
  }

  // ═════════════════════════════════════════
  //  四类参数配置（初始来自后端 defaults，弹窗/预设编辑后存回）
  // ═════════════════════════════════════════
  const cfg = { docx: null, tts: null, stt: null, img: null };
  const CONF_KINDS = ["docx", "tts", "stt", "img"];

  function fallbackCfg(kind) {
    switch (kind) {
      case "docx": return {
        page: { paper_size: "A4", orientation: "portrait", margin_top: 2.54, margin_bottom: 2.54,
          margin_left: 3.18, margin_right: 3.18, gutter: 0.0 },
        typography: { body_font_cn: "宋体", body_font_en: "Times New Roman", body_font_size: 12,
          headings: { h1: { font_size: 22 }, h2: { font_size: 18 }, h3: { font_size: 14 } },
          line_spacing_mode: "multiple", line_spacing: 1.5, first_line_indent_chars: 2, alignment: "left" },
        content: { code_font: "Consolas" },
        advanced: { heading_numbering: false, heading_numbering_format: "1.", auto_toc: false,
          toc_depth: 3, header_text: "", footer_text: "" },
      };
      case "tts": return { engine: "kokoro", edge_voice: "zh-CN-XiaoxiaoNeural", edge_rate: "+0%",
        edge_volume: "+0%", edge_pitch: "+0Hz", kokoro_lang: "z", kokoro_voice: "zf_xiaoxiao",
        kokoro_speed: 1 };
      case "stt": return { language: "auto", device: "cpu", use_itn: true };
      case "img": return { images_per_row: 2 };
      default: return {};
    }
  }

  // ── 预设持久化：并行拉取四类 list（拿 defaults） ──
  const presetState = { docx: { presets: [], defaults: null },
    tts: { presets: [], defaults: null }, stt: { presets: [], defaults: null },
    img: { presets: [], defaults: null } };

  async function loadAllPresets() {
    await Promise.all(CONF_KINDS.map(async (kind) => {
      try {
        const res = await rpc("merge.presets", { kind, action: "list" }, 15000);
        if (res && res.ok) {
          presetState[kind].presets = res.presets || [];
          presetState[kind].defaults = res.defaults || null;
          cfg[kind] = res.defaults || fallbackCfg(kind);
        }
      } catch (e) { toast("加载预设失败", "warn"); }
    }));
  }

  async function loadPreset(kind, name) {
    try {
      const res = await rpc("merge.presets", { kind, action: "load", name });
      if (res && res.ok && res.config) { cfg[kind] = res.config; toast("已应用预设「" + name + "」"); }
      else toast("加载预设失败：" + ((res && res.error) || "未知"), "warn");
    } catch (e) { toast("加载预设失败", "warn"); }
  }
  async function savePreset(kind, name) {
    try {
      const res = await rpc("merge.presets", { kind, action: "save", name, config: cfg[kind] }, 15000);
      if (res && res.ok) { presetState[kind].presets = res.presets || []; toast("预设「" + name + "」已保存"); }
      else toast("保存预设失败：" + ((res && res.error) || "未知"), "warn");
    } catch (e) { toast("保存预设失败", "warn"); }
  }

  function buildPresetOptions(kind) {
    let o = '<option value="">默认</option>';
    (presetState[kind].presets || []).forEach((p) => { o += '<option value="' + esc(p) + '">' + esc(p) + "</option>"; });
    o += '<option value="__save">保存预设…</option>';
    return o;
  }

  // ═════════════════════════════════════════
  //  预设区：按 (源,目标) 决定展示哪一行
  // ═════════════════════════════════════════
  const PTITLE = { tts: "语音引擎", docx: "MD → DOCX 排版", img: "图片预设", stt: "语音识别" };

  function presetMode() {
    const s = srcFmt, d = dstFmt;
    if ((s === "txt" || s === "md") && (!d || AUD(d))) return "tts";
    if (s === "md" && d === "docx") return "docx";
    if (IMG(s) && d === "docx") return "img";
    if (AUD(s) && d === "txt") return "stt";
    return "";
  }

  function summaryFor(kind) {
    const c = cfg[kind] || {};
    if (kind === "docx") return `${(c.page && c.page.paper_size) || "A4"} ${
      (c.page && c.page.orientation) === "landscape" ? "横向" : "竖向"} · ${
      (c.typography && c.typography.body_font_cn) || "宋体"} ${
      (c.typography && c.typography.body_font_size) || 12} 磅 · ${
      (c.typography && c.typography.line_spacing) || 1.5} 倍行距`;
    if (kind === "tts") {
      const eng = c.engine === "edge" ? "Edge-TTS（在线）" : c.engine === "kokoro" ? "Kokoro（默认）" : (c.engine || "Kokoro");
      const v = c.engine === "edge" ? ` · ${c.edge_voice}` : c.engine === "kokoro" ? ` · ${c.kokoro_voice}` : "";
      return eng + v;
    }
    if (kind === "stt") {
      const lng = { auto: "自动", zh: "中文", en: "英文", yue: "粤语", ja: "日语", ko: "韩语" };
      return `${lng[c.language] || c.language || "自动"} · ${c.device === "cuda" ? "GPU" : "CPU"}`;
    }
    if (kind === "img") return `每行 ${c.images_per_row || 2} 张`;
    return "";
  }

  function updatePresetArea() {
    const mode = presetMode();
    $("preset-head").textContent = mode ? PTITLE[mode] : "预设选择";
    const row = $("preset-row");
    row.innerHTML = "";
    $("preset-area").style.display = "block";
    if (!mode) {
      const ph = document.createElement("span");
      ph.className = "preset-placeholder";
      ph.textContent = "无可用预设（仅 MD→DOCX / 图片→DOCX / TXT·MD→音频 / 音频→TXT 支持预设）";
      row.appendChild(ph);
      return;
    }
    // 语音引擎行：额外给出引擎下拉（直接编辑 tts 配置）
    if (mode === "tts") {
      const engSel = document.createElement("select");
      engSel.innerHTML = '<option value="kokoro">Kokoro（默认）</option><option value="edge">Edge-TTS（在线）</option>';
      engSel.value = (cfg.tts && cfg.tts.engine) || "kokoro";
      engSel.onchange = () => { cfg.tts = Object.assign({}, cfg.tts, { engine: engSel.value }); updateSummaryOnly(); };
      const engLbl = document.createElement("span"); engLbl.className = "lbl"; engLbl.textContent = "引擎";
      row.appendChild(engLbl); row.appendChild(engSel);
    }

    // 该类型的预设下拉：默认 + 命名预设 + 保存预设
    const pSel = document.createElement("select");
    pSel.id = "preset-sel-" + mode;
    pSel.innerHTML = buildPresetOptions(mode);
    pSel.onchange = () => onPresetSelect(mode);
    const pLbl = document.createElement("span"); pLbl.className = "lbl"; pLbl.textContent = "预设";
    row.appendChild(pLbl); row.appendChild(pSel);

    const btn = document.createElement("button");
    btn.className = "btn ghost param-btn"; btn.textContent = "参数设置…";
    btn.onclick = () => openParams(mode);
    const spacer = document.createElement("span"); spacer.style.marginLeft = "auto";
    row.appendChild(spacer); row.appendChild(btn);
    updateSummaryOnly();
  }
  function updateSummaryOnly() {
    let el = document.getElementById("preset-summary");
    const mode = presetMode();
    if (!mode) return;
    if (!el) {
      el = document.createElement("span"); el.className = "preset-summary"; el.id = "preset-summary";
      $("preset-row").appendChild(el);
    }
    el.textContent = summaryFor(mode);
  }

  async function onPresetSelect(kind) {
    const sel = $("preset-sel-" + kind); if (!sel) return;
    const v = sel.value;
    if (v === "") {
      cfg[kind] = (presetState[kind].defaults && clone(presetState[kind].defaults)) || fallbackCfg(kind);
      toast("已恢复默认参数");
    } else if (v === "__save") {
      const name = window.prompt("输入预设名称：");
      if (name && name.trim()) await savePreset(kind, name.trim());
      sel.innerHTML = buildPresetOptions(kind); sel.value = "";
    } else {
      await loadPreset(kind, v);
    }
    updatePresetArea();
  }

  const clone = (o) => JSON.parse(JSON.stringify(o || {}));

  // ═════════════════════════════════════════
  //  参数弹窗（4 个模态覆盖层）
  // ═════════════════════════════════════════
  const PAPERS = [["A4", "A4"], ["A3", "A3"]];
  const ORIENTS = [["portrait", "竖向"], ["landscape", "横向"]];
  const fCn = [["宋体", "宋体"], ["微软雅黑", "微软雅黑"], ["黑体", "黑体"], ["仿宋", "仿宋"], ["楷体", "楷体"]];
  const ALIGNS = [["left", "左对齐"], ["center", "居中"], ["justify", "两端对齐"]];
  const TTS_LANG = [["z", "中文(默认)"], ["a", "美式英文"], ["j", "日文"], ["f", "法文"]];
  const STT_LANG = [["auto", "自动检测"], ["zh", "中文"], ["en", "英文"], ["yue", "粤语"],
    ["ja", "日语"], ["ko", "韩语"]];
  const STT_DEV = [["auto", "自动"], ["cpu", "CPU（离线）"], ["cuda", "CUDA（GPU）"]];

  const selH = (id, opts, def) => {
    const o = opts.map(([v, l]) => `<option value="${v}"${String(v) === String(def) ? " selected" : ""}>${l}</option>`).join("");
    return `<select id="${id}">${o}</select>`;
  };
  const inp = (id, def, ph, type) =>
    `<input id="${id}" type="${type || "text"}" value="${String(def ?? "").replace(/"/g, "&quot;")}" placeholder="${ph || ""}"${type === "number" ? ' step="0.1"' : ""}>`;
  const chk = (id, def) => `<input id="${id}" type="checkbox"${def ? " checked" : ""}>`;
  const lbl = (t) => `<span class="lbl">${t}</span>`;
  const row = (...k) => `<div class="preset-row">${k.join("")}</div>`;

  let lastKind = "";

  // MD → DOCX 排版
  const docxPanel = (c = cfg.docx || fallbackCfg("docx")) =>
    row(lbl("页面大小"), selH("cf_paper", PAPERS, c.page.paper_size),
        lbl("方向"), selH("cf_orient", ORIENTS, c.page.orientation)) +
    row(lbl("正文字体"), selH("cf_font_cn", fCn, c.typography.body_font_cn),
        lbl("字号"), inp("cf_font_size", c.typography.body_font_size, "磅", "number")) +
    row(lbl("首行缩进"), inp("cf_indent", c.typography.first_line_indent_chars, "字符数", "number"),
        lbl("行距"), inp("cf_lsp", c.typography.line_spacing, "", "number"),
        lbl("对齐"), selH("cf_align", ALIGNS, c.typography.alignment)) +
    row(lbl("标题编号"), chk("cf_heading_num", c.advanced.heading_numbering),
        lbl("自动目录"), chk("cf_auto_toc", c.advanced.auto_toc));

  // TTS 语音参数
  const ttsPanel = (c = cfg.tts || fallbackCfg("tts")) =>
    row(lbl("引擎"), selH("cf_engine", [["kokoro", "Kokoro（默认）"], ["edge", "Edge-TTS（在线）"]], c.engine)) +
    row(lbl("Edge 音色"), inp("cf_edge_voice", c.edge_voice, "zh-CN-XiaoxiaoNeural"),
        lbl("语速"), inp("cf_edge_rate", c.edge_rate, "+0%"),
        lbl("音量"), inp("cf_edge_volume", c.edge_volume, "+0%")) +
    row(lbl("Kokoro 语言"), selH("cf_kokoro_lang", TTS_LANG, c.kokoro_lang),
        lbl("音色"), inp("cf_kokoro_voice", c.kokoro_voice, "zf_xiaoxiao"),
        lbl("语速"), inp("cf_kokoro_speed", c.kokoro_speed, "0.2-3.0", "number"));

  // 图片 → DOCX 排版
  const imgPanel = (c = cfg.img || fallbackCfg("img")) =>
    row(lbl("每行图数"), selH("cf_cols", [[1, "1"], [2, "2"], [3, "3"], [4, "4"]], c.images_per_row || 2));

  // STT 识别参数
  const sttPanel = (c = cfg.stt || fallbackCfg("stt")) =>
    row(lbl("语言"), selH("cf_stt_lang", STT_LANG, c.language),
        lbl("设备"), selH("cf_stt_device", STT_DEV, c.device),
        lbl("是否标点"), chk("cf_punct", c.use_itn !== false));

  const PANEL_FOR = { docx: docxPanel, tts: ttsPanel, img: imgPanel, stt: sttPanel };
  const TITLE_FOR = { docx: "MD → DOCX 排版", tts: "语音参数", img: "图片排版参数", stt: "识别参数" };

  function openParams(kind) {
    lastKind = kind;
    $("param-title").textContent = TITLE_FOR[kind] || "参数设置";
    // 保证后端 defaults 未返回时也有可用的 fallback
    if (!cfg[kind]) cfg[kind] = fallbackCfg(kind);
    $("param-body").innerHTML = PANEL_FOR[kind](cfg[kind]);
    $("param-mask").style.display = "flex";
  }
  function closeParams() { const m = $("param-mask"); if (m) m.style.display = "none"; }

  function readPanel(kind) {
    const c = clone(cfg[kind] || fallbackCfg(kind));
    const f = (id, d) => { const el = $(id); return el ? el.value : d; };
    const n = (id, d) => { const el = $(id); const x = parseFloat(el ? el.value : ""); return isFinite(x) ? x : d; };
    const b = (id) => { const el = $(id); return el ? el.checked : false; };
    if (kind === "docx") {
      c.page.paper_size = f("cf_paper", "A4"); c.page.orientation = f("cf_orient", "portrait");
      c.typography.body_font_cn = f("cf_font_cn", "宋体"); c.typography.body_font_size = n("cf_font_size", 12);
      c.typography.first_line_indent_chars = n("cf_indent", 2);
      c.typography.line_spacing = n("cf_lsp", 1.5); c.typography.alignment = f("cf_align", "left");
      c.advanced.heading_numbering = b("cf_heading_num"); c.advanced.auto_toc = b("cf_auto_toc");
    } else if (kind === "tts") {
      c.engine = f("cf_engine", "kokoro"); c.edge_voice = f("cf_edge_voice", "zh-CN-XiaoxiaoNeural");
      c.edge_rate = f("cf_edge_rate", "+0%"); c.edge_volume = f("cf_edge_volume", "+0%");
      c.kokoro_lang = f("cf_kokoro_lang", "z"); c.kokoro_voice = f("cf_kokoro_voice", "zf_xiaoxiao");
      c.kokoro_speed = n("cf_kokoro_speed", 1);
    } else if (kind === "img") {
      c.images_per_row = parseInt(f("cf_cols", "2"), 10) || 2;
    } else if (kind === "stt") {
      c.language = f("cf_stt_lang", "auto"); c.device = f("cf_stt_device", "cpu");
      c.use_itn = b("cf_punct");
    }
    return c;
  }

  function wireModal() {
    $("param-ok").onclick = () => { if (lastKind) { cfg[lastKind] = readPanel(lastKind); } closeParams(); updatePresetArea(); };
    $("param-close").onclick = closeParams;
    $("param-mask").addEventListener("click", (e) => { if (e.target === $("param-mask")) closeParams(); });
  }

  // ═════════════════════════════════════════
  //  输出路径自动推算 / 模式
  // ═════════════════════════════════════════
  function fileMode() { return !!$("input-path").value.trim(); }
  function folderMode() { return !!$("folder-path").value.trim(); }

  // 特殊：单文件 且 源∈{pdf,docx} 且 目标∈{jpg,jpeg,png} → 输出是文件夹（每页一张图）
  function outIsFolder() {
    return fileMode() && !folderMode() &&
      ["pdf", "docx"].indexOf(srcFmt) >= 0 && ["jpg", "jpeg", "png"].indexOf(dstFmt) >= 0;
  }

  function refreshOutputMode() {
    $("browse-output").textContent = outIsFolder() ? "浏览文件夹" : "浏览";
    $("output-hint").textContent = outIsFolder()
      ? "（源文件为 PDF/DOCX，将按页拆分为多张图片并保存到此文件夹）"
      : "（所有文件合并为单个文件后保存到此路径）";
  }

  function autoOutputName() {
    if (outManual) return;
    const out = $("output-path");
    const inp = $("input-path").value.trim();
    const folder = $("folder-path").value.trim();
    if (folder) {
      const p = folder.replace(/[\\/]+$/, "");
      const idx = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/"));
      const parent = idx >= 0 ? p.slice(0, idx + 1) : "";
      const name = idx >= 0 ? p.slice(idx + 1) : p;
      out.value = dstFmt ? parent + name + "_拼接." + DST_EXT(dstFmt) : parent + name;
    } else if (inp) {
      const dot = inp.lastIndexOf(".");
      const base = dot > 0 ? inp.slice(0, dot) : inp;
      // 单文件 PDF/DOCX→图片 时输出是文件夹（每页一张图），取目录名即可
      out.value = outIsFolder() ? (base + "_页") : (dstFmt ? base + "." + DST_EXT(dstFmt) : base);
    }
    refreshOutputMode();
  }

  // ═════════════════════════════════════════
  //  源变更联动
  // ═════════════════════════════════════════
  async function onSourceChanged() {
    const file = $("input-path").value.trim();
    const folder = $("folder-path").value.trim();
    if (file) $("folder-path").value = "";
    if (folder) $("input-path").value = "";
    const showFmt = !!folder;
    $("src-fmt-field").style.display = showFmt ? "block" : "none";

    if (folder) {
      // 文件夹模式：自动嗅探目录里占多数的文件类型作为源格式（可再手动修改）
      srcFmt = "";
      $("src-fmt-input").value = "";
      try {
        const r = await rpc("merge.scan", { input: folder });
        if (r && r.ok && r.src_format) {
          srcFmt = r.src_format;
          $("src-fmt-input").value = srcFmt;
        }
      } catch (e) { /* 嗅探失败仅静默，用户可手动选择 */ }
    } else if (file) {
      const dot = file.lastIndexOf(".");
      const ext = dot > 0 ? file.slice(dot + 1).toLowerCase() : "";
      if (ext) { srcFmt = ext; $("src-fmt-input").value = ext; }
    }
    updatePresetArea();
    await refreshDst();
    autoOutputName();
  }

  // ═════════════════════════════════════════
  //  开始拼接
  // ═════════════════════════════════════════
  function pickConfig() {
    if (IMG(srcFmt) && dstFmt === "docx") return cfg.img || fallbackCfg("img");
    if ((srcFmt === "txt" || srcFmt === "md") && AUD(dstFmt)) return cfg.tts || fallbackCfg("tts");
    if (AUD(srcFmt) && dstFmt === "txt") return cfg.stt || fallbackCfg("stt");
    if (dstFmt === "docx") return cfg.docx || fallbackCfg("docx");
    return cfg.docx || fallbackCfg("docx");
  }

  const isErrLine = (l) => /✘|错误|失败|❌|超时|timeout|timed out/i.test(l);
  const isTimeoutLine = (l) => /超时|timeout|timed out/i.test(l);

  async function runMerge() {
    if (busy) return;
    const file = $("input-path").value.trim();
    const folder = $("folder-path").value.trim();
    const out = $("output-path").value.trim();
    if (!file && !folder) { toast("请先选择源文件或源文件夹", "warn"); return; }
    if (!dstFmt) { toast("请先选择目标格式", "warn"); return; }
    if (folder && !srcFmt) { toast("请选择源文件格式", "warn"); return; }
    if (!out) { toast("请填写输出路径", "warn"); return; }

    const config = pickConfig();
    const cta = $("merge-btn");
    const lbl = $("merge-btn-label");
    busy = true; cta.disabled = true; lbl.textContent = "拼接中…";

    clearLog();
    try {
      if (file) {
        // 单文件：直接走 conversion 直连（慢路径也传大超时）
        appendLog("▶ 拼接（单文件）: " + file + " → " + dstFmt.toUpperCase(), "");
        const res = await callConversion({ input: file, output: out, target: dstFmt, config });
        const lines = (res && res.log) || [];
        lines.forEach((l) => appendLog(String(l), isErrLine(String(l)) ? "err" : "ok"));
        appendLog(res && res.ok ? "✔ 拼接完成 → " + out : "✘ 拼接未完成", res && res.ok ? "ok" : "err");
        if (res && res.ok) toast("拼接完成"); else toast("拼接失败", "warn");
      } else {
        // 多文件：merge.concat 做合并
        const res = await rpc("merge.concat",
          { input: folder, src_format: srcFmt, target: dstFmt, output: out, config });
        const lines = (res && res.log) || [];
        lines.forEach((l) => appendLog(String(l), isErrLine(String(l)) ? "err" : "ok"));
        // 后端 bridge 转换有门限：若日志含超时迹象，末尾追加提示
        if (lines.some((l) => isTimeoutLine(String(l)))) {
          appendLog("提示：部分慢速转换可能超时，请对极慢任务改用单文件方式", "warn");
        }
        appendLog(res && res.ok ? "✔ 拼接完成 → " + out : "✘ 拼接未完成", res && res.ok ? "ok" : "err");
        if (res && res.ok) toast("拼接完成"); else toast("拼接失败", "warn");
      }
    } catch (e) {
      appendLog("✘ " + esc(e && e.message ? e.message : e), "err");
      toast("拼接出错：" + (e && e.message ? e.message : e), "warn");
    } finally {
      busy = false; cta.disabled = false; lbl.textContent = "开始拼接";
    }
  }

  // ═════════════════════════════════════════
  //  日志
  // ═════════════════════════════════════════
  function clearLog() { $("log").textContent = ""; }
  function appendLog(msg, kind) {
    const div = document.createElement("div");
    if (kind) div.className = "log-" + kind;
    div.textContent = msg;
    $("log").appendChild(div);
    $("log").scrollTop = $("log").scrollHeight;
  }

  $("log").addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const txt = $("log").innerText || "";
    if (!txt) return;
    const ta = document.createElement("textarea");
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (err) {}
    document.body.removeChild(ta);
    toast("日志已复制");
  });

  // ═════════════════════════════════════════
  //  事件绑定
  // ═════════════════════════════════════════
  function bind() {
    $("browse-input").onclick = async () => {
      const r = await nativeDialog("pickFile");
      if (r && !r.canceled && r.path) { $("input-path").value = r.path; onSourceChanged(); }
    };
    $("browse-folder").onclick = async () => {
      const r = await nativeDialog("pickDir");
      if (r && !r.canceled && r.path) { $("folder-path").value = r.path; onSourceChanged(); }
    };
    $("browse-output").onclick = async () => {
      outManual = true;
      const r = await nativeDialog(outIsFolder() ? "pickDir" : "pickFile");
      if (r && !r.canceled && r.path) $("output-path").value = r.path;
      refreshOutputMode();
    };
    $("input-path").addEventListener("input", onSourceChanged);
    $("folder-path").addEventListener("input", onSourceChanged);
    $("src-fmt-input").addEventListener("input", (e) => applySrc(e.target.value));
    $("output-path").addEventListener("input", () => { outManual = true; refreshOutputMode(); });
    $("merge-btn").onclick = runMerge;
    $("clear-log").onclick = () => { clearLog(); $("log").textContent = "等待开始…"; };
    wireModal();
  }

  // 统一等 WS 真正连接（oct.ready）再加载，避免提前调用后端失败。
  window.addEventListener("oct.ready", () => { bind(); loadFormats(); loadAllPresets(); });
})();