// ══════════════════════════════════════════════════════════
// render.js —— Markdown → HTML（完整移植自 OCTools md/md_render.py）
// 覆盖：标题/段落/有序无序列表/引用/代码块(带语言+复制按钮)/表格/分割线/
// 行内代码/链接/图片/加粗/斜体/删除线/行内数学 $...$/块公式 $$...$$
// 公式用轻量 LaTeX→HTML 转换器（分数/上下标/希腊字母/符号/根式/矩阵等）。
// 暴露 window.MD.render(md) -> HTML 字符串。纯前端，无任何依赖。
// ══════════════════════════════════════════════════════════
(() => {
  "use strict";

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  // ── 数学样式（国风主题变量，内联以适配任意容器）────────────
  const MATH_INLINE = "font-family:'Cambria Math',serif;font-size:15px;";
  const MATH_BLOCK = "font-family:'Cambria Math',serif;font-size:16px;text-align:center;margin:12px 0;padding:10px 14px;background:var(--paper-2);border-radius:8px;";

  // ── LaTeX → HTML ─────────────────────────────────────────
  const GREEK = {
    "\\alpha":"α","\\beta":"β","\\gamma":"γ","\\delta":"δ","\\epsilon":"ε",
    "\\zeta":"ζ","\\eta":"η","\\theta":"θ","\\iota":"ι","\\kappa":"κ",
    "\\lambda":"λ","\\mu":"μ","\\nu":"ν","\\xi":"ξ","\\pi":"π","\\rho":"ρ",
    "\\sigma":"σ","\\tau":"τ","\\upsilon":"υ","\\phi":"φ","\\chi":"χ",
    "\\psi":"ψ","\\omega":"ω","\\Gamma":"Γ","\\Delta":"Δ","\\Theta":"Θ",
    "\\Lambda":"Λ","\\Xi":"Ξ","\\Pi":"Π","\\Sigma":"Σ","\\Upsilon":"Υ",
    "\\Phi":"Φ","\\Psi":"Ψ","\\Omega":"Ω",
  };
  const SYMBOLS = {
    "\\leq":"≤","\\geq":"≥","\\neq":"≠","\\approx":"≈","\\equiv":"≡","\\sim":"∼",
    "\\propto":"∝","\\infty":"∞","\\cdot":"·","\\times":"×","\\div":"÷",
    "\\pm":"±","\\mp":"∓","\\circ":"∘","\\ast":"∗","\\star":"★","\\sum":"∑",
    "\\prod":"∏","\\coprod":"∐","\\int":"∫","\\iint":"∬","\\iiint":"∭",
    "\\oint":"∮","\\partial":"∂","\\nabla":"∇","\\aleph":"ℵ","\\forall":"∀",
    "\\exists":"∃","\\nexists":"∄","\\in":"∈","\\notin":"∉","\\subset":"⊂",
    "\\supset":"⊃","\\subseteq":"⊆","\\supseteq":"⊇","\\emptyset":"∅",
    "\\angle":"∠","\\perp":"⊥","\\parallel":"∥","\\prime":"′","\\deg":"°",
    "\\leftarrow":"←","\\rightarrow":"→","\\Leftarrow":"⇐","\\Rightarrow":"⇒",
    "\\leftrightarrow":"↔","\\Leftrightarrow":"⇔","\\uparrow":"↑","\\downarrow":"↓",
    "\\mapsto":"↦","\\longmapsto":"⟼",
  };

  function latexFrac(body) {
    let start = body.indexOf("{");
    if (start < 0) return body;
    let depth = 0, i = start;
    for (; i < body.length; i++) {
      if (body[i] === "{") depth++;
      else if (body[i] === "}") { depth--; if (depth === 0) break; }
    }
    const num = body.slice(start + 1, i);
    const rest = body.slice(i + 1);
    const s2 = rest.indexOf("{");
    if (s2 < 0) return body;
    depth = 0; let j = s2;
    for (; j < rest.length; j++) {
      if (rest[j] === "{") depth++;
      else if (rest[j] === "}") { depth--; if (depth === 0) break; }
    }
    const den = rest.slice(s2 + 1, j);
    return "<sup>" + latexToHtml(num) + "</sup>/<sub>" + latexToHtml(den) + "</sub>";
  }

  function latexSqrt(body) {
    let m = body.match(/^\\sqrt\[(\w+)\]\{([\s\S]*)\}/);
    if (m) return "<sup>" + m[1] + "</sup>√(" + latexToHtml(m[2]) + ")";
    m = body.match(/^\\sqrt\{([\s\S]*)\}/);
    if (m) return "√(" + latexToHtml(m[1]) + ")";
    return body;
  }

  function latexMatrix(body) {
    let inner = body.replace(/^\\begin\{[a-z]+\}/, "");
    inner = inner.replace(/\\end\{[a-z]+\}$/, "").trim();
    if (!inner) return "";
    const rows = inner.split("\\\\").map((row) =>
      row.split("&").map((c) => latexToHtml(c.trim())));
    const ncols = Math.max(...rows.map((r) => r.length));
    const trs = rows.map((row) => {
      const cells = row.concat(new Array(Math.max(0, ncols - row.length)).fill(""));
      return "<tr>" + cells.map((c) => "<td style='padding:2px 6px;text-align:center'>" + c + "</td>").join("") + "</tr>";
    }).join("");
    return "<span style='display:inline-block;vertical-align:middle;border-left:2px solid #666;border-right:2px solid #666;padding:2px 4px;'><table cellspacing='0'>" + trs + "</table></span>";
  }

  function latexSupsub(body) {
    const re = /([a-zA-Z]\)?)(?:\^(\{[^}]+\}|\w+))?(?:_(\{[^}]+\}|\w+))?/g;
    return body.replace(re, (m, prefix, sup, sub) => {
      if (!sup && !sub) return m;
      let out = "";
      // prefix 可能是单个字符或 "x)"，保留原样
      out = prefix;
      if (sup) out += "<sup>" + latexToHtml(sup.match(/^\{/)?sup.slice(1, -1):sup) + "</sup>";
      if (sub) out += "<sub>" + latexToHtml(sub.match(/^\{/)?sub.slice(1, -1):sub) + "</sub>";
      return out;
    });
  }

  function latexToHtml(src) {
    if (!src) return "";
    let body = String(src).trim();

    // 1. 矩阵
    body = body.replace(/\\begin\{(pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix)\}([\s\S]*?)\\end\{\1\}/g, (m) => latexMatrix(m));

    // 2. \frac
    let guard = 0;
    while (body.indexOf("\\frac") >= 0 && guard++ < 20) {
      const next = latexFrac(body);
      if (next === body) break;
      body = next;
    }

    // 3. \sqrt
    guard = 0;
    while (body.indexOf("\\sqrt") >= 0 && guard++ < 20) {
      const next = latexSqrt(body);
      if (next === body) break;
      body = next;
    }

    // 4. 希腊字母 + 符号（按长度降序，避免短命令先匹配）
    const all = Object.keys(GREEK).concat(Object.keys(SYMBOLS)).sort((a, b) => b.length - a.length);
    for (const cmd of all) {
      body = body.split(cmd).join(GREEK[cmd] || SYMBOLS[cmd]);
    }

    // 5. \text{...} / \operatorname{...}
    body = body.replace(/\\text\{([^}]+)\}/g, "$1");
    body = body.replace(/\\operatorname\{([^}]+)\}/g, "$1");

    // 6. \overline{x} → x̄
    body = body.replace(/\\overline\{([^}]+)\}/g, (_, s) => s + "\u0305");

    // 7. 上下标
    body = latexSupsub(body);

    // 8. 去除剩余 \
    body = body.replace(/\\/g, "");

    // 9. 合并连续空格
    body = body.replace(/ +/g, " ");

    return body.trim();
  }

  // $...$ 行内（跳过 $$...$$）
  function renderMathInline(mdText) {
    const out = [];
    let i = 0, n = mdText.length;
    while (i < n) {
      if (mdText.slice(i, i + 2) === "$$") {
        const j = mdText.indexOf("$$", i + 2);
        if (j > 0) { out.push(mdText.slice(i, j + 2)); i = j + 2; continue; }
      }
      if (mdText[i] === "$" && (i === 0 || mdText[i - 1] !== "\\")) {
        const j = mdText.indexOf("$", i + 1);
        if (j > 0 && mdText[j - 1] !== "\\") {
          out.push('<span style="' + MATH_INLINE + '">' + latexToHtml(mdText.slice(i + 1, j)) + "</span>");
          i = j + 1;
          continue;
        }
      }
      out.push(mdText[i]);
      i++;
    }
    return out.join("");
  }

  function renderMathBlock(mdText) {
    return mdText.replace(/\$\$([\s\S]+?)\$\$/g, (_, l) =>
      '<div style="' + MATH_BLOCK + '">' + latexToHtml(l) + "</div>");
  }

  // ── 行内解析 ─────────────────────────────────────────────
  function inline(text) {
    let s = esc(text);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img src="$2" alt="$1">');
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    s = renderMathInline(s);
    return s;
  }

  // ── 块级解析（行状态机）──────────────────────────────────
  function renderBlock(lines) {
    const out = [];
    let i = 0, n = lines.length;
    let para = [];
    const flush = () => {
      if (para.length) {
        out.push("<p>" + inline(para.join(" ")) + "</p>");
        para = [];
      }
    };
    while (i < n) {
      const line = lines[i];

      // 围栏代码块
      if (/^\s*```/.test(line)) {
        flush();
        const lang = (/^```(\w*)/.exec(line) || [])[1] || "";
        const code = [];
        i++;
        while (i < n && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i++; }
        i++;
        const codeText = code.join("\n");
        out.push('<div class="code-block">' +
          '<div class="code-head"><span>' + esc(lang || "代码") + "</span>" +
          '<button class="btn mini" data-copy>复制</button></div>' +
          "<pre>" + esc(codeText) + "</pre></div>");
        continue;
      }

      // 块公式 $$...$$（独占整行）
      const stripped = line.trim();
      if (stripped.startsWith("$$") && stripped.endsWith("$$") && stripped.length >= 4) {
        flush();
        out.push('<div style="' + MATH_BLOCK + '">' + latexToHtml(stripped.slice(2, -2).trim()) + "</div>");
        i++;
        continue;
      }

      if (!line.trim()) { flush(); i++; continue; }

      // 标题
      const hm = line.match(/^(#{1,6})\s+(.*)$/);
      if (hm) {
        flush();
        const lv = hm[1].length;
        out.push("<h" + lv + ">" + inline(hm[2]) + "</h" + lv + ">");
        i++;
        continue;
      }

      // 分割线
      if (/^\s*([-*_])(\s*\1\s*){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }

      // 引用
      if (/^\s*>/.test(line)) {
        flush();
        const quoted = [];
        while (i < n && /^\s*>/.test(lines[i])) {
          quoted.push((/^\s*>\s?/.exec(lines[i]) || [""])[0] ? lines[i].replace(/^\s*>\s?/, "") : lines[i]);
          i++;
        }
        out.push("<blockquote>" + renderBlock(quoted) + "</blockquote>");
        continue;
      }

      // 表格
      if (line.indexOf("|") >= 0 && i + 1 < n &&
          /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
        flush();
        const cells = (row) => row.replace(/^\s*\|/, "").split("|").map((c) => c.trim());
        const head = cells(line);
        i += 2;
        const body = [];
        while (i < n && lines[i].indexOf("|") >= 0) { body.push(cells(lines[i])); i++; }
        const th = head.map((h) => "<th>" + inline(h) + "</th>").join("");
        const trs = body.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("");
        out.push('<table cellpadding="0" cellspacing="0" width="100%"><thead><tr>' + th +
          "</tr></thead><tbody>" + trs + "</tbody></table>");
        continue;
      }

      // 列表
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        flush();
        const ordered = /^\s*\d+[.)]/.test(line);
        const items = [];
        while (i < n && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          const indent = lines[i].length - lines[i].replace(/^\s*/, "").length;
          const mm = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]);
          items.push({ indent, text: mm[2] });
          i++;
        }
        const tag = ordered ? "ol" : "ul";
        const lis = items.map((it) =>
          '<li style="margin-left:' + ((it.indent / 2) * 20) + 'px">' + inline(it.text) + "</li>").join("");
        out.push("<" + tag + ">" + lis + "</" + tag + ">");
        continue;
      }

      para.push(line);
      i++;
    }
    flush();
    return out.join("");
  }

  function render(md) {
    const lines = (md || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const body = renderBlock(lines);
    return body.trim() ? body : '<p style="color:var(--ink-faint);">（空文档）</p>';
  }

  window.MD = { render };
})();