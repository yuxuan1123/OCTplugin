// ══════════════════════════════════════════════════════════
// ui.js —— md 管理页 + 编辑窗口（完整移植 OCTools plugins/md）。
// 后端调用统一经 window.OCT.callPlugin("md", method, params)，
// 响应取 wrapper.result（kernel 包一层 {ok,result}，内层才是后端返回）。
// ══════════════════════════════════════════════════════════
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);

  // ── Toast ──
  const toast = (msg, kind) => {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 2500);
  };

  // ── RPC ──
  const rpc = async (method, params) => {
    const wrapper = await window.OCT.callPlugin("md", method, params || {});
    return (wrapper && wrapper.result) || {};
  };

  // ── 自绘模态（iframe 内原生 prompt/confirm 可能被拦截）─────
  let _modals = null;
  function ensureModals() {
    if (_modals) return;
    const st = document.createElement("style");
    st.textContent =
      "._mask{position:fixed;inset:0;background:rgba(30,27,23,.4);z-index:120;display:flex;align-items:center;justify-content:center;}" +
      "._dlg{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:20px;width:400px;max-width:88vw;box-shadow:var(--shadow);}" +
      "._dlg h4{margin:0 0 14px;font-size:14px;letter-spacing:2px;color:var(--ink);}" +
      "._dlg ._body{color:var(--ink-soft);font-size:13px;line-height:1.7;margin-bottom:8px;}" +
      "._dlg input{width:100%;background:rgba(255,252,245,.6);border:1px solid var(--line);border-radius:9px;padding:9px 12px;color:var(--ink);font-family:var(--font);font-size:13px;outline:none;}" +
      "._dlg input:focus{border-color:var(--gold);}" +
      "._dlg ._row{display:flex;justify-content:flex-end;gap:8px;margin-top:16px;}";
    document.head.appendChild(st);
    _modals = document.createElement("div");
    document.body.appendChild(_modals);
  }
  function askText(title, def = "") {
    ensureModals();
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "_mask";
      box.innerHTML =
        '<div class="_dlg"><h4></h4><div class="_body"></div>' +
        '<input type="text" spellcheck="false"><div class="_row">' +
        '<button class="btn ghost mini" data-v="0">取消</button>' +
        '<button class="btn mini" data-v="1">确定</button></div></div>';
      box.querySelector("h4").textContent = title || "输入";
      box.querySelector(".input, input").value = def || "";
      box.querySelector("input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") finish(box.querySelector("input").value.trim());
        if (e.key === "Escape") remove();
      });
      const finish = (v) => { const r = !!v; remove(); resolve(r ? v : null); };
      const remove = () => { box.remove(); };
      box.addEventListener("mousedown", (e) => { if (e.target === box) { remove(); resolve(null); } });
      box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        if (b.getAttribute("data-v") === "1") finish(box.querySelector("input").value.trim());
        else { remove(); resolve(null); }
      }));
      _modals.appendChild(box);
      box.querySelector("input").focus();
    });
  }
  function askConfirm(msg, kind = "danger") {
    ensureModals();
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "_mask";
      box.innerHTML =
        '<div class="_dlg"><h4>确认</h4><div class="_body"></div><div class="_row">' +
        '<button class="btn ghost mini" data-v="0">取消</button>' +
        '<button class="btn mini ' + (kind === "danger" ? "danger" : "") + '" data-v="1">确定</button></div></div>';
      box.querySelector("._body").textContent = msg;
      const remove = () => box.remove();
      const finish = (v) => { remove(); resolve(!!v); };
      box.addEventListener("mousedown", (e) => { if (e.target === box) { remove(); resolve(false); } });
      box.querySelector("input") && box.querySelector("input").addEventListener("keydown", (e) => {
        if (e.key === "Escape") { remove(); resolve(false); }
      });
      box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () =>
        finish(b.getAttribute("data-v") === "1")));
      _modals.appendChild(box);
      box.querySelector('[data-v="1"]').focus();
    });
  }

  // ── 复制（clipboard API + execCommand 兜底）──────────────
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

  // ══════════════════════════════════════════════════════════
  //  管理页
  // ══════════════════════════════════════════════════════════
  let files = [];
  let currentPath = "";
  let searchKw = "";

  function selPath() { return currentPath; }

  // 以独立窗口打开编辑器：向宿主 postMessage，宿主 Electron 主进程创建新窗口。
  // 子窗口 editor.html 读取 ?mdEditor=1&path=&auth=&kport 后自连内核并加载文件。
  function openInWindow(path) {
    if (!path) return;
    const rel = "/plugin/md/ui/editor.html?mdEditor=1&path=" + encodeURIComponent(path);
    if (window.parent !== window) {
      window.parent.postMessage({
        type: "oct.openPluginWindow",
        path: rel,
        title: "Markdown 编辑器",
        width: 960,
        height: 720,
      }, "*");
    } else {
      toast("插件内无法直接开窗（请在宿主内使用）", "warn");
    }
  }

  async function refresh() {
    const res = await rpc("md.list");
    if (!res.ok) { toast("加载列表失败：" + (res.error || ""), "warn"); return; }
    files = res.files || [];
    $("dir-badge").textContent = res.dir || "";
    $("hint").textContent = "目录：" + (res.dir || "") + "　|　共 " + files.length + " 个 .md 文件（单击预览，双击/右键编辑）";
    renderList();
  }

  function filtered() {
    const kw = searchKw.trim().toLowerCase();
    if (!kw) return files;
    return files.filter((f) =>
      (f.name || "").toLowerCase().includes(kw) ||
      (f.title || "").toLowerCase().includes(kw));
  }

  function renderList() {
    const ul = $("file-list");
    const list = filtered();
    $("count-label").textContent = files.length + " 个文件";
    $("count-label2").textContent = list.length + " / " + files.length;
    if (!list.length) {
      ul.innerHTML = '<li class="empty">' + (searchKw ? "无匹配文件" : "（暂无 .md 文件）") + "</li>";
      return;
    }
    ul.innerHTML = "";
    list.forEach((f) => {
      const li = document.createElement("li");
      li.className = "item" + (f.path === currentPath ? " active" : "");
      li.dataset.path = f.path;
      li.innerHTML =
        '<div class="icon">MD</div>' +
        '<div class="meta">' +
        '<div class="name"><span class="nm"></span><span class="sz"></span></div>' +
        '<div class="sub"></div><div class="time"></div>' +
        "</div>";
      const nm = li.querySelector(".nm"); nm.textContent = f.name;
      nm.title = f.shortcut_path ? f.shortcut_path + " → " + f.path : f.path;
      li.querySelector(".sz").textContent = f.size_text || "";
      li.querySelector(".sub").textContent = f.title || "Markdown 文档";
      li.querySelector(".time").textContent = f.time_text || "";
      ul.appendChild(li);
    });
  }

  async function showPreview(path) {
    currentPath = path;
    renderList();
    if (!path) {
      $("preview").innerHTML = '<p style="color:var(--ink-faint);">从左侧选择一个 .md 文件查看预览</p>';
      $("btn-open-editor").disabled = true;
      return;
    }
    $("btn-open-editor").disabled = false;
    const res = await rpc("md.read", { path });
    if (!res.ok) { $("preview").innerHTML = "<p>读取失败：" + esc(res.error) + "</p>"; return; }
    $("preview").innerHTML = window.MD.render(res.content || "");
  }

  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // ══════════════════════════════════════════════════════════
  //  事件绑定
  // ══════════════════════════════════════════════════════════
  $("btn-refresh").onclick = refresh;
  $("search").addEventListener("input", () => { searchKw = $("search").value; renderList(); });

  $("btn-new").onclick = async () => {
    const name = await askText("新建 Markdown", "新建文档.md");
    if (!name) return;
    const body = "# " + name.replace(/\.md$/i, "") + "\n\n";
    const res = await rpc("md.create", { name, body });
    if (!res.ok) { toast(res.error || "创建失败", "warn"); return; }
    await refresh();
    openInWindow(res.path);
    toast("已新建 " + res.name, "ok");
  };

  $("btn-dir").onclick = async () => {
    const d = await askText("选择 md 目录（绝对路径）", $("dir-badge").textContent || "");
    if (!d) return;
    const res = await rpc("md.setDir", { dir: d });
    if (!res.ok) { toast("设置目录失败：" + (res.error || ""), "warn"); return; }
    toast("已切换目录", "ok");
    refresh();
  };

  // 文件列表
  $("file-list").addEventListener("click", (e) => {
    const li = e.target.closest(".item");
    if (li) showPreview(li.dataset.path);
  });
  $("file-list").addEventListener("dblclick", (e) => {
    const li = e.target.closest(".item");
    if (li) openInWindow(li.dataset.path);
  });
  $("file-list").addEventListener("contextmenu", async (e) => {
    e.preventDefault();
    const li = e.target.closest(".item");
    if (!li) return;
    const path = li.dataset.path;
    const name = li.querySelector(".nm").textContent;
    const m = document.createElement("div");
    m.style.cssText = "position:fixed;z-index:130;background:var(--paper);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);padding:6px;min-width:140px;";
    m.innerHTML = '<button class="btn ghost mini" style="display:flex;width:100%;margin-bottom:4px" data-a="open">新窗口编辑</button>' +
      '<button class="btn ghost mini" style="display:flex;width:100%;margin-bottom:4px" data-a="ren">刷新列表</button>' +
      '<div style="height:1px;background:var(--line);margin:4px 0"></div>' +
      '<button class="btn ghost mini danger" style="display:flex;width:100%" data-a="del">删除</button>';
    m.style.left = Math.min(e.clientX, window.innerWidth - 160) + "px";
    m.style.top = Math.min(e.clientY, window.innerHeight - 120) + "px";
    document.body.appendChild(m);
    const done = () => m.remove();
    document.addEventListener("click", done, { once: true });
    m.addEventListener("mousedown", (ev) => ev.stopPropagation());
    m.querySelector("[data-a=open]").onclick = () => { done(); openInWindow(path); };
    m.querySelector("[data-a=ren]").onclick = () => { done(); refresh(); };
    m.querySelector("[data-a=del]").onclick = async () => {
      done();
      const ok = await askConfirm("确定删除「" + name + "」？此操作不可恢复。");
      if (!ok) return;
      const r = await rpc("md.delete", { path });
      if (!r.ok) { toast("删除失败：" + (r.error || ""), "warn"); return; }
      if (currentPath === path) showPreview("");
      refresh();
      toast("已删除：" + name, "ok");
    };
  });

  $("btn-open-editor").onclick = () => openInWindow(selPath());

  // 代码块复制（事件委托，覆盖管理页预览）
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const pre = btn.closest(".code-block");
    copyText(pre ? pre.querySelector("pre").textContent : "");
  });

  // ── 启动：等待 OCT 桥就绪 ──
  function init() { refresh(); }
  if (window.OCT && window.OCT.callPlugin) init();
  else window.addEventListener("oct.ready", init);

  // 初始空预览提示
  $("preview").innerHTML = '<p style="color:var(--ink-faint);">从左侧选择一个 .md 文件查看预览</p>';
})();