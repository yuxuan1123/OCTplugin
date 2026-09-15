(() => {
  const $ = (id) => document.getElementById(id);
  const toastEl = $("toast");
  let toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1800);
  }

  // ── 状态 ─────────────────────────────
  const state = { files: null, rootName: "" };

  const IMAGE_EXT = new Set([".jpg",".jpeg",".png",".gif",".bmp",".webp",".svg",".ico",".tiff",".tif",".heic",".heif",".avif"]);

  function readOptions() {
    const d = parseInt($("depth").value) || 0;
    return {
      maxDepth: d,
      showHidden: $("show-hidden").checked,
      fileFilter: $("filter").value,
      excludeExt: parseExt($("exclude-ext").value),
      keywords: parseKw($("exclude-kw").value)
    };
  }
  function parseExt(raw) {
    const out = new Set();
    String(raw || "").split(",").forEach(s => {
      s = (s || "").trim().toLowerCase();
      if (!s) return;
      out.add(s.startsWith(".") ? s : "." + s);
    });
    return out;
  }
  function parseKw(raw) {
    const out = [];
    String(raw || "").split(",").forEach(s => {
      s = (s || "").trim().toLowerCase();
      if (s) out.push(s);
    });
    return out;
  }

  // ── 前端：从 webkitDirectory 选中的文件构建目录树（无后端） ──
  function buildFromFiles(files, opts) {
    const rootName = files.length ? files[0].webkitRelativePath.split("/")[0] : "根目录";
    const nodes = {};   // path -> node
    const root = { name: rootName, children: [] };
    nodes[rootName] = root;

    files.forEach(f => {
      const parts = f.webkitRelativePath.split("/");
      let node = root, key = parts[0], prefix = rootName;
      for (let i = 1; i < parts.length; i++) {
        key += "/" + parts[i];
        // 在 node.children 中查找/新建子节点
        let child = node.children.find(c => c.name === parts[i]);
        if (!child) {
          child = { name: parts[i], children: [] };
          node.children.push(child);
          nodes[key] = child;
        }
        node = child;
      }
    });
    return renderTree(root, opts);
  }

  function isDirNode(node) {
    return node.children && node.children.length > 0;
  }

  function shouldSkip(node, opts) {
    const name = node.name;
    if (!opts.showHidden && name.startsWith(".")) return true;
    const lower = name.toLowerCase();
    for (const kw of opts.keywords) if (lower.indexOf(kw) !== -1) return true;
    if (isDirNode(node)) return false;
    const dot = name.lastIndexOf(".");
    const ext = dot > 0 ? name.slice(dot).toLowerCase() : "";
    if (opts.fileFilter === "all") return true;
    if (opts.fileFilter === "images" && IMAGE_EXT.has(ext)) return true;
    if (opts.excludeExt.has(ext)) return true;
    return false;
  }

  function renderTree(root, opts) {
    const lines = [root.name || "根目录"];
    const maxDepth = opts.maxDepth > 0 ? opts.maxDepth : Infinity;
    function walk(node, prefix, depth) {
      const vis = node.children.filter(c => !shouldSkip(c, opts));
      vis.forEach((ch, idx) => {
        const isLast = idx === vis.length - 1;
        const isDir = isDirNode(ch);
        lines.push(prefix + (isLast ? "└── " : "├── ") + ch.name + (isDir ? "/" : ""));
        if (isDir && depth < maxDepth) {
          walk(ch, prefix + (isLast ? "    " : "│   "), depth + 1);
        }
      });
    }
    walk(root, "", 1);
    return lines.join("\n");
  }

  // ── 渲染输出 ──
  let currentText = "";
  function setOutput(text, statText) {
    currentText = text;
    $("out").textContent = text;
    $("stat").textContent = statText || (text ? "已生成 " + text.split("\n").length + " 行。" : "尚未生成。");
  }

  // ── 复制（clipboard + execCommand 兜底） ──
  async function copy(text) {
    if (!text) return toast("没有可复制的内容");
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到剪贴板");
    } catch (e) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        toast("已复制到剪贴板");
      } catch (e2) { toast("复制失败，请手动选择文本"); }
    }
  }

  // ── 事件绑定 ──
  $("folder-btn").addEventListener("click", () => $("dir-input").click());
  $("dir-input").addEventListener("change", (ev) => {
    const files = ev.target.files;
    if (!files || !files.length) return;
    state.files = Array.from(files);
    state.rootName = state.files[0].webkitRelativePath.split("/")[0];
    $("folder-name").textContent = "已选择文件夹：" + state.rootName + "（" + state.files.length + " 个文件）";
    $("path").value = "";
  });

  function generate() {
    const opts = readOptions();
    if (state.files && state.files.length) {
      setOutput(buildFromFiles(state.files, opts));
    } else {
      const path = $("path").value.trim();
      if (path) scanByPath(path, opts);
      else toast("请先「选择文件夹」或输入根目录路径");
    }
  }
  $("gen-btn").addEventListener("click", generate);
  $("scan-btn").addEventListener("click", () => {
    const path = $("path").value.trim();
    if (!path) return toast("请先输入根目录路径");
    scanByPath(path, readOptions());
  });

  async function scanByPath(path, opts) {
    // §3.4：收到 {ok:true,result:<内层>}，取 .result 判断 .ok
    let r;
    try {
      r = await window.OCT.callPlugin("tree", "tree.scan", {
        root: path, maxDepth: opts.maxDepth, ignoreHidden: !opts.showHidden
      });
    } catch (e) { return toast("后端扫描失败：" + e.message); }
    if (!r || !r.ok) return toast("后端扫描失败");
    const text = (typeof r.result === "string") ? r.result : JSON.stringify(r.result);
    setOutput(text, "已生成 " + text.split("\n").length + " 行（后端扫描）。");
  }

  $("copy-btn").addEventListener("click", () => copy(currentText));
  window.addEventListener("oct.error", (e) => toast(String(e.detail)));
})();
