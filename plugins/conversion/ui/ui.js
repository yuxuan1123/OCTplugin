// ══ 格式转换 · 交互逻辑 ══
// 复刻 OCTools 转换页：两级格式选择器（分类按钮 + 芯片）→ 可达性联动
// （同类直达 + 星型跨类寻路）→ 单文件/文件夹批量 → 实时日志。
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const apply = async (method, params) => {
    if (!window.OCT) throw new Error("内核未连接");
    // 长超时（客户端 + 内核 pl.Call 的 timeoutMs 都要）：惰性插件冷启动会先探测
    // 依赖（kokoro import 约 20s）再等进程就绪，且大文件转换可能较慢；
    // 默认 15s 会误超时导致目标格式列表加载为空。
    const wrap = await window.OCT.rpc(
      "plugin.call",
      { pluginId: "conversion", method, params: params || {}, timeoutMs: 600000 },
      600000
    );
    const payload = (wrap && wrap.result) || wrap || {};
    if (payload.error) throw new Error(payload.error);
    return payload;
  };

  let CATS = [];            // 分类：{key,name,formats:{values,labels,icons}}
  let ALL_SOURCE = [];      // 可作为源的格式 id
  let srcFmt = "";          // 当前源格式（文件夹模式下可手动指定）
  let dstFmt = "";          // 当前目标格式
  let reachable = [];       // 当前源的可达目标（扁平数组）
  let busy = false;
  let outManual = false;     // 输出路径是否由用户手动填写（手动则不再自动重算）
  let toastTimer = null;
  function toast(msg, kind = "ok") {
    const t = $("toast"); t.textContent = msg; t.className = "toast show " + kind;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = "toast"; }, 2200);
  }

  // ── 原生对话框（经宿主 postMessage 中转，host 提供 dialogue） ──
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

  // ── 加载格式元数据 ──
  async function loadFormats() {
    try {
      const res = await apply("conversion.formats", { src: srcFmt });
      if (!res || !res.ok) { toast("加载格式失败", "warn"); return; }
      CATS = res.categories || [];
      ALL_SOURCE = res.source_formats || [];
      if (res.reachable && res.reachable.length) reachable = res.reachable;
      buildPicker($("dst-picker"), "dst");
      buildSrcPicker();
      renderChips("dst");
    } catch (e) { toast("加载格式失败：" + (e.message || e), "warn"); }
  }

  const chipsOf = (cat) => (cat && cat.formats && cat.formats.values) || [];

  // ── 构建两级选择器（一级分类按钮 + 二级芯片） ──
  function buildPicker(container, which) {
    container.innerHTML = "";
    const catsRow = document.createElement("div"); catsRow.className = "cats";
    const chipsBox = document.createElement("div"); chipsBox.className = "chips";
    container.appendChild(catsRow); container.appendChild(chipsBox);

    CATS.forEach((cat, ci) => {
      const b = document.createElement("button");
      b.className = "cat-btn" + (ci === 0 ? " active" : "");
      b.textContent = cat.name;
      b.dataset.cat = cat.key;
      b.onclick = () => {
        catsRow.querySelectorAll(".cat-btn").forEach((x) => x.classList.remove("active"));
        b.classList.add("active"); renderChips(which);
      };
      catsRow.appendChild(b);
    });
  }

  const pickerOf = (which) => $(which === "dst" ? "dst-picker" : "src-picker");

  const activeCat = (which) => {
    const box = pickerOf(which); if (!box) return CATS[0] || null;
    const act = box.querySelector(".cat-btn.active");
    return CATS.find((c) => c.key === (act && act.dataset.cat)) || CATS[0] || null;
  };

  function renderChips(which) {
    if (!CATS.length) return;
    const cat = activeCat(which);
    const chipsBox = pickerOf(which).querySelector(".chips");
    chipsBox.innerHTML = "";
    if (!cat) return;

    const labels = (cat.formats && cat.formats.labels) || [];
    chipsOf(cat).forEach((fmt, i) => {
      const c = document.createElement("button");
      c.className = "chip";
      c.dataset.fmt = fmt;
      c.textContent = labels[i] || fmt.toUpperCase();
      const isActive = which === "dst" ? (dstFmt === fmt) : (srcFmt === fmt);
      if (which === "dst") c.disabled = !canReach(fmt);
      if (isActive) c.classList.add("active");
      else if (which === "dst" && c.disabled && dstFmt === fmt) dstFmt = "";
      c.onclick = () => (which === "dst" ? selectDst(fmt) : selectSrc(fmt));
      chipsBox.appendChild(c);
    });

    // 空态：当前分类无任何可达目标
    const hint = $("dst-hint");
    if (which === "dst") {
      const anyInCat = chipsOf(cat).some((f) => canReach(f));
      const hasAny = chipsOf(cat).length > 0;
      hint.style.display = (hasAny && !anyInCat) ? "block" : "none";
      if (hasAny && !anyInCat) hint.textContent = "当前源没有可选的目标格式";
    }
  }

  // 无可达清单（无源）时全量可选
  function canReach(fmt) {
    if (!srcFmt) return true;
    return reachable.indexOf(fmt) >= 0;
  }

  // ── 源格式选择器（仅文件夹模式显示；全部源格式扁平陈列） ──
  function buildSrcPicker() {
    const box = pickerOf("src");
    box.innerHTML = "";
    const chipsBox = document.createElement("div"); chipsBox.className = "chips";
    box.appendChild(chipsBox);
    ALL_SOURCE.forEach((fmt) => {
      const label = fmt === "txt" ? "TXT" : fmt.toUpperCase();
      const c = document.createElement("button");
      c.className = "chip" + (srcFmt === fmt ? " active" : "");
      c.dataset.fmt = fmt;
      c.textContent = label;
      c.onclick = () => selectSrc(fmt);
      chipsBox.appendChild(c);
    });
  }

  function addActiveSrc(box, fmt) {
    const el = box && box.querySelector(`[data-fmt="${fmt}"]`);
    if (el) { box.querySelectorAll(".chip").forEach((x) => x.classList.remove("active")); el.classList.add("active"); }
  }

  function selectSrc(fmt) {
    srcFmt = fmt;
    const box = pickerOf("src");
    box.querySelectorAll(".chip").forEach((x) => x.classList.toggle("active", x.dataset.fmt === fmt));
    updatePresetArea();
    refreshDst();
  }

  function selectDst(fmt) {
    dstFmt = fmt;
    pickerOf("dst").querySelectorAll(".chip").forEach((x) => x.classList.toggle("active", x.dataset.fmt === fmt));
    updatePresetArea();
    autoOutputName();
  }

  // ── 刷新目标可达性（后端 planner.beachable * 星型寻路） ──
  async function refreshDst() {
    try {
      const res = await apply("conversion.formats", { src: srcFmt });
      if (!res || !res.ok) return;
      reachable = res.reachable || [];
      renderChips("dst");
    } catch (e) { toast("刷新可达目标失败", "warn"); }
  }

  // ── 预设区（按 源 + 目标 显隐） ──
  const IMG = (f) => ["png", "jpg", "jpeg", "bmp", "webp", "tiff", "heic", "gif"].indexOf(f) >= 0;
  const AUD = (f) => ["mp3", "aac", "wav", "flac", "ogg", "opus", "wma", "m4a", "amr", "ac3", "aiff"].indexOf(f) >= 0;
  const isOCR = (f) => f === "txt-ocr" || f === "txt_ocr";

  // ── 预设区参数面板（按 源 + 目标 渲染对应转换类型的可选参数）──
  const sel = (id, opts, def) => {
    const o = opts.map(([v, l]) =>
      `<option value="${v}"${String(v) === String(def) ? " selected" : ""}>${l}</option>`).join("");
    return `<select id="${id}">${o}</select>`;
  };
  const inp = (id, def, ph, type) => {
    const escV = String(def ?? "").replace(/"/g, "&quot;");
    return `<input id="${id}" type="${type || "text"}" value="${escV}" placeholder="${ph || ""}">`;
  };
  const chk = (id, def) => `<input id="${id}" type="checkbox"${def ? " checked" : ""}>`;
  const lbl = (t, sub) =>
    `<span class="lbl"${sub ? ` style="font-size:11px"` : ""}>${t}</span>`;
  const row = (...kids) => `<div class="preset-row">${kids.join("")}</div>`;
  const note = (t) => `<div class="preset-row"><span class="preset-note">${t}</span></div>`;

  const PAPERS = [["A4", "A4"], ["A3", "A3"], ["A5", "A5"], ["Letter", "Letter"], ["Legal", "Legal"]];
  const ORIENTS = [["portrait", "竖向"], ["landscape", "横向"]];
  const LMODES = [["multiple", "多倍行距"], ["fixed", "固定值"]];
  const ALIGNS = [["left", "左对齐"], ["center", "居中"], ["right", "右对齐"], ["justify", "两端对齐"]];
  const TTS_ENGINES = [["kokoro", "Kokoro（默认）"], ["edge", "Edge-TTS（在线）"], ["moss", "MOSS-TTS（模仿）"]];
  const STT_LANGS = [["auto", "自动检测"], ["zh", "中文"], ["en", "英文"], ["yue", "粤语"],
    ["ja", "日语"], ["ko", "韩语"], ["nospeech", "仅静音"]];
  const OCR_LANGS = [["ch", "中文"], ["en", "英文"], ["japan", "日文"], ["korean", "韩文"]];

  const mdDocxPanel = () =>
    note("标题 / 列表 / 段落 结构化转换 · 可调整页面与排版") +
    row(lbl("纸张"), sel("cfg_paper", PAPERS, "A4"), lbl("方向"), sel("cfg_orient", ORIENTS, "portrait")) +
    row(lbl("页边距 上/下(cm)"), inp("cfg_margin_t", 2.54), inp("cfg_margin_b", 2.54),
      lbl("左/右(cm)"), inp("cfg_margin_l", 3.18), inp("cfg_margin_r", 3.18)) +
    row(lbl("中文字体"), inp("cfg_font_cn", "宋体"), lbl("英文字体"), inp("cfg_font_en", "Times New Roman"), lbl("字号"), inp("cfg_font_size", 12, "磅", "number")) +
    row(lbl("行距模式"), sel("cfg_lsp_mode", LMODES, "multiple"), lbl("行距"), inp("cfg_lsp", 1.5, "", "number"),
      lbl("首行缩进"), inp("cfg_indent", 2.0, "字符数", "number"), lbl("对齐"), sel("cfg_align", ALIGNS, "left")) +
    row(lbl("H1 字号"), inp("cfg_h1", 22, "", "number"), lbl("H2 字号"), inp("cfg_h2", 18, "", "number"), lbl("H3 字号"), inp("cfg_h3", 14, "", "number")) +
    row(lbl("代码字体"), inp("cfg_code_font", "Consolas")) +
    row(lbl("标题编号"), chk("cfg_heading_num", false), lbl("自动目录"), chk("cfg_auto_toc", false),
      lbl("目录深度"), inp("cfg_toc_depth", 3, "", "number")) +
    row(lbl("页眉"), inp("cfg_header", ""), lbl("页脚"), inp("cfg_footer", ""));

  const ttsPanel = () =>
    note("文本 转 音频 · 选择引擎并设置音色/语速") +
    row(lbl("引擎"), sel("cfg_tts_engine", TTS_ENGINES, "kokoro")) +
    row(lbl("Edge 音色"), inp("cfg_edge_voice", "zh-CN-XiaoxiaoNeural"),
      lbl("语速"), inp("cfg_edge_rate", "+0%"), lbl("音量"), inp("cfg_edge_volume", "+0%"), lbl("音调"), inp("cfg_edge_pitch", "+0Hz")) +
    row(lbl("Kokoro 语言"), sel("cfg_kokoro_lang", [["z", "中文(默认)"], ["a", "美式英文"], ["f", "法文"], ["j", "日文"]], "z"),
      lbl("音色"), inp("cfg_kokoro_voice", "zf_xiaoxiao"), lbl("语速"), inp("cfg_kokoro_speed", 1.0, "0.2–3.0", "number")) +
    row(lbl("MOSS 模型目录"), inp("cfg_moss_model", "")) +
    row(lbl("MOSS 音色"), inp("cfg_moss_voice", ""), lbl("参考音频"), inp("cfg_moss_ref", ""));

  const sttPanel = () =>
    note("音频 转 文本 · 识别语言 / 设备 / 数字归一化") +
    row(lbl("语言"), sel("cfg_stt_lang", STT_LANGS, "auto"), lbl("设备"), sel("cfg_stt_device",
      [["cpu", "CPU（纯离线）"], ["cuda", "CUDA（GPU）"]], "cpu"), lbl("数字归一化"), chk("cfg_stt_itn", true));

  const ocrPanel = () =>
    note("调用 PaddleOCR 识别图片中的文字（PP-OCRv6_tiny / onnxruntime / CPU）") +
    row(lbl("识别语言"), sel("cfg_ocr_lang", OCR_LANGS, "ch"));

  const imgPanel = () =>
    note("图片合并为单个 DOCX") +
    row(lbl("每行张数"), sel("preset-cols", [[1, "1"], [2, "2"], [3, "3"], [4, "4"]], 2));

  // ── 当前参数（稳定存储；弹窗内编辑后写入，转换时读取）──
  let currentCfg = {};
  let lastKind = "";

  function defaultCfgFor(kind) {
    switch (kind) {
      case "md": return {
        page: { paper_size: "A4", orientation: "portrait", margin_top: 2.54, margin_bottom: 2.54, margin_left: 3.18, margin_right: 3.18 },
        typography: { body_font_cn: "宋体", body_font_en: "Times New Roman", body_font_size: 12,
          line_spacing_mode: "multiple", line_spacing: 1.5, first_line_indent_chars: 2.0, alignment: "left",
          headings: { h1: { font_size: 22 }, h2: { font_size: 18 }, h3: { font_size: 14 } } },
        content: { code_font: "Consolas" },
        advanced: { heading_numbering: false, auto_toc: false, toc_depth: 3, header_text: "", footer_text: "" },
      };
      case "tts": return { engine: "kokoro", edge_voice: "zh-CN-XiaoxiaoNeural", edge_rate: "+0%",
        edge_volume: "+0%", edge_pitch: "+0Hz", kokoro_lang: "z", kokoro_voice: "zf_xiaoxiao",
        kokoro_speed: 1.0, moss_model_dir: "", moss_voice: "", moss_reference: "" };
      case "stt": return { language: "auto", device: "cpu", use_itn: true };
      case "ocr": return { lang: "ch" };
      case "img": return { images_per_row: 2 };
      default: return {};
    }
  }

  const setField = (id, val) => {
    const el = $(id); if (!el) return;
    if (el.type === "checkbox") el.checked = !!val; else el.value = val;
  };

  function applyCfgToPanel(kind) {
    const c = currentCfg;
    if (kind === "md") {
      setField("cfg_paper", c.page.paper_size); setField("cfg_orient", c.page.orientation);
      setField("cfg_margin_t", c.page.margin_top); setField("cfg_margin_b", c.page.margin_bottom);
      setField("cfg_margin_l", c.page.margin_left); setField("cfg_margin_r", c.page.margin_right);
      setField("cfg_font_cn", c.typography.body_font_cn); setField("cfg_font_en", c.typography.body_font_en);
      setField("cfg_font_size", c.typography.body_font_size); setField("cfg_lsp_mode", c.typography.line_spacing_mode);
      setField("cfg_lsp", c.typography.line_spacing); setField("cfg_indent", c.typography.first_line_indent_chars);
      setField("cfg_align", c.typography.alignment);
      setField("cfg_h1", c.typography.headings.h1.font_size); setField("cfg_h2", c.typography.headings.h2.font_size);
      setField("cfg_h3", c.typography.headings.h3.font_size);
      setField("cfg_code_font", c.content.code_font);
      setField("cfg_heading_num", c.advanced.heading_numbering); setField("cfg_auto_toc", c.advanced.auto_toc);
      setField("cfg_toc_depth", c.advanced.toc_depth); setField("cfg_header", c.advanced.header_text);
      setField("cfg_footer", c.advanced.footer_text);
    } else if (kind === "tts") {
      setField("cfg_tts_engine", c.engine);
      setField("cfg_edge_voice", c.edge_voice); setField("cfg_edge_rate", c.edge_rate);
      setField("cfg_edge_volume", c.edge_volume); setField("cfg_edge_pitch", c.edge_pitch);
      setField("cfg_kokoro_lang", c.kokoro_lang); setField("cfg_kokoro_voice", c.kokoro_voice);
      setField("cfg_kokoro_speed", c.kokoro_speed);
      setField("cfg_moss_model", c.moss_model_dir); setField("cfg_moss_voice", c.moss_voice);
      setField("cfg_moss_ref", c.moss_reference);
    } else if (kind === "stt") {
      setField("cfg_stt_lang", c.language); setField("cfg_stt_device", c.device); setField("cfg_stt_itn", c.use_itn);
    } else if (kind === "ocr") {
      setField("cfg_ocr_lang", c.lang);
    } else if (kind === "img") {
      setField("preset-cols", c.images_per_row);
    }
  }

  function readCfgFromPanel(kind) {
    const f = (k, d) => { const el = $(k); return el ? el.value : d; };
    const n = (k, d) => { const el = $(k); const x = parseFloat(el ? el.value : ""); return isFinite(x) ? x : d; };
    const b = (k) => { const el = $(k); return el ? el.checked : false; };
    if (kind === "md") return {
      page: { paper_size: f("cfg_paper", "A4"), orientation: f("cfg_orient", "portrait"),
        margin_top: n("cfg_margin_t", 2.54), margin_bottom: n("cfg_margin_b", 2.54),
        margin_left: n("cfg_margin_l", 3.18), margin_right: n("cfg_margin_r", 3.18) },
      typography: { body_font_cn: f("cfg_font_cn", "宋体"), body_font_en: f("cfg_font_en", "Times New Roman"),
        body_font_size: n("cfg_font_size", 12), line_spacing_mode: f("cfg_lsp_mode", "multiple"),
        line_spacing: n("cfg_lsp", 1.5), first_line_indent_chars: n("cfg_indent", 2.0), alignment: f("cfg_align", "left"),
        headings: { h1: { font_size: n("cfg_h1", 22) }, h2: { font_size: n("cfg_h2", 18) }, h3: { font_size: n("cfg_h3", 14) } } },
      content: { code_font: f("cfg_code_font", "Consolas") },
      advanced: { heading_numbering: b("cfg_heading_num"), auto_toc: b("cfg_auto_toc"), toc_depth: n("cfg_toc_depth", 3),
        header_text: f("cfg_header", ""), footer_text: f("cfg_footer", "") },
    };
    if (kind === "tts") return { engine: f("cfg_tts_engine", "kokoro"), edge_voice: f("cfg_edge_voice", "zh-CN-XiaoxiaoNeural"),
      edge_rate: f("cfg_edge_rate", "+0%"), edge_volume: f("cfg_edge_volume", "+0%"), edge_pitch: f("cfg_edge_pitch", "+0Hz"),
      kokoro_lang: f("cfg_kokoro_lang", "z"), kokoro_voice: f("cfg_kokoro_voice", "zf_xiaoxiao"),
      kokoro_speed: n("cfg_kokoro_speed", 1.0), moss_model_dir: f("cfg_moss_model", ""),
      moss_voice: f("cfg_moss_voice", ""), moss_reference: f("cfg_moss_ref", "") };
    if (kind === "stt") return { language: f("cfg_stt_lang", "auto"), device: f("cfg_stt_device", "cpu"), use_itn: b("cfg_stt_itn") };
    if (kind === "ocr") return { lang: f("cfg_ocr_lang", "ch") };
    if (kind === "img") return { images_per_row: parseInt($("preset-cols") ? $("preset-cols").value : "2", 10) || 2 };
    return {};
  }

  const collectCfg = () => currentCfg;

  // ── 参数弹窗 ──
  const PANEL_FOR = { md: mdDocxPanel, tts: ttsPanel, stt: sttPanel, ocr: ocrPanel, img: imgPanel };
  const TITLE_FOR = { md: "MD → DOCX 排版", tts: "TTS 语音参数", stt: "STT 识别参数",
    ocr: "图片 → 文字（OCR 参数）", img: "图片 → DOCX 排版", pdf: "PDF → DOCX" };

  function panelKind() {
    if (srcFmt === "md" && dstFmt === "docx") return "md";
    if (IMG(srcFmt) && isOCR(dstFmt)) return "ocr";
    if (IMG(srcFmt) && dstFmt === "docx") return "img";
    if ((srcFmt === "txt" || srcFmt === "md") && AUD(dstFmt)) return "tts";
    if (AUD(srcFmt) && dstFmt === "txt") return "stt";
    if (srcFmt === "pdf" && dstFmt === "docx") return "pdf";
    return "";
  }

  function summaryFor(kind) {
    const c = currentCfg;
    const eng = { kokoro: "Kokoro（默认）", edge: "Edge-TTS（在线）", moss: "MOSS-TTS（模仿）" };
    const lng = { auto: "自动", zh: "中文", en: "英文", yue: "粤语", ja: "日语", ko: "韩语", nospeech: "仅静音" };
    const ocrLng = { ch: "中文", en: "英文", japan: "日文", korean: "韩文" };
    if (kind === "md") return `${c.page.paper_size} ${c.page.orientation === "landscape" ? "横向" : "竖向"} · ${c.typography.body_font_cn} ${c.typography.body_font_size} 磅 · ${c.typography.line_spacing} 倍行距`;
    if (kind === "tts") { const v = c.engine === "edge" ? ` · ${c.edge_voice}` : c.engine === "kokoro" ? ` · ${c.kokoro_voice}` : ""; return `${eng[c.engine] || c.engine}${v}`; }
    if (kind === "stt") return `${lng[c.language] || c.language} · ${c.device === "cuda" ? "GPU" : "CPU"}`;
    if (kind === "ocr") return ocrLng[c.lang] || c.lang;
    if (kind === "img") return `每行 ${c.images_per_row} 张`;
    return "纯 Python 文本重建";
  }

  function openParams(kind) {
    $("param-title").textContent = TITLE_FOR[kind] || "参数设置";
    $("param-body").innerHTML = (PANEL_FOR[kind] || (() => ""))();
    applyCfgToPanel(kind);
    $("param-mask").style.display = "flex";
  }
  function closeParams() { const m = $("param-mask"); if (m) m.style.display = "none"; }

  function wireModal() {
    $("param-ok").onclick = () => {
      currentCfg = readCfgFromPanel(lastKind);
      closeParams();
      updatePresetArea();
    };
    $("param-reset").onclick = () => {
      if (!lastKind) return;
      currentCfg = defaultCfgFor(lastKind);
      applyCfgToPanel(lastKind);
      closeParams();
      updatePresetArea();
    };
    $("param-close").onclick = closeParams;
    $("param-mask").addEventListener("click", (e) => { if (e.target === $("param-mask")) closeParams(); });
  }

  function updatePresetArea() {
    const area = $("preset-area");
    const kind = panelKind();
    if (kind !== lastKind) { currentCfg = defaultCfgFor(kind); lastKind = kind; }
    area.innerHTML = "";
    if (!kind) { area.style.display = "none"; return; }
    area.style.display = "block";
    const r = document.createElement("div"); r.className = "preset-row";
    const t = document.createElement("span"); t.className = "preset-head"; t.textContent = TITLE_FOR[kind];
    const s = document.createElement("span"); s.className = "preset-note"; s.id = "preset-summary";
    s.textContent = summaryFor(kind);
    const btn = document.createElement("button"); btn.className = "btn param-btn"; btn.textContent = "参数设置…";
    btn.onclick = () => openParams(kind);
    const sp = document.createElement("span"); sp.style.marginLeft = "auto";
    r.appendChild(t); r.appendChild(s);
    if (PANEL_FOR[kind]) { r.appendChild(sp); r.appendChild(btn); }
    area.appendChild(r);
  }

  // ── 输出路径/文件夹自动推算 ──
  function dstExt() {
    const e = { "txt-ocr": "txt", "txt_ocr": "txt", "pptx-img": "pptx" }[dstFmt] || dstFmt;
    return e;
  }

  function folderMode() { return !!$("folder-path").value.trim(); }

  function autoOutputName() {
    if (outManual) return;
    const out = $("output-folder");
    if (!out) return;
    if (!dstFmt) return;
    if (folderMode()) {
      const p = $("folder-path").value.replace(/[\\/]+$/, "");
      const idx = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/"));
      const parent = idx >= 0 ? p.slice(0, idx + 1) : "";
      const name = idx >= 0 ? p.slice(idx + 1) : p;
      out.value = parent + name + "_转换_" + dstFmt.toUpperCase();
    } else {
      const inp = $("input-path").value.trim();
      if (!inp) return;
      const dot = inp.lastIndexOf(".");
      const base = dot > 0 ? inp.slice(0, dot) : inp;
      out.value = base + "." + dstExt();
    }
  }

  // ── 源变更联动 ──
  async function onSourceChanged() {
    const file = $("input-path").value.trim();
    const folder = $("folder-path").value.trim();
    if (file) $("folder-path").value = "";
    if (folder) $("input-path").value = "";
    $("src-fmt-field").style.display = folder ? "block" : "none";
    if (folder) {
      // 文件夹模式下需指定源格式；如未选择则保持现状
    } else if (file) {
      const dot = file.lastIndexOf(".");
      const ext = dot > 0 ? file.slice(dot + 1).toLowerCase() : "";
      if (ext && ALL_SOURCE.indexOf(ext) >= 0) srcFmt = ext;
      addActiveSrc(pickerOf("src"), srcFmt);
    }
    updatePresetArea();
    await refreshDst();
    autoOutputName();
  }

  // ── 执行转换 ──
  async function runConvert() {
    if (busy) return;
    const file = $("input-path").value.trim();
    const folder = $("folder-path").value.trim();
    const out = $("output-folder").value.trim();
    if (!file && !folder) { toast("请先选择源文件或源文件夹", "warn"); return; }
    if (!dstFmt) { toast("请先选择目标格式", "warn"); return; }

    const cfg = collectCfg();

    let params;
    if (folder) {
      if (!srcFmt) { toast("请选择源文件格式", "warn"); return; }
      if (!out) { toast("文件夹模式需要输出文件夹", "warn"); return; }
      params = { input: folder, src_format: srcFmt, target: dstFmt, out_dir: out, config: cfg };
    } else {
      params = { input: file, target: dstFmt, config: cfg };
      if (out) params.output = out;
    }

    const logEl = $("log");
    const cta = $("convert-btn");
    busy = true; cta.disabled = true; cta.textContent = "转换中...";
    clearLog();
    try {
      appendLog("▶ " + (folder ? "批量转换" : "转换") + ": " + (folder ? folder : file) + " → " + dstFmt.toUpperCase(), "info");
      const res = await apply("conversion.convert", params);
      const lines = (res && res.log) || [];
      lines.forEach((l) => appendLog(l, /✘|错误|失败|❌/i.test(String(l)) ? "err" : "ok"));
      $("state").textContent = res && res.ok ? "✔ 转换完成" : "✘ 转换未完成";
      if (res && res.ok) toast("转换完成", "ok"); else toast("转换失败", "warn");
    } catch (e) {
      appendLog("✘ " + esc(e.message || e), "err");
      $("state").textContent = "✘ 转换出错";
      toast("转换出错：" + (e.message || e), "warn");
    } finally {
      busy = false; cta.disabled = false; cta.textContent = "开始转换";
    }
  }

  function clearLog() { $("log").innerHTML = "等待开始…"; $("state").textContent = ""; }
  function appendLog(msg, kind) {
    const div = document.createElement("div");
    if (kind) div.className = "log-" + kind;
    div.textContent = msg;
    $("log").appendChild(div);
    $("log").scrollTop = $("log").scrollHeight;
  }

  // 右键复制全部日志
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

  // ── 事件绑定 ──
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
      const r = await nativeDialog("pickDir");
      if (r && !r.canceled && r.path) { outManual = true; $("output-folder").value = r.path; }
    };
    $("input-path").addEventListener("input", onSourceChanged);
    $("folder-path").addEventListener("input", onSourceChanged);
    $("output-folder").addEventListener("input", () => { outManual = true; autoOutputName(); });
    $("convert-btn").onclick = runConvert;
    wireModal();
  }

  // 初始：等桥就绪后绑事件 + 加载格式
  function start() {
    if (window.OCT) { bind(); loadFormats(); return; }
    setTimeout(start, 60);
  }
  window.addEventListener("oct.ready", () => { bind(); loadFormats(); });
  start();
})();