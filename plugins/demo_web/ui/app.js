// demo_web 插件页桥客户端。
// 宿主在 iframe.onload 后通过 postMessage 传 {type:'oct.hello', port, token, pluginId}；
// 本页据此直连内核 WS 鉴权，所有 RPC 在本页内直接走该 WS（与内核同源，最低延迟）。
(() => {
  let ws = null;
  let seq = 0;
  const pending = new Map();
  const selfId = location.pathname.split("/")[2] || "demo_web";

  function rpc(method, params, timeout = 15000) {
    return new Promise((res, rej) => {
      if (!ws || ws.readyState !== 1) return rej(new Error("内核未连接"));
      const id = ++seq;
      pending.set(id, { res, rej });
      ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method, params: params || {} }));
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error(method + " 超时")); } }, timeout);
    });
  }

  window.addEventListener("message", (ev) => {
    const d = ev.data || {};
    if (d.type !== "oct.hello" || ws) return;
    try { ws = new WebSocket("ws://127.0.0.1:" + d.port); } catch { return; }
    ws.onopen = async () => {
      try {
        await new Promise((res, rej) => {
          const id = ++seq;
          const t = setTimeout(() => rej(new Error("鉴权超时")), 8000);
          pending.set(id, { res: () => { clearTimeout(t); res(); }, rej });
          ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method: "kernel.hello", params: { client: selfId, token: d.token } }));
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
  });

  // 暴露给插件页脚本
  window.OCT = {
    pluginId: selfId,
    rpc,                       // rpc(method, params)
    callPlugin: (pluginId, method, params) => rpc("plugin.call", { pluginId, method, params: params || {} })
  };
})();