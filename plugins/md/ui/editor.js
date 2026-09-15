// ══════════════════════════════════════════════════════════
// editor.js —— 独立窗口 Markdown 编辑器（新窗口，非 iframe 重叠层）。
// 复用 app.js（mdEditor 查询模式下自连内核）与 render.js。
// 通过 ?mdEditor=1&path=<encodeURIComponent(绝对路径)> 指定要编辑的文件。
// ══════════════════════════════════════════════════════════
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const toast = (msg, kind) => {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 2500);
  };

  const q = new URLSearchParams(location.search);
  const FILE_PATH = q.get("path") || "";
  const NAME = FILE_PATH.split(/[\\/]/).pop() || "";

  const rpc = async (method, params) => {
    const wrapper = await window.OCT.callPlugin("md", method, params || {});
    return (wrapper && wrapper.result) || {};
  };

  const state = { path: FILE_PATH, original: "", dirty: false, view: "split" };

  function copyText(text, okMsg) {
    const ok = () => toast(okMsg || "已复制", "ok");
    const fail = () => toast("复制失败", "warn");
    const exec = () => {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); ok(); } catch { fail(); }
      ta.remove();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok).catch(exec);
    } else exec();
  }

  function current() { return $("source").value; }

  function refresh() {
    const text = current();
    state.dirty = text !== state.original;
    $("status").textContent = state.dirty ? "● 未保存" : "✓ 已保存";
    $("status").className = "status " + (state.dirty ? "dirty" : "saved");
    $("count").textContent = text.length + " 字";
    if (state.view !== "edit") $("preview").innerHTML = window.MD.render(text);
  }

  function setView(v) {
    state.view = v;
    $("editPane").style.display = (v === "split" || v === "edit") ? "flex" : "none";
    $("prevPane").style.display = (v === "split" || v === "preview") ? "flex" : "none";
    document.querySelectorAll(".view").forEach((b) => b.classList.toggle("active", b.id === "v-" + v));
    if (v === "preview") $("preview").innerHTML = window.MD.render(current());
  }

  function wrapSelection(marker) {
    const t = $("source"), s = t.selectionStart, e0 = t.selectionEnd;
    const sel = t.value.slice(s, e0) || "文字";
    t.value = t.value.slice(0, s) + marker + sel + marker + t.value.slice(e0);
    t.selectionStart = s + marker.length;
    t.selectionEnd = s + marker.length + sel.length;
    t.focus();
    t.dispatchEvent(new Event("input"));
  }

  async function save() {
    const res = await rpc("md.save", { path: state.path, content: current() });
    if (!res.ok) { toast("保存失败：" + (res.error || ""), "warn"); return; }
    state.original = current();
    refresh();
    toast("已保存：" + $("name").value, "ok");
  }

  async function saveAs() {
    const def = state.path.replace(/\.[^.]+$/, "") + "-副本.md";
    const name = prompt("另存为（同目录）", def.split(/[\\/]/).pop());
    if (!name) return;
    const idx = Math.max(state.path.lastIndexOf("/"), state.path.lastIndexOf("\\"));
    const newPath = state.path.slice(0, idx + 1) + name;
    const res = await rpc("md.save", { path: newPath, content: current() });
    if (!res.ok) { toast("另存失败：" + (res.error || ""), "warn"); return; }
    state.path = newPath;
    $("name").value = name;
    document.title = name + " · Markdown";
    toast("已另存为 " + name, "ok");
  }

  async function renameEditor() {
    const newName = $("name").value.trim();
    if (!newName || newName === NAME) { $("name").value = NAME; return; }
    const res = await rpc("md.rename", { path: state.path, newName });
    if (!res.ok) { toast("重命名失败：" + (res.error || ""), "warn"); $("name").value = NAME; return; }
    state.path = res.path;
    $("path").textContent = res.path;
    document.title = res.name + " · Markdown";
    toast("已重命名为 " + res.name, "ok");
  }

  async function del() {
    if (!confirm('确定删除「' + $("name").value + '」？此操作不可恢复。')) return;
    const res = await rpc("md.delete", { path: state.path });
    if (!res.ok) { toast("删除失败：" + (res.error || ""), "warn"); return; }
    toast("已删除", "ok");
    window.close();
  }

  // 已从地址栏拿到 file path → 先显示文件名/路径（即使读失败也有名字）。
  function showMeta() {
    $("name").value = NAME;
    $("path").textContent = FILE_PATH;
    document.title = (NAME || "未命名") + " · Markdown";
  }

  async function load(tryN) {
    if (!FILE_PATH) {
      toast("未收到文件路径，请在宿主内重新打开", "warn");
      return;
    }
    // 每次先同步显示文件名（不依赖后端回调）
    showMeta();
    const res = await rpc("md.read", { path: FILE_PATH });
    if (!res.ok) {
      // 插件可能刚启动尚未就绪（load_mode 竞态）：短暂退避后重试几次
      const n = tryN || 0;
      if (n < 4) {
        setTimeout(() => load(n + 1), 300);
        return;
      }
      toast("打开失败：" + (res.error || "未知错误"), "warn");
      return;
    }
    state.original = res.content || "";
    $("source").value = state.original;
    state.dirty = false;
    refresh();
    toast("已打开", "ok");
  }

  // 事件
  $("source").addEventListener("input", refresh);
  $("name").addEventListener("change", renameEditor);
  $("name").addEventListener("keydown", (e) => { if (e.key === "Enter") $("name").blur(); if (e.key === "Escape") { $("name").value = NAME; $("name").blur(); } });
  $("save").onclick = save;
  $("save-as").onclick = saveAs;
  $("delete").onclick = del;
  document.querySelectorAll(".view").forEach((b) => b.addEventListener("click", () => setView(b.id.replace("v-", ""))));

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const pre = btn.closest(".code-block");
    copyText(pre ? pre.querySelector("pre").textContent : "");
  });

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey) {
      const k = e.key.toLowerCase();
      if (k === "s") { e.preventDefault(); save(); return; }
      if (k === "b") { e.preventDefault(); wrapSelection("**"); return; }
      if (k === "i") { e.preventDefault(); wrapSelection("*"); return; }
    }
  });

  // 文件名/路径只要从地址栏拿到就先显示（即使后续连接或读取失败也不至于全空）
  showMeta();

  // 必须等 oct.ready（app.js 派发，WS 已连上）再读文件；
  // 不能凭 window.OCT 存在就立即 load —— 此刻 WS 往往还没 open，rpc 会立刻失败。
  window.addEventListener("oct.ready", load);
})();