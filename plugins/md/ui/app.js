// 插件页桥客户端。宿主 onload 后 postMessage 传 {type:'oct.hello', port, token, pluginId}
// 据此直连内核 WS。若是宿主为 md 编辑器独立窗口打开的页面（?mdEditor=1&auth=&kport&path=），
// 则改为直接读查询参数自连内核（无宿主 parent 握手）。
(() => {
  let ws = null;
  let seq = 0;
  const pending = new Map();
  const selfId = location.pathname.split("/")[2] || "color_pick";

  function rpc(method, params, timeout = 15000) {
    return new Promise((res, rej) => {
      if (!ws || ws.readyState !== 1) return rej(new Error("内核未连接"));
      const id = ++seq;
      pending.set(id, { res, rej });
      ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method, params: params || {} }));
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error(method + " 超时")); } }, timeout);
    });
  }

  function connect(port, token) {
    ws = new WebSocket("ws://127.0.0.1:" + port);
    ws.onopen = async () => {
      try {
        await new Promise((res, rej) => {
          const id = ++seq;
          const t = setTimeout(() => rej(new Error("鉴权超时")), 8000);
          pending.set(id, { res: () => { clearTimeout(t); res(); }, rej });
          ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method: "kernel.hello", params: { client: selfId, token } }));
        });
        window.dispatchEvent(new CustomEvent("oct.ready", { detail: { pluginId: selfId } }));
      } catch (e) { window.dispatchEvent(new CustomEvent("oct.error", { detail: String(e) })); }
    };
    ws.onmessage = (m) => {
      let msg; try { msg = JSON.parse(m.data); } catch { return; }
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id); pending.delete(msg.id);
        msg.error ? rej(Object.assign(new Error(msg.error.message), { data: msg.error.data, result: msg.result })) : res(msg.result);
      }
    };
    return ws;
  }

  // 独立窗口模式：宿主把内核地址放在查询参数里（mdIde editor 场景）
  const q = new URLSearchParams(location.search);
  if (q.get("mdEditor") === "1" && q.get("kport")) {
    try { connect(q.get("kport"), q.get("auth") || ""); } catch (e) { /* 忽略，等待错误派发 */ }
  } else {
    window.addEventListener("message", (ev) => {
      const d = ev.data || {};
      if (d.type !== "oct.hello" || ws) return;
      try { connect(d.port, d.token); } catch { /* 忽略 */ }
    });
  }

  window.OCT = {
    pluginId: selfId,
    rpc,
    callPlugin: (pluginId, method, params) => rpc("plugin.call", { pluginId, method, params: params || {} })
  };
})();