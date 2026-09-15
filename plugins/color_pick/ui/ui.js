// 取色器业务逻辑（纯前端，移植自 OCTools TabColorPick）。
(() => {
  const $ = (id) => document.getElementById(id);
  const toast = (msg) => {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 1800);
  };

  let color = { r: 52, g: 152, b: 219 }; // 初始 #3498DB
  const hex = () => "#" + [color.r, color.g, color.b].map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase();

  // ── 颜色转换（移植自 tab_color_pick.py，逻辑保持一致） ──
  const toHSL = (r, g, b) => {
    const [rr, gg, bb] = [r / 255, g / 255, b / 255];
    const mx = Math.max(rr, gg, bb), mn = Math.min(rr, gg, bb);
    const l = (mx + mn) / 2;
    let h = 0, s = 0;
    if (mx !== mn) {
      const d = mx - mn;
      s = d / (1 - Math.abs(2 * l - 1));
      if (mx === rr) h = ((gg - bb) / d) % 6;
      else if (mx === gg) h = (bb - rr) / d + 2;
      else h = (rr - gg) / d + 4;
      h *= 60; if (h < 0) h += 360;
    }
    return [Math.round(h), Math.round(s * 100), Math.round(l * 100)];
  };
  const toHSV = (r, g, b) => {
    const [rr, gg, bb] = [r / 255, g / 255, b / 255];
    const mx = Math.max(rr, gg, bb), mn = Math.min(rr, gg, bb);
    const v = mx, d = mx - mn;
    let h = 0, s = 0;
    if (mx !== 0) s = d / mx;
    if (d !== 0) {
      if (mx === rr) h = ((gg - bb) / d) % 6;
      else if (mx === gg) h = (bb - rr) / d + 2;
      else h = (rr - gg) / d + 4;
      h *= 60; if (h < 0) h += 360;
    }
    return [Math.round(h), Math.round(s * 100), Math.round(v * 100)];
  };
  const toCMYK = (r, g, b) => {
    if (r === 0 && g === 0 && b === 0) return [0, 0, 0, 100];
    let c = 1 - r / 255, m = 1 - g / 255, y = 1 - b / 255;
    const k = Math.min(c, m, y);
    if (k === 1) return [0, 0, 0, 100];
    c = (c - k) / (1 - k) * 100; m = (m - k) / (1 - k) * 100; y = (y - k) / (1 - k) * 100;
    return [Math.round(c), Math.round(m), Math.round(y), Math.round(k * 100)];
  };
  const isDark = (r, g, b) => (0.299 * r + 0.587 * g + 0.114 * b) < 128;

  const fmts = [
    ["十六进制 (Hex)", () => hex()],
    ["RGB", () => `rgb(${color.r}, ${color.g}, ${color.b})`],
    ["HSL", () => { const [h, s, l] = toHSL(color.r, color.g, color.b); return `hsl(${h}°, ${s}%, ${l}%)`; }],
    ["HSV", () => { const [h, s, v] = toHSV(color.r, color.g, color.b); return `hsv(${h}°, ${s}%, ${v}%)`; }],
    ["CMYK", () => { const [c, m, y, k] = toCMYK(color.r, color.g, color.b); return `cmyk(${c}%, ${m}%, ${y}%, ${k}%)`; }],
  ];

  function render() {
    const hx = hex();
    const btn = $("picker-btn");
    btn.style.background = hx;
    btn.style.color = isDark(color.r, color.g, color.b) ? "#fff" : "#000";
    $("swatch").style.background = hx;
    $("cur-hex").textContent = hx;
    fmts.forEach(([name, fn], i) => {
      const row = document.querySelectorAll(".fmt-row")[i];
      row.querySelector(".fmt-val").textContent = fn();
    });
  }

  // ── 页面内置取色器（跨源 iframe 内 Chromium 拦截原生 color input 的弹窗，
//    故用 canvas 自绘 HSV 取色板，完全在 iframe 内运行） ──
  let temp = { h: 210, s: 0.6, v: 0.9 };   // 工作色（去色板内临时拖动）
  const hsvToRgb = (h, s, v) => {
    h = ((h % 360) + 360) % 360;
    const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    let R = 0, G = 0, B = 0;
    if (h < 60) { R = c; G = x; } else if (h < 120) { R = x; G = c; }
    else if (h < 180) { G = c; B = x; } else if (h < 240) { G = x; B = c; }
    else if (h < 300) { R = x; B = c; } else { R = c; B = x; }
    return { r: Math.round((R + m) * 255), g: Math.round((G + m) * 255), b: Math.round((B + m) * 255) };
  };

  const SABOX = $("sa-box"), HUEGAN = $("hue-bar");
  const saCtx = SABOX.getContext("2d"), hueCtx = HUEGAN.getContext("2d");
  const boxRect = () => SABOX.getBoundingClientRect();
  const hueRect = () => HUEGAN.getBoundingClientRect();

  function drawSa() {
    const w = SABOX.width, h = SABOX.height;
    const hg = saCtx.createLinearGradient(0, 0, w, 0);
    hg.addColorStop(0, "#fff");
    hg.addColorStop(1, `hsl(${temp.h.toFixed(0)},100%,50%)`);
    saCtx.fillStyle = hg; saCtx.fillRect(0, 0, w, h);
    const vg = saCtx.createLinearGradient(0, 0, 0, h);
    vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, "rgba(0,0,0,1)");
    saCtx.fillStyle = vg; saCtx.fillRect(0, 0, w, h);
    // 句柄
    const dx = temp.s * boxRect().width, dy = (1 - temp.v) * boxRect().height;
    const dot = $("p-dot");
    dot.style.left = dx + "px"; dot.style.top = dy + "px";
  }
  function drawHue() {
    const w = HUEGAN.width, h = HUEGAN.height;
    const g = hueCtx.createLinearGradient(0, 0, w, 0);
    for (let i = 0; i <= 12; i++) g.addColorStop(i / 12, `hsl(${i * 30},100%,50%)`);
    hueCtx.fillStyle = g; hueCtx.fillRect(0, 0, w, h);
    $("p-thumb").style.left = (temp.h / 360 * hueRect().width) + "px";
  }
  function applyTemp() {
    const { r, g, b } = hsvToRgb(temp.h, temp.s, temp.v);
    color = { r, g, b }; render();
    $("p-swatch").style.background = hex();
    $("p-hex").textContent = hex();
  }

  const bindDrag = (el, update) => {
    const move = (e) => { update(e); };
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      update(e);
    });
    el.addEventListener("pointermove", (e) => {
      if (el.hasPointerCapture(e.pointerId)) update(e);
    });
  };
  bindDrag(SABOX, (e) => {
    const r = boxRect();
    temp.s = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    temp.v = Math.min(1, Math.max(0, 1 - (e.clientY - r.top) / r.height));
    drawSa(); applyTemp();
  });
  bindDrag(HUEGAN, (e) => {
    const r = hueRect();
    temp.h = Math.max(0, Math.min(360, (e.clientX - r.left) / r.width * 360));
    drawHue(); drawSa(); applyTemp();
  });

  let savedHex = "#3498DB";   // 打开时的颜色，供“取消”回滚
  function openPicker() {
    savedHex = hex();
    const [h, s, v] = toHSV(color.r, color.g, color.b);
    temp = { h, s: s / 100 || 0, v: v / 100 || 0 };
    $("p-overlay").hidden = false;
    drawHue(); drawSa(); applyTemp();
  }
  $("p-cancel").onclick = () => {
    $("p-overlay").hidden = true;
    color = { r: parseInt(savedHex.slice(1, 3), 16), g: parseInt(savedHex.slice(3, 5), 16), b: parseInt(savedHex.slice(5, 7), 16) };
    render();
  };
  $("p-ok").onclick = () => { $("p-overlay").hidden = true; };
  $("p-overlay").addEventListener("click", (e) => {
    if (e.target === $("p-overlay")) $("p-cancel").onclick();
  });

  // ── 全屏取色：后端抓整屏 → 前端渲染拾取（跨插件范围） ──
  const fsOv = $("fs-overlay"), fsCv = $("fs-canvas"), fsMag = $("fs-mag"),
        fsBub = $("fs-bubble"), fsStage = $("fs-stage");
  const magCtx = fsMag.getContext("2d");
  let fsOff = null;          // 全分辨率离屏 canvas（用于精确取像素/放大镜）
  let fsActive = false;
  const fsHex = (r, g, b) => "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase();

  function closeFs() { fsActive = false; fsOv.hidden = true; }
  const screenBtn = $("screen-btn");
  function screenPick() {
    screenBtn.disabled = true; const old = screenBtn.textContent; screenBtn.textContent = "吸管启动中…";
    const reset = () => { screenBtn.disabled = false; screenBtn.textContent = old; };
    const oct = window.OCT;
    if (!oct || typeof oct.rpc !== "function") { toast("内核连接未就绪"); reset(); return; }
    toast("全屏吸管已启动：移动鼠标实时取色 · 单击选定 · Esc 取消");
    // 实时同步：轮询后端写出的 live.json，把“当前颜色”跟随光标实时刷新
    const pollTimer = setInterval(async () => {
      try {
        const r0 = await fetch("/plugin/color_pick/.cache/live.json?t=" + Date.now());
        const j = await r0.json();
        if (j && j.ok && j.color && j.color.length === 3) {
          color = { r: j.color[0], g: j.color[1], b: j.color[2] }; render();
        }
      } catch (e) { /* 文件尚未生成/被占用时静默 */ }
    }, 100);
    const stopPoll = () => clearInterval(pollTimer);
    oct.rpc("plugin.call", {
      pluginId: "color_pick", method: "colorpick.eyedrop", params: {}, timeoutMs: 600000
    }, 600000).then((wrap) => {
      stopPoll();
      const inner = (wrap && typeof wrap === "object" && wrap.result && wrap.result === "object")
        ? wrap.result : wrap;
      if (!inner || inner.ok === false) {
        if (inner && inner.cancelled) toast("已取消取色");
        else toast((inner && inner.error) || "吸管失败");
        reset(); return;
      }
      const c = inner.color;
      color = { r: c[0], g: c[1], b: c[2] }; render();
      toast("已取色 " + hex());
      reset();
    }).catch(() => { stopPoll(); toast("吸管已关闭"); reset(); });
  }

  function fsMoveTo(e) {
    if (!fsActive) return;
    const rect = fsCv.getBoundingClientRect();
    const cx = e.clientX, cy = e.clientY;
    if (cx < rect.left || cx > rect.right || cy < rect.top || cy > rect.bottom) {
      fsMag.style.display = "none"; return;
    }
    const bx = Math.min(fsCv.width - 1, Math.max(0, Math.round((cx - rect.left) / rect.width * fsCv.width)));
    const by = Math.min(fsCv.height - 1, Math.max(0, Math.round((cy - rect.top) / rect.height * fsCv.height)));
    const d = fsOff.getContext("2d").getImageData(bx, by, 1, 1).data;
    const r = d[0], g = d[1], b = d[2];
    // 放大镜
    const mx = Math.min(Math.max(bx, 12), fsCv.width - 12), my = Math.min(Math.max(by, 12), fsCv.height - 12);
    magCtx.clearRect(0, 0, 220, 220);
    magCtx.drawImage(fsOff, mx - 11, my - 11, 22, 22, 0, 0, 220, 220);
    magCtx.fillStyle = "rgba(0,0,0,.55)"; magCtx.fillRect(107, 107, 6, 6);
    magCtx.fillStyle = "#fff"; magCtx.fillRect(109, 109, 2, 2);
    // 放大镜位置（避开光标点）
    const W = fsStage.clientWidth, H = fsStage.clientHeight, sz = 100;
    let ml = cx + 18, mt = cy - sz / 2;
    if (ml + sz > W) ml = cx - sz - 18;
    ml = Math.max(0, ml); mt = Math.max(0, Math.min(mt, H - sz));
    fsMag.style.left = ml + "px"; fsMag.style.top = mt + "px"; fsMag.style.display = "block";
    fsBub.textContent = fsHex(r, g, b);
    fsBub.style.left = cx + "px"; fsBub.style.top = cy + "px";
  }

  fsStage.addEventListener("mousemove", fsMoveTo);
  fsStage.addEventListener("click", (e) => {
    if (!fsActive) return;
    const rect = fsCv.getBoundingClientRect();
    const cx = e.clientX, cy = e.clientY;
    if (cx < rect.left || cx > rect.right || cy < rect.top || cy > rect.bottom) return;
    const bx = Math.min(fsCv.width - 1, Math.max(0, Math.round((cx - rect.left) / rect.width * fsCv.width)));
    const by = Math.min(fsCv.height - 1, Math.max(0, Math.round((cy - rect.top) / rect.height * fsCv.height)));
    const d = fsOff.getContext("2d").getImageData(bx, by, 1, 1).data;
    color = { r: d[0], g: d[1], b: d[2] }; render();
    closeFs(); toast("已取色 " + fsHex(d[0], d[1], d[2]));
  });
  $("fs-cancel").onclick = closeFs;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !fsOv.hidden) closeFs(); });
  $("screen-btn").onclick = screenPick;

  // ── 解析手动输入（移植 _parse_color） ──
  function parseColor(text) {
    text = text.trim();
    let m = /^#?([0-9a-fA-F]{6})$/.exec(text);
    if (m) {
      try { const v = parseInt(m[1], 16); return { r: v >> 16 & 255, g: v >> 8 & 255, b: v & 255 }; }
      catch { return null; }
    }
    m = /rgb\s*\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*\)/.exec(text);
    if (m) {
      const [r, g, b] = [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
      if ([r, g, b].every(x => x >= 0 && x <= 255)) return { r, g, b };
    }
    return null;
  }

  function convertFromInput() {
    const text = $("input").value.trim();
    if (!text) return;
    const c = parseColor(text);
    if (!c) { toast("无法识别，请使用 #RRGGBB 或 rgb(R,G,B)"); return; }
    color = c; render();
  }

  // ── 组装格式行 + 复制 ──
  const list = $("fmt-list");
  fmts.forEach(([name]) => {
    const row = document.createElement("div");
    row.className = "fmt-row";
    row.innerHTML = `<span class="fmt-name">${name}</span><span class="fmt-val" title="点击复制"></span>`;
    row.querySelector(".fmt-val").addEventListener("click", () => copyText(row.querySelector(".fmt-val").textContent));
    list.appendChild(row);
  });

  function copyText(t) {
    const ok = () => toast("已复制 " + t);
    const fail = () => toast("复制失败");
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(t).then(ok).catch(() => execCopy(t, ok, fail));
    } else {
      execCopy(t, ok, fail);
    }
  }
  // execCommand 兜底：跨源 iframe / 非安全上下文下 clipboard API 可能不可用。
  function execCopy(t, ok, fail) {
    const ta = document.createElement("textarea");
    ta.value = t; ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); ok(); } catch { fail(); }
    ta.remove();
  }

  // 右键复制：色块/按钮/各格式值均在右键菜单拦截并复制
  const attachCopy = (el, getText) => {
    if (!el) return;
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); copyText(getText()); });
  };
  attachCopy($("swatch"), () => hex());
  attachCopy($("picker-btn"), () => hex());
  $("cur-hex").title = "右键复制十六进制";

  $("picker-btn").onclick = openPicker;
  $("convert-btn").onclick = convertFromInput;
  $("input").addEventListener("keydown", (e) => { if (e.key === "Enter") convertFromInput(); });

  render();
})();
