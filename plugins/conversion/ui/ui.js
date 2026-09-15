// 转换页业务逻辑（纯前端，移植自 OCTools Conversion 页的换算口径）。
(() => {
  const $ = (id) => document.getElementById(id);
  const toast = (msg) => {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 1800);
  };

  // ── 单位库：线性类别统一换算到「基准单位」，温度单独按公式 ──
  // 结构：{ key:， name:， unit:， kind: "linear"|"temp" ， units: [ {name, sym, k} ] }
  // 线性类 k = 每「基准单位」？否 —— 采用「1 单位 = k × 基准单位」，
  // 因此：基准值 = value × from.k；结果 = 基准值 / to.k。
  const CATS = [
    { key:"length", name:"长度", kind:"linear", units:[
      {name:"毫米", sym:"mm", k:0.001},
      {name:"厘米", sym:"cm", k:0.01},
      {name:"米",   sym:"m",  k:1},
      {name:"千米", sym:"km", k:1000},
      {name:"英寸", sym:"in", k:0.0254},
      {name:"英尺", sym:"ft", k:0.3048},
      {name:"码",   sym:"yd", k:0.9144},
      {name:"英里", sym:"mi", k:1609.344},
      {name:"海里", sym:"nmi", k:1852},
    ]},
    { key:"area", name:"面积", kind:"linear", units:[
      {name:"平方毫米",  sym:"mm²", k:1e-6},
      {name:"平方厘米",  sym:"cm²", k:1e-4},
      {name:"平方米",    sym:"m²",  k:1},
      {name:"平方千米",  sym:"km²", k:1e6},
      {name:"平方英尺",  sym:"ft²", k:0.09290304},
      {name:"平方码",    sym:"yd²", k:0.83612736},
      {name:"亩",        sym:"亩",  k:666.6666667},
      {name:"公顷",      sym:"ha",  k:1e4},
      {name:"英亩",      sym:"acre",k:4046.8564224},
    ]},
    { key:"volume", name:"体积", kind:"linear", units:[
      {name:"毫升",   sym:"ml",   k:0.001},
      {name:"升",     sym:"L",    k:1},
      {name:"立方厘米",sym:"cm³", k:0.001},
      {name:"立方米", sym:"m³",   k:1000},
      {name:"立方英寸",sym:"in³", k:0.016387064},
      {name:"立方英尺",sym:"ft³", k:28.316846592},
      {name:"美制加仑",sym:"US gal", k:3.785411784},
      {name:"英制加仑",sym:"UK gal", k:4.54609},
    ]},
    { key:"mass", name:"重量", kind:"linear", units:[
      {name:"毫克",   sym:"mg",   k:0.001},
      {name:"克",     sym:"g",    k:1},
      {name:"千克",   sym:"kg",   k:1000},
      {name:"吨",     sym:"t",    k:1e6},
      {name:"盎司",   sym:"oz",   k:28.349523125},
      {name:"磅",     sym:"lb",   k:453.59237},
      {name:"两",     sym:"两",   k:50},
      {name:"斤",     sym:"斤",   k:500},
    ]},
    { key:"temp", name:"温度", kind:"temp", units:[
      {name:"摄氏度",   sym:"°C", k:"C"},
      {name:"华氏度",   sym:"°F", k:"F"},
      {name:"开尔文",   sym:"K",  k:"K"},
    ]},
    { key:"time", name:"时间", kind:"linear", units:[
      {name:"毫秒", sym:"ms",   k:0.001},
      {name:"秒",   sym:"s",    k:1},
      {name:"分钟", sym:"min",  k:60},
      {name:"小时", sym:"h",    k:3600},
      {name:"天",   sym:"d",    k:86400},
      {name:"周",   sym:"周",   k:604800},
      {name:"月",   sym:"月",   k:2592000},
      {name:"年",   sym:"年",   k:31536000},
    ]},
    { key:"speed", name:"速度", kind:"linear", units:[
      {name:"米/秒",     sym:"m/s",  k:1},
      {name:"千米/小时", sym:"km/h", k:1000/3600},
      {name:"英里/小时", sym:"mph",  k:0.44704},
      {name:"英尺/秒",   sym:"ft/s", k:0.3048},
      {name:"节",        sym:"kn",   k:0.514444},
    ]},
    { key:"data", name:"数据存储", kind:"linear", units:[
      {name:"比特",   sym:"bit",   k:0.125},
      {name:"字节",   sym:"B",     k:1},
      {name:"千字节", sym:"KB",    k:1000},
      {name:"兆字节", sym:"MB",    k:1e6},
      {name:"吉字节", sym:"GB",    k:1e9},
      {name:"太字节", sym:"TB",    k:1e12},
      {name:"千比字节",sym:"KiB",  k:1024},
      {name:"兆比字节",sym:"MiB",  k:1048576},
      {name:"吉比字节",sym:"GiB",  k:1073741824},
    ]},
  ];

  // ── 温度：统一换算到 °C 作为中间量，再转目标 ──
  // K 值：C→base 与 base→C 各不相同，故直接用公式
  function toBaseC(v, type) {
    switch (type) {
      case "C": return v;
      case "F": return (v - 32) * 5 / 9;
      case "K": return v - 273.15;
    }
  }
  function fromBaseC(v, type) {
    switch (type) {
      case "C": return v;
      case "F": return v * 9 / 5 + 32;
      case "K": return v + 273.15;
    }
  }

  let curCat = CATS[0];

  function renderUnits(sel, target) {
    const fromSel = $("from"), toSel = $("to");
    const a = curCat.units.map((u, i) => `<option value="${i}">${u.name}（${u.sym}）</option>`).join("");
    fromSel.innerHTML = a;
    toSel.innerHTML = a;
    // 默认目标单位 = 下一项（若无则当前项）
    toSel.selectedIndex = Math.min($("cat").selectedIndex === -1 ? 1 : (curCat.units.length > 1 ? 1 : 0), curCat.units.length - 1);
  }

  function fillCats() {
    const catSel = $("cat");
    catSel.innerHTML = CATS.map((c, i) => `<option value="${i}">${c.name}</option>`).join("");
    catSel.addEventListener("change", () => {
      curCat = CATS[catSel.selectedIndex];
      renderUnits();
      convert(true);
    });
  }

  function convert(showBlank) {
    const from = curCat.units[$("from").value | 0];
    const to = curCat.units[$("to").value | 0];
    const raw = $("value").value;
    if (raw === "" || isNaN(raw)) {
      if (showBlank) { $("result-val").textContent = "—"; $("result-empty").style.display = ""; }
      return;
    }
    const v = parseFloat(raw);
    let result;
    if (curCat.kind === "temp") {
      result = fromBaseC(toBaseC(v, from.k), to.k);
    } else {
      const base = v * from.k;
      result = base / to.k;
    }
    const txt = formatNum(result);
    $("result-val").textContent = txt;
    $("result-unit").textContent = to.name + "（" + to.sym + "）";
    $("result-empty").style.display = "none";

    // 全量表：当前数值在所有同类单位下
    renderTable(v);
  }

  function renderTable(v) {
    const tb = $("tbody");
    tb.innerHTML = "";
    const rows = curCat.units.map((u) => {
      const r = curCat.kind === "temp" ? fromBaseC(toBaseC(v, curryFrom().k), u.k) : (v * curryFrom().k / u.k);
      const txt = formatNum(r);
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="td-unit">${u.name}（${u.sym}）</td><td class="td-num" title="点击复制">${txt}</td>`;
      tr.querySelector(".td-num").addEventListener("click", () => copyText(txt));
      return tr;
    });
    rows.forEach((tr) => tb.appendChild(tr));
    $("table-empty").style.display = "none";
  }

  // 当前选中的「从单位」的 sym（供温度表用）
  let curryFrom = () => curCat.units[$("from").value | 0];

  function formatNum(n) {
    if (!isFinite(n)) return "∞";
    const neg = n < 0 ? "-" : "";
    const abs = Math.abs(n);
    if (abs !== 0 && (abs >= 1e9 || abs < 1e-6)) {
      return neg + abs.toExponential(6);
    }
    // 保留最多 8 位小数，去掉末尾多余的 0
    let s = neg + abs.toFixed(8);
    if (s.indexOf(".") !== -1) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s;
  }

  // ── 复制 ──
  function copyText(t) {
    const ok = () => toast("已复制 " + t);
    const fail = () => toast("复制失败");
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(t).then(ok).catch(() => execCopy(t, ok, fail));
    } else {
      execCopy(t, ok, fail);
    }
  }
  function execCopy(t, ok, fail) {
    const ta = document.createElement("textarea");
    ta.value = t; ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); ok(); } catch { fail(); }
    ta.remove();
  }

  // ── 事件绑定 ──
  fillCats();
  $("from").addEventListener("change", () => convert(true));
  $("to").addEventListener("change", () => convert(true));
  $("value").addEventListener("input", () => { if ($("value").value !== "") convert(false); });
  $("value").addEventListener("keydown", (e) => { if (e.key === "Enter") convert(true); });
  $("convert-btn").addEventListener("click", () => convert(true));
  $("result-val").addEventListener("click", () => copyText($("result-val").textContent));
  $("copy-btn").addEventListener("click", () => copyText($("result-val").textContent));

  renderUnits();
})();