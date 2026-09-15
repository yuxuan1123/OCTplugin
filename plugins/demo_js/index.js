// demo_js：验证阶段F —— Node 插件作为独立进程受内核管理，npm 依赖隔离（luxon）。
// 与 Python 插件同用 stdio JSON-RPC：
//   内核 → 插件：{"id":n,"method":"..."}；插件回 {"id":n,"result":...}/{"error":...}
'use strict';
const readline = require('readline');

let luxon = null, luxonVer = null;
try {
  luxon = require('luxon');
  luxonVer = require('luxon/package.json').version;
} catch (e) {
  luxonVer = null; // 依赖未装；deps.install 后重启出现
}

function handle(method, params) {
  if (method === 'hello' || method === 'today') {
    const date = luxon ? luxon.DateTime.now().toISODate() : null;
    return { date, luxon: luxonVer, node: process.version, inNodeModules: !!luxon };
  }
  if (method === 'luxon') return { luxon: luxonVer };
  if (method === 'ping') return { pong: true };
  return null;
}

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  line = line.trim();
  if (!line) return;
  let req;
  try { req = JSON.parse(line); } catch (e) {
    process.stdout.write(JSON.stringify({ v: 1, jsonrpc: '2.0', id: 0, error: { code: -32700, message: String(e) } }) + '\n');
    return;
  }
  const id = req.id || 0;
  const out = { v: 1, jsonrpc: '2.0', id };
  const result = handle(req.method || '', req.params || {});
  if (result === null) out.error = { code: -32601, message: 'method not found' };
  else out.result = result;
  process.stdout.write(JSON.stringify(out) + '\n');
});