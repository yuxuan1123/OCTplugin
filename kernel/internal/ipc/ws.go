package ipc

import (
	"encoding/json"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/octplugin/kernel/internal/deps"
	"github.com/octplugin/kernel/internal/perms"
	"github.com/octplugin/kernel/internal/protocol"
	"github.com/octplugin/kernel/internal/supervisor"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true }, // 仅监听 127.0.0.1，见 Listen
}

// Server 内核对外 WS + 资源服务入口。内核与宿主 1:1 长连接。
type Server struct {
	authToken    string
	smanager     *supervisor.Manager
	gate         *perms.Gate
	installer    *deps.Installer
	resourcesDir string // 阶段E：共享资源根目录（内核对插件/宿主暴露 resources/）
	pluginsDir   string // 阶段G：插件 UI 静态文件根（内核对宿主 iframe 暴露 /plugin/）
	mu           sync.Mutex
	conn         *websocket.Conn
	writeMu      sync.Mutex // 串行化所有 WS 写，避免并发 WriteJSON panic
}

// HostClientName 标识宿主主连接；其余 client 视为插件 iframe 连接。
const HostClientName = "octplugin-host"

// setHost 把宿主连接设为事件广播目标；重复连接时替换并关闭旧宿主连接。
func (s *Server) setHost(conn *websocket.Conn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.conn != nil && s.conn != conn {
		_ = s.conn.Close()
	}
	s.conn = conn
}

func NewServer(token string, mgr *supervisor.Manager, gate *perms.Gate, inst *deps.Installer, resourcesDir, pluginsDir string) *Server {
	return &Server{authToken: token, smanager: mgr, gate: gate, installer: inst, resourcesDir: resourcesDir, pluginsDir: pluginsDir}
}

// WireEvents 把单长连接写者注入当前运行插件，作为插件→内核 event 广播出口。
// 需在 manager.StartAll() 之后调用（否则插件尚未启动）。
func (s *Server) WireEvents() {
	for _, id := range s.smanager.List() {
		if pl := s.smanager.Plugin(id); pl != nil {
			pl.Event = func(src, typ string, data any) { s.broadcast(src, typ, data) }
		}
	}
}

// broadcast 把插件事件以 ws 通知（无 id）推给宿主（FR-8 事件通道）。
func (s *Server) broadcast(src, typ string, data any) {
	s.mu.Lock()
	conn := s.conn
	s.mu.Unlock()
	if conn == nil {
		return
	}
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	_ = conn.WriteJSON(protocol.Notification{ // 无 id 的通知（事件通道）
		V: protocol.ProtocolVersion, JSONRPC: "2.0", Method: "event",
		Params: map[string]any{"source": src, "type": typ, "data": data},
	})
}

// Listen 绑定 localhost 随机端口，返回监听器与端口。
func (s *Server) Listen(addr string) (net.Listener, int, error) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, 0, err
	}
	return ln, ln.Addr().(*net.TCPAddr).Port, nil
}

func (s *Server) Serve(ln net.Listener) {
	http.Serve(ln, s)
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// 阶段E：资源服务 /res/* —— 向宿主/插件 iframe 暴露共享资源（OCTools 可随意调用的等价物）
	if !websocket.IsWebSocketUpgrade(r) && strings.HasPrefix(r.URL.Path, "/res/") {
		s.serveRes(w, r)
		return
	}
	// 阶段G：插件 UI /plugin/<id>/* —— 向宿主 iframe 暴露插件自带 HTML 界面
	if !websocket.IsWebSocketUpgrade(r) && strings.HasPrefix(r.URL.Path, "/plugin/") {
		s.servePluginUI(w, r)
		return
	}
	// 新 BrowserWindow 会请求 /favicon.ico：返回应用 SVG 图标，避免 400/404 噪音。
	if !websocket.IsWebSocketUpgrade(r) && (r.URL.Path == "/favicon.ico" || r.URL.Path == "/favicon.svg") {
		s.serveFavicon(w, r)
		return
	}
	// 非 WebSocket 的普通 HTTP 直接 404，避免误交给 upgrader 返回 "Bad Request"。
	if !websocket.IsWebSocketUpgrade(r) {
		http.NotFound(w, r)
		return
	}
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[ws] upgrade: %v", err)
		return
	}
	// 多连接模型：宿主 + 各插件 iframe 各自独立持有一条 WS，互不互踢。
	log.Printf("[ws] connected from %s", r.RemoteAddr)
	go s.handle(conn)
}

// handle 首个消息必须是带 token 的 kernel.hello（D8），否则关闭。
func (s *Server) handle(conn *websocket.Conn) {
	defer conn.Close()
	// 首个消息
	_, raw, err := conn.ReadMessage()
	if err != nil {
		log.Printf("[ws] first msg read: %v", err)
		return
	}
	var hello protocol.Request
	if err := json.Unmarshal(raw, &hello); err != nil || hello.Method != "kernel.hello" {
		log.Printf("[ws] auth rejected: err=%v raw=%s", err, string(raw))
		return
	}
	var p struct {
		Token  string `json:"token"`
		Client string `json:"client"`
	}
	_ = json.Unmarshal(hello.Params, &p)
	if p.Token != s.authToken {
		log.Printf("[ws] auth rejected: bad token")
		return
	}
	// 仅宿主连接作为事件广播目标（plugin→宿主 event 推送）；插件 iframe 各自独立并发。
	if p.Client == HostClientName {
		s.setHost(conn)
	}
	s.reply(conn, protocol.NewResult(hello.ID, map[string]any{
		"kernel": "kerneld", "protocolVersion": protocol.ProtocolVersion,
		"capabilities": []string{"plugin", "stdio"},
	}))

	// 后续消息循环
	for {
		_, raw, err := conn.ReadMessage()
		if err != nil {
			log.Printf("[ws] closed: %v", err)
			return
		}
		var req protocol.Request
		if err := json.Unmarshal(raw, &req); err != nil {
			s.reply(conn, protocol.NewError(0, protocol.ErrParse, nil))
			continue
		}
		s.dispatch(conn, req)
	}
}

func (s *Server) dispatch(conn *websocket.Conn, req protocol.Request) {
	switch req.Method {
	case "kernel.ping":
		s.reply(conn, protocol.NewResult(req.ID, map[string]any{}))
	case "plugin.list":
		s.handleList(conn, req)
	case "plugin.call":
		s.handleCall(conn, req)
	case "plugin.details":
		s.handlePluginDetails(conn, req)
	case "plugin.restart":
		s.handlePluginRestart(conn, req)
	case "plugin.import":
		s.handlePluginImport(conn, req)
	case "plugin.remove":
		s.handlePluginRemove(conn, req)
	case "perms.list":
		s.handlePermsList(conn, req)
	case "perms.authorize":
		s.handlePermsAuthorize(conn, req)
	case "perms.revoke":
		s.handlePermsRevoke(conn, req)
	case "deps.preview":
		s.handleDepsPreview(conn, req)
	case "deps.install":
		s.handleDepsInstall(conn, req)
	case "deps.getConfig":
		s.handleDepsGetConfig(conn, req)
	case "deps.setConfig":
		s.handleDepsSetConfig(conn, req)
	case "registry.list":
		s.handleRegistryList(conn, req)
	case "registry.call":
		s.handleRegistryCall(conn, req)
	case "command.list":
		s.handleCommandList(conn, req)
	default:
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrMethodNotFound, nil))
	}
}

func (s *Server) handleList(conn *websocket.Conn, req protocol.Request) {
	ids := s.smanager.List()
	res := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		p := s.smanager.Plugin(id)
		if p == nil {
			continue
		}
		res = append(res, map[string]any{
			"pluginId": id, "name": p.Manifest.Name, "type": p.Manifest.Type,
			"state": "RUNNING", "ui": p.Manifest.UI,
		})
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"plugins": res}))
}

// plugin.details 返回插件的完整描述（manifest 字段），供宿主渲染权限/命令等。
func (s *Server) handlePluginDetails(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	_ = json.Unmarshal(req.Params, &p)
	pl := s.smanager.Plugin(p.PluginID)
	if pl == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID}))
		return
	}
	mf := pl.Manifest
	decl, granted, high := s.gate.Subset(mf.ID)
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{
		"pluginId": mf.ID, "name": mf.Name, "type": mf.Type, "entry": mf.Entry,
		"loadMode": mf.LoadMode, "uiMode": mf.UIMode, "ui": mf.UI,
		"permissionsDeclared": decl, "permissionsGranted": granted,
		"highRisk":     high,
		"dependencies": mf.Dependencies,
		"commands":     mf.Commands,
		"state":        "RUNNING",
	}))
}

// perms.list 查询某插件权限状态。
func (s *Server) handlePermsList(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	_ = json.Unmarshal(req.Params, &p)
	if s.gate == nil {
		s.reply(conn, protocol.NewResult(req.ID, map[string]any{}))
		return
	}
	decl, granted, _ := s.gate.Subset(p.PluginID)
	permsStatus := []map[string]any{}
	for _, d := range decl {
		permsStatus = append(permsStatus, map[string]any{
			"perm": d, "granted": contains(granted, d),
		})
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"pluginId": p.PluginID, "perms": permsStatus}))
}

// perms.authorize 首次授权（用户逐项或一键同意，FR-7）。
func (s *Server) handlePermsAuthorize(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string   `json:"pluginId"`
		Perms    []string `json:"perms"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.PluginID == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	if err := s.gate.Authorize(p.PluginID, p.Perms); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrDepsInstall, map[string]any{"error": err.Error()}))
		return
	}
	_, granted, _ := s.gate.Subset(p.PluginID)
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"pluginId": p.PluginID, "granted": granted}))
}

// perms.revoke 动态收回某项权限（即时生效）。
func (s *Server) handlePermsRevoke(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
		Perm     string `json:"perm"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.PluginID == "" || p.Perm == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	if err := s.gate.Revoke(p.PluginID, p.Perm); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrDepsInstall, map[string]any{"error": err.Error()}))
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"pluginId": p.PluginID, "revoked": p.Perm}))
}

// plugin.restart 重启指定插件（安装隔离依赖后切换解释器）。
func (s *Server) handlePluginRestart(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	_ = json.Unmarshal(req.Params, &p)
	if err := s.smanager.Restart(p.PluginID); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"error": err.Error()}))
		return
	}
	s.WireEvents() // 阶段E：重启会新建 Plugin，需重注事件广播出口
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"pluginId": p.PluginID, "restarted": true}))
}

// plugin.import 从源目录导入 OCTplugin 格式插件（复制进 plugins/<id> 并启动）。
func (s *Server) handlePluginImport(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		SrcPath string `json:"srcPath"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.SrcPath == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	if err := s.smanager.Import(p.SrcPath); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrDepsInstall, map[string]any{"error": err.Error()}))
		return
	}
	s.WireEvents()
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"imported": true}))
}

// plugin.remove 永久移除插件（停进程 + 删目录）。
func (s *Server) handlePluginRemove(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.PluginID == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	if err := s.smanager.Remove(p.PluginID); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID, "error": err.Error()}))
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"pluginId": p.PluginID, "removed": true}))
}

// deps.preview 返回待安装的依赖清单，供宿主弹窗确认（FR-3）。
func (s *Server) handleDepsPreview(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	_ = json.Unmarshal(req.Params, &p)
	pl := s.smanager.Plugin(p.PluginID)
	if pl == nil || s.installer == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID}))
		return
	}
	mf := pl.Manifest
	rows := s.installer.Preview(mf)
	// 是否已就绪（Python 看 venv，Node 看插件目录 node_modules）
	satisfied := s.installer.VenvPython(mf) != "" || s.installer.NodeModulesReady(mf)
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{
		"pluginId": p.PluginID, "dependencies": rows, "satisfied": satisfied,
	}))
}

// deps.install 安装插件隔离依赖（uv venv + pip/rpm），失败自动回滚。
func (s *Server) handleDepsInstall(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	_ = json.Unmarshal(req.Params, &p)
	pl := s.smanager.Plugin(p.PluginID)
	if pl == nil || s.installer == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID}))
		return
	}
	// 已就绪则幂等返回（Python 看 venv，Node 看插件目录里的 node_modules）。
	satisfied := s.installer.VenvPython(pl.Manifest) != "" || s.installer.NodeModulesReady(pl.Manifest)
	if satisfied {
		s.reply(conn, protocol.NewResult(req.ID, map[string]any{
			"pluginId": p.PluginID, "installed": true, "satisfied": true,
		}))
		return
	}
	// 首次安装：先停掉持有该 venv/目录的插件进程，否则 Windows 无法删除依赖目录（Access denied）
	_ = s.smanager.StopPlugin(p.PluginID)
	if err := s.installer.Install(pl.Manifest); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrDepsInstall, map[string]any{"error": err.Error()}))
		return
	}
	// 安装后会重启插件（全新进程，解释器经 interp 重新解析；事件出口需重注）
	if rerr := s.smanager.Restart(p.PluginID); rerr != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrDepsInstall, map[string]any{"error": "restart after install: " + rerr.Error()}))
		return
	}
	s.WireEvents()
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{
		"pluginId": p.PluginID, "installed": true, "restarted": true,
	}))
}

// deps.getConfig 读取当前依赖源设置（镜像源/缓存目录）。
func (s *Server) handleDepsGetConfig(conn *websocket.Conn, req protocol.Request) {
	if s.installer == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrMethodNotFound, nil))
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{
		"index_urls": s.installer.IndexURLs, "cache_dir": s.installer.CacheDir,
	}))
}

// deps.setConfig 更新依赖源设置并持久化到 store/deps.json（下次安装立即生效）。
func (s *Server) handleDepsSetConfig(conn *websocket.Conn, req protocol.Request) {
	if s.installer == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrMethodNotFound, nil))
		return
	}
	var p struct {
		IndexURLs []string `json:"index_urls"`
		CacheDir  string   `json:"cache_dir"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	s.installer.SetIndex(p.IndexURLs).SetCacheDir(p.CacheDir)
	if err := s.installer.SaveConfig(); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrIO, map[string]any{"error": err.Error()}))
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"saved": true}))
}

func contains(ss []string, s string) bool {
	for _, v := range ss {
		if v == s {
			return true
		}
	}
	return false
}

func (s *Server) handleCall(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string          `json:"pluginId"`
		Method   string          `json:"method"`
		Params   json.RawMessage `json:"params"`
		Timeout  *int            `json:"timeoutMs"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.PluginID == "" || p.Method == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	pl := s.smanager.Plugin(p.PluginID)
	if pl == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID}))
		return
	}
	if !pl.Alive() {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginDown, map[string]any{"pluginId": p.PluginID}))
		return
	}
	timeout := 15 * time.Second
	if p.Timeout != nil {
		timeout = time.Duration(*p.Timeout) * time.Millisecond
	}
	var params any
	if len(p.Params) > 0 {
		_ = json.Unmarshal(p.Params, &params)
	}
	resp, err := pl.Call(p.Method, params, timeout)
	if err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrTimeout, map[string]any{"error": err.Error()}))
		return
	}
	if resp.Error != nil {
		s.reply(conn, protocol.Response{V: protocol.ProtocolVersion, JSONRPC: "2.0",
			ID: req.ID, Error: resp.Error})
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"ok": true, "result": resp.Result}))
}

// registry.list 返回全部已注册共享函数（FR-8 注册表）。
func (s *Server) handleRegistryList(conn *websocket.Conn, req protocol.Request) {
	funcs := []map[string]any{}
	for _, f := range s.smanager.FuncList() {
		funcs = append(funcs, map[string]any{
			"name": f.Name, "method": f.Method, "desc": f.Desc, "pluginId": f.PluginID,
		})
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"functions": funcs}))
}

// registry.call 宿主按共享函数名调用（内核路由到所属插件）。
func (s *Server) handleRegistryCall(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		Name    string          `json:"name"`
		Params  json.RawMessage `json:"params"`
		Timeout *int            `json:"timeoutMs"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.Name == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	timeout := 15 * time.Second
	if p.Timeout != nil {
		timeout = time.Duration(*p.Timeout) * time.Millisecond
	}
	var params any
	if len(p.Params) > 0 {
		_ = json.Unmarshal(p.Params, &params)
	}
	resp, err := s.smanager.CallFunc(p.Name, params, timeout)
	if err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"error": err.Error()}))
		return
	}
	if resp.Error != nil {
		s.reply(conn, protocol.Response{V: protocol.ProtocolVersion, JSONRPC: "2.0",
			ID: req.ID, Error: resp.Error})
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"ok": true, "result": resp.Result}))
}

// serveRes 提供共享资源：/res/<相对路径> 映射到 resourcesDir，做目录穿越防护。
func (s *Server) serveRes(w http.ResponseWriter, r *http.Request) {
	rel := strings.TrimPrefix(r.URL.Path, "/res/")
	clean := filepath.Clean("/" + rel) // 归一，阻止 ../ 逃逸
	target := filepath.Join(s.resourcesDir, filepath.FromSlash(strings.TrimPrefix(clean, "/")))
	if !strings.HasPrefix(target+sResourcesSep, s.resourcesDir+sResourcesSep) &&
		target != s.resourcesDir {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	st, err := os.Stat(target)
	if err != nil || st.IsDir() {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, target)
}

const sResourcesSep = string(os.PathSeparator)

// servePluginUI 提供插件 iframe 界面静态文件：/plugin/<id>/<rel> → plugins/<id>/<rel>。
// 与 serveRes 同款目录穿越防护；仅服务声明 ui.type=web 的插件。
func (s *Server) servePluginUI(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/plugin/") // "<id>[/<relpath>]"
	slash := strings.Index(rest, "/")
	if slash < 0 {
		http.NotFound(w, r)
		return
	}
	id, rel := rest[:slash], rest[slash+1:]
	pl := s.smanager.Plugin(id)
	if pl == nil || pl.Manifest.UI.Type == "" {
		http.NotFound(w, r)
		return
	}
	pluginRoot := filepath.Join(s.pluginsDir, id)
	target := filepath.Join(pluginRoot, filepath.FromSlash(filepath.Clean("/"+rel))) // 归一防穿越
	if !strings.HasPrefix(target+sResourcesSep, pluginRoot+sResourcesSep) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	f, err := os.Open(target)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || st.IsDir() {
		http.NotFound(w, r)
		return
	}
	http.ServeContent(w, r, st.Name(), st.ModTime(), f)
}

// serveFavicon 返回应用 logo（octpusY.svg）作为新 BrowserWindow 的站点图标。
func (s *Server) serveFavicon(w http.ResponseWriter, r *http.Request) {
	f, err := os.Open(filepath.Join(s.resourcesDir, "logo", "octpusY.svg"))
	if err != nil {
		w.Header().Set("Content-Type", "image/svg+xml")
		w.WriteHeader(http.StatusNoContent) // 无图标文件时返回 204，避免 400/404 噪音
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", "image/svg+xml")
	http.ServeContent(w, r, "octpusY.svg", time.Time{}, f)
}

// command.list 返回全部可执行命令（FR-9 命令面板），宿主据此补全。
func (s *Server) handleCommandList(conn *websocket.Conn, req protocol.Request) {
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"commands": s.smanager.Commands()}))
}

func (s *Server) reply(conn *websocket.Conn, resp protocol.Response) {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	if err := conn.WriteJSON(resp); err != nil {
		log.Printf("[ws] write: %v", err)
	}
}
