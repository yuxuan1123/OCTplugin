// OCTplugin 宿主主进程（MVP）：
//   1) 拉起 Go 内核 daemon（./kernel/kerneld.exe）
//   2) 从内核 stdout 读首个 auth 行 {port, token}
//   3) WebSocket 连接 localhost:port，首条消息发带 token 的 kernel.hello
//   4) 调 plugin.call → demo_hello.hello，结果在窗口显示。

const { app, BrowserWindow, dialog, ipcMain, globalShortcut, Tray, Menu, nativeImage } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const WebSocket = require("ws");

const KERNEL_EXE = path.join(__dirname, "..", "kernel", "kerneld.exe");
const APP_LOGO = path.join(__dirname, "..", "resources", "logo", "octpusY.svg");
const TRAY_ICON = path.join(__dirname, "..", "resources", "icons", "logo16.png");
const TRAY_ICON_2X = path.join(__dirname, "..", "resources", "icons", "logo32.png");

let win = null;
let ws = null;
let kernelProc = null;
let tray = null;
let seq = 0;
let kernelAuth = null; // 内核地址/token，供新开插件子窗口复用

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

function showMain() {
  if (!win) return;
  win.show(); win.focus();
}
function setupTray() {
  // 托盘图标优先用 octpusY.svg；若 SVG 无法解码（空图像）则回退 PNG
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
    // 演示调用：demo_hello 可能被用户设为 disabled/lazy 或移除。失败不应阻断 UI 启动。
    let details = null, hello = null;
    try { details = await rpc("plugin.details", { pluginId: "demo_hello" }); } catch (e) {}
    try { hello = await rpc("plugin.call", { pluginId: "demo_hello", method: "hello", params: { name: "OCTplugin" } }); } catch (e) {}
    win.webContents.send("kernel:ready", {
      list, hello, details,
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
ipcMain.handle("rpc", async (e, method, params) => rpc(method, params));

// 阶段1：选择插件源目录（「添加插件」导入用）
ipcMain.handle("dialog:pickDir", async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(win, {
    title: "选择插件源目录", properties: ["openDirectory"],
  });
  if (canceled || !filePaths.length) return { canceled: true };
  return { canceled: false, path: filePaths[0] };
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
    setTimeout(() => reject(new Error("等待内核 auth 超时")), 5000);
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

function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    ws.send(JSON.stringify({ v: 1, jsonrpc: "2.0", id, method, params }));
    const timer = setTimeout(() => { ws.off("message", onMsg); reject(new Error("RPC 超时: " + method)); }, 15000);
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