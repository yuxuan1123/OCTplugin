// 翻译插件前端：选目标语种 → 输原文 → 翻译 → 展示结果 + 复制。
// §3.4：callPlugin 返回 {ok, result:<内层>}，须取 result 判断内层 ok。
(() => {
  const $ = id => document.getElementById(id);
  const src = $("src"), res = $("res"), toSel = $("to"), toast = $("toast");

  // 常见语种（auto=自动检测；其后为「译成」目标）
  const LANGS = { auto:"自动检测", zh:"中文", en:"英文", ja:"日文", ko:"韩文",
                  fr:"法语", de:"德语", ru:"俄语", es:"西班牙语" };
  let busy = false;
  let toastTimer = null;

  function showToast(msg){
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
  }

  function fillLangs(map){
    toSel.innerHTML = "";
    for (const k of Object.keys(map)) {
      const opt = document.createElement("option");
      opt.value = k; opt.textContent = map[k];
      toSel.appendChild(opt);
    }
    toSel.value = "zh"; // 默认译成中文
  }
  fillLangs(LANGS);

  // 复制：navigator.clipboard 优先，跨源/iframe 下 execCommand 兜底
  function copyText(text){
    if (!text) return false;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
  }
  function fallbackCopy(text){
    try {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      ok || showToast("复制失败");
    } catch (e) { showToast("复制失败"); }
  }

  function setSourceTag(source){
    const tag = $("src-tag");
    tag.innerHTML = '<span class="dot"></span>' + (LANGS[source] ? LANGS[source] : ("源语种 " + source));
  }

  async function doTranslate(){
    const text = src.value.trim();
    if (!text) { showToast("请先输入要翻译的文本"); src.focus(); return; }
    const to = toSel.value || "zh";
    const btn = $("translate-btn");
    if (busy) return;
    busy = true; btn.disabled = true; btn.textContent = "翻译中…";
    const label = LANGS[to] || to;
    setSourceTag("auto");
    try {
      const wrapped = await window.OCT.callPlugin("translation", "translation.translate", { text, to });
      // §3.4 解包：结构 {ok, result:<内层>}，判断内层 ok
      const inner = (wrapped && wrapped.ok) ? wrapped.result : null;
      if (inner && inner.ok) {
        res.value = inner.result.text;
        setSourceTag(inner.result.source);
      } else {
        const msg = (inner && inner.error) || (wrapped && wrapped.error) || "翻译失败";
        showToast(msg);
        res.value = "";
      }
    } catch (e) {
      showToast("翻译失败：" + (e.message || e));
      res.value = "";
    } finally {
      busy = false; btn.disabled = false; btn.textContent = "翻 译";
    }
  }

  $("translate-btn").addEventListener("click", doTranslate);
  $("clear-btn").addEventListener("click", () => { src.value = ""; src.focus(); });
  $("copy-btn").addEventListener("click", () => {
    if (!res.value) { showToast("暂无译文可复制"); return; }
    copyText(res.value);
    showToast("已复制译文");
  });
  // Ctrl/Cmd + Enter 快速翻译
  src.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") doTranslate(); });
})();