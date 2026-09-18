// OCTplugin 宿主主进程（MVP）：
//   1) 拉起 Go 内核 daemon（./kernel/kerneld.exe）
//   2) 从内核 stdout 读首个 auth 行 {port, token}
//   3) WebSocket 连接 localhost:port，首条消息发带 token 的 kernel.hello
//   4) 调 plugin.list → 结果在窗口显示。

const { app, BrowserWindow, dialog, ipcMain, globalShortcut, Tray, Menu, nativeImage } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const WebSocket = require("ws");

const KERNEL_EXE = path.join(__dirname, "..", "kernel", "kerneld.exe");
const APP_LOGO = path.join(__dirname, "..", "resources", "logo", "logo128.png");
const TRAY_ICON = path.join(__dirname, "..", "resources", "icons", "logo16.png");
const TRAY_ICON_2X = path.join(__dirname, "..", "resources", "icons", "logo32.png");

let win = null;
let ws = null;
let kernelProc = null;
let tray = null;
let seq = 0;
let kernelAuth = null; // 内核地址/token，供新开插件子窗口复用

// 给宿主主进程一个可辨认的标题（任务管理器/进程列表里更容易区分）
process.title = "OCTplugin · 墨韵工作台";

// 插件请求以独立窗口打开页面（如 md 编辑器）。宿主把内核地址放查询参数，
// 子页 app.js 见 ?mdEditor=1&kport=&auth= 时自连内核 WS。
ipcMain.handle("win:openPluginWindow", async (e, opts) => {
  const url = opts && opts.url;
  if (!url || !kernelAuth) return { ok: false, error: !kernelAuth ? "内核未就绪" : "无 url" };
  const sep = url.includes("?") ? "&" : "?";
  const full = url + sep + "auth=" + encodeURIComponent(kernelAuth.token) + "&kport=" + kernelAuth.port;
  const sub = new BrowserWindow({
    width: opts.width || 960, height: opts.height || 720,
    title: opts.title || "插件窗口", icon: APP_LOGO,
    autoHideMenuBar: true,
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  sub.loadURL(full);
  return { ok: true };
});

// 设置子窗口：在主窗口外以独立小窗打开“某一项设置表单”（parent:win 使关闭主窗时一并清理）。
// 复用 win:openPluginWindow 的建窗思路；URL 追加 view=settings&sec=…&auth=&kport=（sub 的 index.html 走宿主 rpc 桥，auth/kport 仅为格式一致）
ipcMain.handle("win:openSettings", async (e, { sec } = {}) => {
  if (!kernelAuth) return { ok: false, error: "内核未就绪" };
  const names = { A: "依赖源设置", B: "启动与资源·全局策略", C: "启动与资源·单插件策略", D: "插件排序·默认页" };
  const name = names[sec] || sec || "设置";
  const sub = new BrowserWindow({
    width: 720, height: 640, title: "设置 · " + name, icon: APP_LOGO,
    autoHideMenuBar: true, parent: win, // parent 绑定但仍是可拖出主窗外的独立窗口
    // 与主窗一致开启 nodeIntegration：子窗加载的是同一个 index.html，其渲染脚本用 require("electron") +
    // ipcRenderer.invoke("rpc") 经由宿主主进程复用内核连接（详见 index.html 注释）。
    webPreferences: { nodeIntegration: true, contextIsolation: false },
  });
  // 必须用 loadFile 的 options.query 传参：直接把 ?view=… 拼进 filePath 会被当磁盘文件名，进不了子窗口模式。
  sub.loadFile("index.html", {
    query: { view: "settings", sec: sec || "", auth: kernelAuth.token, kport: String(kernelAuth.port) },
  });
  return { ok: true };
});

// 关闭当前调用者所属窗口（设置子窗口的“关闭”按钮用；sender 即子窗自身）
ipcMain.handle("win:closeSelf", (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w) w.close();
  return { ok: true };
});

function showMain() {
  if (!win) return;
  win.show(); win.focus();
}
function setupTray() {
  // 托盘图标用 logo128.png（与窗口/顶栏同款 logo）；异常则回退小尺寸 PNG
  let img = null;
  try { img = nativeImage.createFromPath(APP_LOGO); } catch (e) { img = null; }
  if (!img || img.isEmpty()) img = nativeImage.createFromPath(fs.existsSync(TRAY_ICON) ? TRAY_ICON : TRAY_ICON_2X);
  tray = new Tray(img);
  tray.setToolTip("OCTplugin · 墨韵工作台");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "显示主窗口", click: showMain },
    { type: "separator" },
    { label: "退出", click: () => app.quit() },
  ]));
  tray.on("click", showMain);
}

function createWindow() {
  win = new BrowserWindow({
    width: 900,
    height: 560,
    title: "OCTplugin · MVP",
    icon: APP_LOGO,
    frame: false, // 完全无边框：自绘顶部栏（拖拽区 + 窗口控制按钮），去掉原生重复的标题栏
    webPreferences: { nodeIntegration: true, contextIsolation: false },
  });
  win.loadFile("index.html");
  // 自绘标题栏的窗口控制
  ipcMain.on("win:minimize", () => win && win.minimize());
  ipcMain.on("win:maximize", () => {
    if (!win) return;
    win.isMaximized() ? win.unmaximize() : win.maximize();
  });
  ipcMain.on("win:close", () => win && win.close());
  ipcMain.on("win:tray", () => win && win.hide()); // 收起到系统托盘
  const emitState = () => { try { win.webContents.send("win:state", win.isMaximized() ? "max" : "normal"); } catch (e) {} };
  win.on("maximize", emitState);
  win.on("unmaximize", emitState);
  // 等 renderer 完成加载后再启动链路，避免 send 早于监听而丢失
  win.webContents.on("did-finish-load", () => {
    if (win._booted) return;
    win._booted = true;
    emitState();
    bootstrap();
  });
}

async function bootstrap() {
  try {
    const auth = await startKernel();
    kernelAuth = auth; // 供后续创建插件子窗口（md 编辑器等）复用端口/token
    await connect(auth);
    const list = await rpc("plugin.list", {});
    win.webContents.send("kernel:ready", {
      list,
      kernelBase: `http://127.0.0.1:${auth.port}/`,
      resBase: `http://127.0.0.1:${auth.port}/res/`,
      port: auth.port, token: auth.token,
    });
  } catch (e) {
    console.error("启动失败:", e);
    win.webContents.send("kernel:error", String(e));
  }
}

// renderer 通用 RPC 桥（授权/吊销/读文件等）
ipcMain.handle("rpc", async (e, method, params, timeoutMs) => rpc(method, params, timeoutMs));

// 阶段1：选择插件源目录（「添加插件」导入用）
ipcMain.handle("dialog:pickDir", async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(win, {
    title: "选择插件源目录", properties: ["openDirectory"],
  });
  if (canceled || !filePaths.length) return { canceled: true };
  return { canceled: false, path: filePaths[0] };
});

// 文件对话框：供插件选择单个文件（如格式转换的源文件）；filters=[{name,extensions}]
ipcMain.handle("dialog:pickFile", async (e, filters) => {
  const { canceled, filePaths } = await dialog.showOpenDialog(win, {
    title: "选择文件",
    properties: ["openFile"],
    filters: (filters && filters.length ? filters : null) || [{ name: "所有文件", extensions: ["*"] }],
  });
  if (canceled || !filePaths.length) return { canceled: true };
  return { canceled: false, path: filePaths[0] };
});

// ── 外部地址与内存设置：扫描各插件静态 manifest（不依赖插件进程是否在线）──
const PLUGINS_ROOT = path.join(__dirname, "..", "plugins");
const LOAD_DEF = "lazy", UNLOAD_DEF = "idle", IDLE_MIN_DEF = 10;
function readJsonSafe(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return fallback; }
}
ipcMain.handle("res:scan", async () => {
  const groups = [];
  if (!fs.existsSync(PLUGINS_ROOT)) return { groups };
  const dirs = fs.readdirSync(PLUGINS_ROOT, { withFileTypes: true }).filter(d => d.isDirectory());
  for (const d of dirs) {
    const pid = d.name;
    const mf = path.join(PLUGINS_ROOT, pid, "resources", "manifest.json");
    if (!fs.existsSync(mf)) continue;
    const manifest = readJsonSafe(mf, []);
    const saved = readJsonSafe(path.join(PLUGINS_ROOT, pid, "store", "models.json"), {});
    const resources = saved.resources || {};
    for (const m of manifest) {
      const rec = resources[m.key] || {};
      groups.push({
        pid, key: m.key, label: m.label || m.key, type: m.type || "model",
        variant: m.variant || "", path: rec.path || "", load: rec.load || LOAD_DEF,
        unload: rec.unload || UNLOAD_DEF, idle_min: rec.idle_min || IDLE_MIN_DEF,
      });
    }
  }
  return { groups };
});
ipcMain.handle("res:set", async (e, { pid, item } = {}) => {
  if (!pid || !item || !item.key) return { ok: false, error: "缺少 pid / item.key" };
  const storeFile = path.join(PLUGINS_ROOT, pid, "store", "models.json");
  const data = readJsonSafe(storeFile, {});
  data.resources = data.resources || {};
  data.resources[item.key] = {
    path: item.path || "", load: item.load || LOAD_DEF,
    unload: item.unload || UNLOAD_DEF,
    idle_min: (item.idle_min === undefined || item.idle_min === null || item.idle_min === "")
      ? IDLE_MIN_DEF : Math.max(1, parseInt(item.idle_min, 10) || IDLE_MIN_DEF),
  };
  try {
    fs.mkdirSync(path.dirname(storeFile), { recursive: true });
    fs.writeFileSync(storeFile, JSON.stringify(data, null, 2), "utf8");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
});

// 定位 uv：优先显式 OCTRUN_UV → PATH（where uv）→ 常见安装目录。
function detectUv() {
  if (process.env.OCTRUN_UV) return process.env.OCTRUN_UV;
  const { execSync } = require("child_process");
  try {
    const hit = execSync("where uv", { encoding: "utf8" }).split(/\r?\n/)[0].trim();
    if (hit) return hit;
  } catch (e) { /* 不在 PATH，继续查安装目录 */ }
  const home = os.homedir();
  for (const c of [path.join(home, ".local", "bin", "uv.exe"),
                   path.join(home, ".cargo", "bin", "uv.exe")]) {
    try { if (fs.existsSync(c)) return c; } catch (e) {}
  }
  return null;
}

function startKernel() {
  return new Promise((resolve, reject) => {
    // /c 让 Go 输出 stdio 原始（Windows 下避免 Go 检测 tty 出问题）
    // 注入 OCTRUN_UV：Windows 下 uv 常不在 PATH，内核依赖它解析托管 Python。
    const uv = detectUv();
    const env = uv ? Object.assign({}, process.env, { OCTRUN_UV: uv }) : process.env;
    kernelProc = spawn(KERNEL_EXE, [], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true, env });

    let buf = "";
    kernelProc.stdout.on("data", (d) => {
      buf += d.toString();
      const nl = buf.indexOf("\n");
      if (nl < 0) return;
      const line = buf.slice(0, nl).trim();
      try {
        const { auth } = JSON.parse(line);
        resolve(auth);
      } catch (e) {
        reject(new Error("内核 auth 行解析失败: " + line));
      }
    });
    kernelProc.stderr.on("data", (d) => console.error("[kernel]", d.toString().trim()));
    kernelProc.on("exit", (code) => {
      if (buf.indexOf("\n") < 0) reject(new Error(`内核提前退出 code=${code}`));
    });
    // 内核在 StartAll 期间会做依赖就绪探测（亚秒~15s 上限），auth 只在其后打印；
    // 5s 过短会误判。60s 覆盖首次依赖探测/下载，避免启动偶发超时。
    setTimeout(() => reject(new Error("等待内核 auth 超时")), 60000);
  });
}

function connect(auth) {
  return new Promise((resolve, reject) => {
    ws = new WebSocket(`ws://127.0.0.1:${auth.port}`);
    ws.on("open", () => {
      const id = Date.now();
      ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method: "kernel.hello",
                               params: { client: "octplugin-host", token: auth.token } }));
      ws.on("message", (m) => {
        const msg = JSON.parse(m.toString());
        if (msg.id === id) resolve(msg);
        else win.webContents.send("kernel:event", JSON.stringify(msg));
      });
    });
    ws.on("error", reject);
  });
}

function rpc(method, params, timeoutMs) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method, params }));
    const timer = setTimeout(() => { ws.off("message", onMsg); reject(new Error("RPC 超时: " + method)); }, timeoutMs || 15000);
    const onMsg = (m) => {
      const msg = JSON.parse(m.toString());
      if (msg.id !== id) return;
      ws.off("message", onMsg);
      clearTimeout(timer);
      if (msg.error) reject(new Error(msg.error.message + " " + JSON.stringify(msg.error.data || "")));
      else resolve(msg.result);
    };
    ws.on("message", onMsg);
  });
}

app.whenReady().then(() => {
  createWindow();
  setupTray();
  registerShortcuts();
});

// 阶段D·全局热键（FR-9）：Ctrl+K / Ctrl+Shift+P 打开命令面板，带冲突检测兜底
function registerShortcuts() {
  const openPalette = () => {
    if (!win) return;
    win.show(); win.focus();
    win.webContents.send("palette:open");
  };
  const combo = process.platform === "darwin" ? "Command+Shift+P" : "Control+K";
  const registered = globalShortcut.register(combo, openPalette);
  if (!registered) {
    // 冲突兜底：改用窗口内 before-input-event 捕获（不冒充系统级全局键）
    console.warn(`[host] globalShortcut ${combo} 被占用（冲突），回退窗口内热键`);
    win.webContents.on("before-input-event", (event, input) => {
      if (input.type !== "keyDown") return;
      const k = (input.key || "").toUpperCase();
      if ((input.control || input.meta) && k === "K") { event.preventDefault(); openPalette(); }
    });
  }
}

app.on("will-quit", () => { globalShortcut.unregisterAll(); });

app.on("window-all-closed", () => {
  if (kernelProc) kernelProc.kill();
  app.quit();
});