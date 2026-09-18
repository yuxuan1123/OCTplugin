/*
 * conversion/apps/sharp_worker.js
 * ────────────────────────────────
 * sharp 渲染子进程（独立 Node 进程，局部 node_modules）。
 *
 * 由 Python 侧 apps/sharp_client.py 懒启动（仅当真正执行 SVG→位图 / 图像
 * 转码时才 spawn），通过 stdin/stdout 收发 JSON 行：
 *   ← stdin   {id, method, params}
 *   → stdout  {id, ok, result?} / {id, ok:false, error}
 *
 * 方法：
 *   svg2png   {input, output, width?, height?, scale?}
 *             SVG → PNG 位图（sharp 内置 librsvg 渲染，无系统依赖）
 *
 * sharp 在启动时立即 require（进程存在期间只加载一次）；因此"加载时机"
 * 完全由 Python 侧决定：不执行渲染就不 spawn 本进程，也就不会加载 sharp。
 */

"use strict";

const readline = require("readline");

// 启动即加载 sharp：把首次加载耗时放在进程启动时刻，避免拖慢第一次请求
let sharp = require("sharp");

const rl = readline.createInterface({ input: process.stdin });

function reply(id, payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

async function handle(method, params) {
  switch (method) {
    case "svg2png": {
      const input = params.input;
      const output = params.output;
      let img = sharp(input);
      if (params.width || params.height) {
        img = img.resize(params.width || null, params.height || null);
      } else if (params.scale && params.scale !== 1) {
        const meta = await sharp(input).metadata();
        const w = Math.round((meta.width || 0) * params.scale);
        const h = Math.round((meta.height || 0) * params.scale);
        if (w > 0 && h > 0) img = img.resize(w, h);
      }
      await img.png().toFile(output);
      return { ok: true };
    }
    default:
      return { ok: false, error: `unknown method: ${method}` };
  }
}

rl.on("line", async (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    reply(0, { ok: false, error: "bad json" });
    return;
  }
  const id = req.id || 0;
  try {
    const result = await handle(req.method, req.params || {});
    reply(id, Object.assign({ id }, result));
  } catch (e) {
    reply(id, { id, ok: false, error: String(e && e.message || e) });
  }
});

process.stdin.on("end", () => {
  process.exit(0);
});
