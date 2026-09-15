// merge 插件页逻辑。桥在 app.js（window.OCT.callPlugin）。
// plugin.call 返回 {ok, result:<内层>}，须取 .result 判断内层 .ok（见 §3.4）。
(() => {
  const $ = (id) => document.getElementById(id);

  const pathInput = $("path-input");
  const contentArea = $("content-area");
  const optNewline = $("opt-newline");
  const optTimestamp = $("opt-timestamp");
  const prefixInput = $("prefix-input");
  const mergeBtn = $("merge-btn");
  const refreshBtn = $("refresh-btn");
  const pickBtn = $("pick-btn");
  const pickFile = $("pick-file");
  const logBox = $("log-box");
  const toastEl = $("toast");

  let toastTimer = null;
  function toast(msg, isErr) {
    toastEl.textContent = msg;
    toastEl.classList.toggle("err", !!isErr);
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3200);
  }

  function logLine(msg) {
    logBox.textContent += (logBox.textContent && logBox.textContent !== "等待操作…" ? "\n" : "") + msg;
    logBox.scrollTop = logBox.scrollHeight;
  }
  function setLog(msg) { logBox.textContent = msg; logBox.scrollTop = logBox.scrollHeight; }

  function targetPath() { return (pathInput.value || "").trim(); }

  // 调用后端并正确解包 {ok, result}，返回内层 result；失败抛错。
  async function backend(method, params) {
    const ok = await window.OCT.callPlugin("merge", method, params); // {ok, result}
    const r = ok && ok.result;
    if (!ok || !r || r.ok === false) {
      throw new Error((r && r.error) || "请求失败：" + method);
    }
    return r;
  }

  // 读取目标文件内容并刷新预览区
  async function refreshPreview() {
    const path = targetPath();
    if (!path) { setLog("请在“目标文件”填写绝对路径。"); return false; }
    logLine("加载 → " + path);
    const r = await backend("merge.read", { path });
    if (r.content == null) setLog("目标文件当前内容：（空 / 无法预览）");
    else setLog(r.content || "目标文件当前内容：（空）");
    return true;
  }

  // 追加/合并
  async function doMerge() {
    const path = targetPath();
    const content = contentArea.value;
    if (!path) return toast("请填写目标文件绝对路径", true);
    if (!content) return toast("请在文本框填入要合并的内容", true);

    mergeBtn.disabled = true;
    try {
      const opts = {
        newline: optNewline.checked,
        timestamp: optTimestamp.checked,
        prefix: (prefixInput.value || "").trim()
      };
      const r = await backend("merge.append", { path, content, opts });
      toast("已追加 " + String(r.count) + " 字符到 " + path);
      logLine("✓ 已写入 " + String(r.count) + " 字符 → " + path);
      try {
        const pr = await window.OCT.callPlugin("merge", "merge.read", { path });
        const rr = pr && pr.result;
        if (rr && rr.ok) { setLog(rr.content || "（空）"); }
      } catch (e) { /* 预览失败不阻塞 */ }
      contentArea.focus();
    } catch (err) {
      toast("合并失败：" + (err && err.message), true);
      logLine("✗ " + (err && err.message));
    } finally {
      mergeBtn.disabled = false;
    }
  }

  // 选文件：预览现有文件内容（追加始终写回 path-input 填的绝对路径）
  pickFile.addEventListener("change", () => {
    const f = pickFile.files && pickFile.files[0];
    if (!f) return;
    const fp = f.path || "";
    if (fp) { pathInput.value = fp; }
    const reader = new FileReader();
    reader.onload = () => {
      setLog("选择文件预览（追加仍写回上方路径）：\n" + (reader.result || ""));
    };
    reader.readAsText(f, "utf-8");
    pickFile.value = "";
  });
  pickBtn.addEventListener("click", () => pickFile.click());

  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    try { await refreshPreview(); }
    catch (e) { toast("读取失败：" + e.message, true); }
    finally { refreshBtn.disabled = false; }
  });
  mergeBtn.addEventListener("click", doMerge);

  window.addEventListener("oct.ready", () => {
    toast("合并插件已就绪");
  });
})();
