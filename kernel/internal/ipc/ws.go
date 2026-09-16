package ipc

import (
	"encoding/json"
	"fmt"
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

// EventSink 返回一个插件→内核→宿主的广播闭包，供懒启动/重启的新插件实例注入 Event。
func (s *Server) EventSink() func(src, typ string, data any) {
	return func(src, typ string, data any) { s.broadcast(src, typ, data) }
}

// NotifyState 由内核 Manager 的状态变更回调注入，把插件进程启停推给宿主（source=kernel, type=plugin.state）。
func (s *Server) NotifyState(id, state string) {
	s.broadcast("kernel", "plugin.state", map[string]any{"id": id, "state": state})
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
	case "plugin.start":
		s.handlePluginStart(conn, req)
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
	case "deps.envs":
		s.handleDepsEnvs(conn, req)
	case "registry.list":
		s.handleRegistryList(conn, req)
	case "registry.call":
		s.handleRegistryCall(conn, req)
	case "command.list":
		s.handleCommandList(conn, req)
	case "plugin.getLifecycle":
		s.handlePluginGetLifecycle(conn, req)
	case "plugin.updateSettings":
		s.handlePluginUpdateSettings(conn, req)
	default:
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrMethodNotFound, nil))
	}
}

func (s *Server) handleList(conn *websocket.Conn, req protocol.Request) {
	sums := s.smanager.All()
	res := make([]map[string]any, 0, len(sums))
	for _, sm := range sums {
		res = append(res, map[string]any{
			"pluginId": sm.ID, "name": sm.Name, "type": sm.Type,
			"state": sm.State, "disabled": sm.Disabled, "ui": sm.UI,
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
	mf, ok := s.smanager.Describe(p.PluginID)
	if !ok {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID}))
		return
	}
	decl, granted, high := s.gate.Subset(mf.ID)
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{
		"pluginId": mf.ID, "name": mf.Name, "type": mf.Type, "entry": mf.Entry,
		"loadMode": mf.LoadMode, "uiMode": mf.UIMode, "ui": mf.UI,
		"permissionsDeclared": decl, "permissionsGranted": granted,
		"highRisk":     high,
		"dependencies": mf.Dependencies,
		"commands":     mf.Commands,
		"lifecycle":    mf.LifecyclePolicy,
		"state":        s.smanager.State(p.PluginID),
	}))
}

// plugin.getLifecycle 返回某插件的生命周期策略视图（默认/覆盖/生效）+ 状态。
func (s *Server) handlePluginGetLifecycle(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.PluginID == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	view := s.smanager.Lifecycle(p.PluginID)
	s.reply(conn, protocol.NewResult(req.ID, view))
}

// plugin.updateSettings 保存某插件的用户覆盖并立即生效（内部会重启该插件）。
// 参数与 Override 字段对齐；未提供/为 null 的字段视为不改动，清空传空对象 `{}` 意为恢复 manifest 默认。
func (s *Server) handlePluginUpdateSettings(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string              `json:"pluginId"`
		Override supervisor.Override `json:"override"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.PluginID == "" {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrParse, nil))
		return
	}
	if err := s.smanager.SetLifecycle(p.PluginID, p.Override); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrDepsInstall, map[string]any{"error": err.Error()}))
		return
	}
	s.WireEvents() // 重启会新建 Plugin，需重注事件广播出口
	view := s.smanager.Lifecycle(p.PluginID)
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"ok": true, "effective": view.Effective, "state": view.State}))
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

// plugin.start 显式拉起插件进程（懒启动入口：前端点开未运行插件时调用，避免“加载 UI”隐式启动）。
func (s *Server) handlePluginStart(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
	}
	_ = json.Unmarshal(req.Params, &p)
	if _, err := s.smanager.GetOrStart(p.PluginID); err != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginDown, map[string]any{"error": err.Error()}))
		return
	}
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{"pluginId": p.PluginID, "started": true}))
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
	// 是否已就绪（Python 做顶层包导入探测，Node 看插件目录 node_modules）
	satisfied := s.installer.Ready(mf)
	s.reply(conn, protocol.NewResult(req.ID, map[string]any{
		"pluginId": p.PluginID, "dependencies": rows, "satisfied": satisfied,
	}))
}

// deps.install 安装插件隔离依赖（uv venv + pip/rpm），失败自动回滚。
func (s *Server) handleDepsInstall(conn *websocket.Conn, req protocol.Request) {
	var p struct {
		PluginID string `json:"pluginId"`
		Force    bool   `json:"force"`
	}
	_ = json.Unmarshal(req.Params, &p)
	pl := s.smanager.Plugin(p.PluginID)
	if pl == nil || s.installer == nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginMissing, map[string]any{"pluginId": p.PluginID}))
		return
	}
	// 已就绪则幂等返回（Python 做顶层包导入探测，Node 看插件目录里的 node_modules）。
	// force=true 时跳过该短路，无论 venv 是否存在都按 manifest 依赖重新安装，
	// 用于 requirements.txt 等新增依赖后触发的“重装生效”。
	satisfied := s.installer.Ready(pl.Manifest)
	if satisfied && !p.Force {
		s.reply(conn, protocol.NewResult(req.ID, map[string]any{
			"pluginId": p.PluginID, "installed": true, "restarted": false,
			"satisfied": true, "venvPython": s.installer.VenvPython(pl.Manifest),
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
		"satisfied": true, "venvPython": s.installer.VenvPython(pl.Manifest),
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

// deps.envs 汇总已安装库及版本：项目托管 Python、各插件隔离 venv、Electron 宿主 Node 依赖。
func (s *Server) handleDepsEnvs(conn *websocket.Conn, req protocol.Request) {
	result := make(map[string]any)

	// 1) 项目托管 Python（uv managed 3.12）
	if mp, err := s.installer.ManagedPython(); err == nil {
		entry := map[string]any{"python": mp}
		if pkgs, e2 := s.installer.ListPackages(mp); e2 == nil {
			entry["packages"] = pkgs
		} else {
			entry["error"] = e2.Error()
		}
		result["managed"] = entry
	}

	// 2) 各插件隔离 venv（node 插件无 venv 自然被跳过）
	var plugins []map[string]any
	for _, sum := range s.smanager.All() {
		mf := sum.Manifest
		vp := s.installer.VenvPython(mf)
		if vp == "" {
			continue
		}
		entry := map[string]any{"pluginId": mf.ID, "name": mf.Name, "python": vp, "packages": []deps.Pkg{}}
		if pkgs, e := s.installer.ListPackages(vp); e == nil {
			entry["packages"] = pkgs
		} else {
			entry["error"] = e.Error()
		}
		plugins = append(plugins, entry)
	}
	result["plugins"] = plugins

	// 3) Electron 宿主顶层 Node 依赖
	if pkgs, e := s.installer.NodePackages(); e == nil {
		result["node"] = map[string]any{"packages": pkgs}
	} else {
		result["node"] = map[string]any{"error": e.Error()}
	}

	s.reply(conn, protocol.NewResult(req.ID, result))
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
	// 懒启动：未运行（lazy/prewarm 或已回收）则先按需拉起，再派发请求。
	pl, gerr := s.smanager.GetOrStart(p.PluginID)
	if gerr != nil {
		s.reply(conn, protocol.NewError(req.ID, protocol.ErrPluginDown, map[string]any{
			"pluginId": p.PluginID, "error": gerr.Error(),
		}))
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
	// 未显式给超时时，用插件 manifest 声明的单请求超时（若有）。
	if p.Timeout == nil && pl.Manifest.Limits.RequestTimeoutMs > 0 {
		timeout = time.Duration(pl.Manifest.Limits.RequestTimeoutMs) * time.Millisecond
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
	// UI 是静态资源，不应要求插件进程已运行：lazy/prewarm 未启动时也需能 serve。
	mf, ok := s.smanager.Describe(id)
	if !ok || mf.UI.Type == "" {
		http.NotFound(w, r)
		return
	}
	pluginRoot := filepath.Join(s.pluginsDir, id)
	// 说明：加载 UI 不隐式启动进程。启动改由宿主前端显式 plugin.start 触发（避免 iframe 预载所有插件时把 lazy 全拉起）。
	lm := s.smanager.Lifecycle(id).Effective.LoadMode
	// disabled 插件：不可被 HTTP 触发启动，也不 serve 正常可交互 UI，返回“已禁用”状态页。
	if lm == supervisor.LoadModeDisabled {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintf(w, disabledPaneHTML, id)
		return
	}
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

// disabledPaneHTML 返回给“已禁用”插件的占位页：不承载可交互 UI，明确告知用户服务已停。
const disabledPaneHTML = `<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
font-family:system-ui,sans-serif;background:#f3ecdd;color:#7a7468;gap:10px}
.b{font-size:30px}.t{font-size:15px;font-weight:600;color:#1e1b17}
.s{font-size:12px;color:#8a8377;text-align:center;padding:0 30px}</style></head>
<body><div class="b">i</div><div class="t">该插件服务已禁用</div>
<div class="s">插件「%s」已设为 disabled，其进程不会启动，页面仅为占位。
请在 设置 → 启动与资源 中重新启用。</div></body></html>`
