// OCTplugin CLI 冒烟（验证前: cd host && npm install）
// 启动内核→读 auth→WS 鉴权→plugin.list→plugin.call demo_hello.hello
const { spawn } = require("child_process");
const path = require("path");
const WebSocket = require("ws");

const KERNEL = path.join(__dirname, "..", "kernel", "kerneld.exe");
const kernel = spawn(KERNEL, [], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
let buf = "";
let seq = 0;

const fail = (m) => { console.error("FAIL:", m, (m && m.data) ? "\n  detail: " + JSON.stringify(m.data) : ""); cleanup(); process.exit(1); };
const cleanup = () => { try { kernel.kill(); } catch {} };

kernel.stdout.on("data", (d) => {
  buf += d.toString();
  const nl = buf.indexOf("\n");
  if (nl < 0) return;
  run(JSON.parse(buf.slice(0, nl).trim()).auth);
});
kernel.stderr.on("data", (d) => process.stderr.write("[kernel] " + d.toString()));

function rpc(ws, method, params) {
  return new Promise((res, rej) => {
    const id = ++seq;
    ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method, params }));
    const onMsg = (m) => {
      const msg = JSON.parse(m.toString());
      if (msg.id !== id) return;
      ws.off("message", onMsg);
      msg.error ? rej(Object.assign(new Error(msg.error.message), { data: msg.error.data })) : res(msg.result);
    };
    ws.on("message", onMsg);
    setTimeout(() => rej(new Error("rpc timeout")), method === "deps.install" ? 90000 : 10000);
  });
}

const TARGET = path.join(__dirname, "..", "store", "perms.json");
async function run(auth) {
  const ws = new WebSocket(`ws://127.0.0.1:${auth.port}`);
  ws.on("open", async () => {
    try {
      const hello = await rpc(ws, "kernel.hello", { client: "smoke", token: auth.token });
      console.log("hello:", JSON.stringify(hello));
      console.log("list :", JSON.stringify(await rpc(ws, "plugin.list", {})));
      console.log("call :", JSON.stringify(await rpc(ws, "plugin.call",
        { pluginId: "demo_hello", method: "hello", params: { name: "OCTplugin" } })));

      // 阶段A·权限门：未授权读文件应被拒(-32005)
      console.log("read (unauth):", JSON.stringify(await rpc(ws, "plugin.call",
        { pluginId: "demo_hello", method: "readfile", params: { path: TARGET } })));

      await rpc(ws, "perms.authorize", { pluginId: "demo_hello", perms: ["file_read"] });
      console.log("authorize    : 已授 file_read");
      console.log("read (auth)  :", JSON.stringify(await rpc(ws, "plugin.call",
        { pluginId: "demo_hello", method: "readfile", params: { path: TARGET } })));

      await rpc(ws, "perms.revoke", { pluginId: "demo_hello", perm: "file_read" });
      console.log("read (revoke):", JSON.stringify(await rpc(ws, "plugin.call",
        { pluginId: "demo_hello", method: "readfile", params: { path: TARGET } })));

      // 阶段B·依赖隔离：预览→安装→重启插件→hello 应命中 venv(packaging 存在)
      console.log("expect inVenv=false before install");
      const pre = await rpc(ws, "plugin.call",
        { pluginId: "demo_hello", method: "hello", params: { name: "t" } });
      console.log("hello(前):", JSON.stringify(pre));

      const preview = await rpc(ws, "deps.preview", { pluginId: "demo_hello" });
      console.log("preview  :", JSON.stringify(preview));

      const inst = await rpc(ws, "deps.install", { pluginId: "demo_hello" });
      if (!inst.installed) throw new Error("install returned not installed");
      console.log("install  : venv=", inst.venvPython);

      // 安装后重启插件，使其改用 venv 解释器。MVP 用 plugin.restart（新增）
      await rpc(ws, "plugin.restart", { pluginId: "demo_hello" });
      const post = await rpc(ws, "plugin.call",
        { pluginId: "demo_hello", method: "hello", params: { name: "t" } });
      console.log("hello(后):", JSON.stringify(post));
      if (post.result && !post.result.inVenv) throw new Error("插件未处于 venv 运行");
      if (post.result && !post.result.packaging) throw new Error("venv 未安装 packaging");

      // 阶段B·隔离验证：两个插件锁不同版本 numpy，证明各自隔离环境独立
      for (const id of ["demo_np1", "demo_np2"]) {
        await rpc(ws, "deps.install", { pluginId: id });
        await rpc(ws, "plugin.restart", { pluginId: id });
      }
      const np1 = await rpc(ws, "plugin.call", { pluginId: "demo_np1", method: "hello", params: {} });
      const np2 = await rpc(ws, "plugin.call", { pluginId: "demo_np2", method: "hello", params: {} });
      const v1 = np1.result && np1.result.numpy;
      const v2 = np2.result && np2.result.numpy;
      console.log("numpy(demo_np1):", v1);
      console.log("numpy(demo_np2):", v2);
      if (!v1 || !v2 || v1 === v2) throw new Error("依赖隔离未生效：两插件 numpy 应不同");
      if (v1 !== "1.26.4" || v2 !== "2.2.1") throw new Error(`版本不符: np1=${v1} np2=${v2}`);

      // 阶段C·共享函数注册表 + 跨插件经内核中转
      const fl = await rpc(ws, "registry.list", {});
      const names = fl.functions.map(f => f.name);
      console.log("functions:", names.join(", "));
      if (!names.includes("np1.version") || !names.includes("np2.version") || !names.includes("np1.callPeer"))
        throw new Error("registry 注册表缺失共享函数");
      const np2v = await rpc(ws, "registry.call", { name: "np2.version", params: {} });
      console.log("registry.call np2.version →", JSON.stringify(np2v.result));
      if (!np2v.result || np2v.result.numpy !== "2.2.1") throw new Error("按名调用 np2.version 失败");
      // 跨插件：demo_np1（venv1）经内核调用 demo_np2（venv2）
      const cross = await rpc(ws, "plugin.call", { pluginId: "demo_np1", method: "call_peer", params: {} });
      console.log("cross-plugin demo_np1→demo_np2 →", JSON.stringify(cross.result));
      if (!cross.result || !cross.result.peer || cross.result.peer !== "2.2.1")
        throw new Error("跨插件经内核中转调用失败: " + JSON.stringify(cross));

      // 阶段D·命令面板数据源：command.list 聚合插件可执行命令
      const cl = await rpc(ws, "command.list", {});
      const cmds = cl.commands.map(c => `${c.pluginId}:${c.name}`);
      console.log("commands:", cmds.join(", "));
      for (const want of ["demo_np1:/npversion", "demo_np2:/npversion", "demo_np1:/callpeer", "demo_hello:/hello", "demo_js:/today"])
        if (!cmds.includes(want)) throw new Error("command.list 缺失命令 " + want);
      const exec = await rpc(ws, "plugin.call", { pluginId: "demo_np1", method: "npversion", params: {} });
      if (!exec.result || exec.result.numpy !== "1.26.4") throw new Error("执行插件命令 npversion 失败");

      // 阶段E·事件流：插件→内核(event.emit)→宿主，验证能收到 ws 通知（无 id）
      const evP = new Promise((res, rej) => {
        const to = setTimeout(() => rej(new Error("event 未在 3s 内送达")), 3000);
        ws.on("message", (m) => {
          const msg = JSON.parse(m.toString());
          if (msg.method === "event" && msg.params && msg.params.type === "demo.tick") {
            clearTimeout(to); res(msg.params);
          }
        });
      });
      await rpc(ws, "plugin.call", { pluginId: "demo_np1", method: "emit_event", params: {} });
      const evGot = await evP;
      console.log("event.emit →", JSON.stringify(evGot));
      if (evGot.source !== "demo_np1" || !evGot.data) throw new Error("事件广播内容不符");

      // 阶段E·资源服务：GET /res/icons/... 应返回文件，穿越应当 403/404
      const resOk = await new Promise((res) => require("http").get(
        { host: "127.0.0.1", port: auth.port, path: "/res/logo/octpusY.svg" }, (r) => {
          const chunks = []; r.on("data", (c) => chunks.push(c));
          r.on("end", () => res({ status: r.statusCode, len: Buffer.concat(chunks).length }));
        }).on("error", () => res({ status: 0 })));
      console.log("res /logo:", JSON.stringify(resOk));
      if (resOk.status !== 200 || resOk.len < 10) throw new Error("资源服务未返回 logo");
      const resBad = await new Promise((res) => require("http").get(
        { host: "127.0.0.1", port: auth.port, path: "/res/../store/perms.json" }, (r) => {
          r.resume(); r.on("end", () => res(r.statusCode));
        }).on("error", () => res(0)));
      console.log("res 穿越:", resBad);
      if (resBad === 200) throw new Error("资源服务目录穿越防护失效");

      // 阶段F·Node 插件：独立进程 + npm 依赖隔离
      const js0 = await rpc(ws, "plugin.call", { pluginId: "demo_js", method: "today", params: {} });
      console.log("node before:", JSON.stringify(js0.result));
      if (!js0.result || !js0.result.node) throw new Error("Node 插件未以 node 运行");
      await rpc(ws, "deps.install", { pluginId: "demo_js" }); // npm 隔离安装
      const js1 = await rpc(ws, "plugin.call", { pluginId: "demo_js", method: "today", params: {} });
      console.log("node after:", JSON.stringify(js1.result));
      if (!js1.result || !js1.result.inNodeModules) throw new Error("Node 隔离依赖(luxon)未生效");
      if (js1.result.luxon == null) throw new Error("luxon 未安装进隔离 node_modules");
      const jsV = (js1.result.node || "").startsWith("v") ? "" : "node版本异常";
      if (jsV) throw new Error(jsV);

      // 阶段G·插件 UI：plugin.list 带 ui 字段 + /plugin/<id> 静态路由能服务插件页
      const plist = await rpc(ws, "plugin.list", {});
      const web = (plist.plugins || []).find(p => p.pluginId === "demo_web");
      if (!web || !web.ui || web.ui.type !== "web") throw new Error("plugin.list 缺少 web 插件的 ui 字段");
      const uiOk = await new Promise((res) => require("http").get(
        { host: "127.0.0.1", port: auth.port, path: "/plugin/demo_web/ui/index.html" }, (r) => {
          const chunks = []; r.on("data", (c) => chunks.push(c));
          r.on("end", () => res({ status: r.statusCode, html: Buffer.concat(chunks).toString() }));
        }).on("error", () => res({ status: 0, html: "" })));
      console.log("plugin UI: ", uiOk.status, uiOk.html.slice(0, 40));
      if (uiOk.status !== 200 || !uiOk.html.includes("iframe")) throw new Error("插件 UI 静态路由未工作");
      const uiTraverse = await new Promise((res) => require("http").get(
        { host: "127.0.0.1", port: auth.port, path: "/plugin/../store/perms.json" }, (r) => {
          r.resume(); r.on("end", () => res(r.statusCode));
        }).on("error", () => res(0)));
      console.log("plugin UI 穿越:", uiTraverse);
      if (uiTraverse === 200) throw new Error("插件 UI 目录穿越防护失效");
      // 验证 bridge 插件后端可被调用（宿主 iframe 内走的同一条 plugin.call）
      const wcall = await rpc(ws, "plugin.call", { pluginId: "demo_web", method: "hello", params: {} });
      console.log("demo_web hello:", JSON.stringify(wcall.result));
      if (!wcall.result || !wcall.result.ok) throw new Error("demo_web 后端不可调用");

      // 阶段H·home 迁移：插件列表带 ui + /plugin/home/ui 静态路由 + 后端状态可调用
      const hl = await rpc(ws, "plugin.list", {});
      const homeP = (hl.plugins || []).find(p => p.pluginId === "home");
      if (!homeP || !homeP.ui || homeP.ui.type !== "web") throw new Error("home 插件缺少 ui 声明");
      const homeUi = await new Promise((res) => require("http").get(
        { host: "127.0.0.1", port: auth.port, path: "/plugin/home/ui/index.html" }, (r) => {
          const chunks = []; r.on("data", (c) => chunks.push(c));
          r.on("end", () => res({ status: r.statusCode, html: Buffer.concat(chunks).toString() }));
        }).on("error", () => res({ status: 0, html: "" })));
      console.log("home UI: ", homeUi.status, homeUi.html.includes("今日打卡"));
      if (homeUi.status !== 200 || !homeUi.html.includes("今日打卡")) throw new Error("home 插件 UI 未服务");
      const hcall = await rpc(ws, "plugin.call", { pluginId: "home", method: "state", params: {} });
      console.log("home state:", JSON.stringify(hcall.result));
      if (!hcall.result || hcall.result.clockin == null || !Array.isArray(hcall.result.tasks))
        throw new Error("home 后端 state 不可调用");

      // 阶段I·取色器：列表带 ui + 静态路由 + 后端 ping
      const cpl = await rpc(ws, "plugin.list", {});
      const cp = (cpl.plugins || []).find(p => p.pluginId === "color_pick");
      if (!cp || !cp.ui || cp.ui.type !== "web") throw new Error("color_pick 缺少 ui 声明");
      const cpUi = await new Promise((res) => require("http").get(
        { host: "127.0.0.1", port: auth.port, path: "/plugin/color_pick/ui/index.html" }, (r) => {
          const chunks = []; r.on("data", (c) => chunks.push(c));
          r.on("end", () => res({ status: r.statusCode, html: Buffer.concat(chunks).toString() }));
        }).on("error", () => res({ status: 0, html: "" })));
      console.log("color_pick UI: ", cpUi.status, cpUi.html.includes("选择颜色"));
      if (cpUi.status !== 200 || !cpUi.html.includes("选择颜色")) throw new Error("取色器 UI 未服务");
      const cpCall = await rpc(ws, "plugin.call", { pluginId: "color_pick", method: "ping", params: {} });
      console.log("color_pick ping:", JSON.stringify(cpCall.result));
      if (!cpCall.result || !cpCall.result.pong) throw new Error("取色器后端不可调用");

      ws.close();
      console.log("SMOKE OK");
      cleanup();
      process.exit(0);
    } catch (e) { fail(e); }
  });
  ws.on("error", (e) => fail("ws error " + e.message));
}
setTimeout(() => fail("总超时"), 150000);