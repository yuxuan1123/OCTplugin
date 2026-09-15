package supervisor

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/octplugin/kernel/internal/perms"
	"github.com/octplugin/kernel/internal/protocol"
)

// 心跳间隔 / 超时（对应 OCTools：5s ping，12s 无响应判定卡死）
const (
	HeartbeatInterval = 5 * time.Second
	HeartbeatTimeout  = 3 * HeartbeatInterval
)

type Manifest struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Type     string `json:"type"`
	Entry    string `json:"entry"` // 相对插件目录的入口文件，如 main.py
	UIMode   string `json:"ui_mode"`
	Dir      string `json:"-"`
	Py3v     string `json:"python_version,omitempty"` // 默认 "3.12"
	LoadMode string `json:"load_mode"`                // always/lazy/auto_recycle
	ManifestFields
}

type Plugin struct {
	ID       string
	Manifest Manifest
	Gate     *perms.Gate                     // 敏感操作拦截（插件→内核 gate 请求）
	mgr      *Manager                        // 阶段C：供插件发起的跨插件 registry.call 路由
	Event    func(src, typ string, data any) // 阶段E：插件→内核→宿主的广播出口
	proc     *exec.Cmd
	stdin    io.WriteCloser
	stdout   io.ReadCloser

	mu      sync.Mutex
	nextID  int
	pending map[int]chan protocol.Response

	lastPong time.Time

	alive bool
}

// LocatePython 用 uv 定位托管解释器（only-managed，不依赖系统 Python）。
func LocatePython(version string) (string, error) {
	uvBin := "uv"
	if v := os.Getenv("OCTRUN_UV"); v != "" {
		uvBin = v
	}
	out, err := exec.Command(uvBin, "python", "find", version).Output()
	if err != nil {
		// 显式报错，避免静默回退系统 Python
		return "", fmt.Errorf("uv python find %s failed: %w (ensure uv in PATH or OCTRUN_UV)", version, err)
	}
	return strings.TrimSpace(string(out)), nil
}

// Start 启动插件子进程：解释器 = 托管 3.12，跑 manifest.Entry。
func New(manifest Manifest, pythonPath string) *Plugin {
	return &Plugin{
		ID:       manifest.ID,
		Manifest: manifest,
		pending:  make(map[int]chan protocol.Response),
	}
}

func (p *Plugin) Start(pythonPath string) error {
	pyVersion := p.Manifest.Py3v
	if pyVersion == "" {
		pyVersion = "3.12"
	}
	cmd := exec.Command(pythonPath, p.Manifest.Entry)
	cmd.Dir = p.Manifest.Dir
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr

	if err := cmd.Start(); err != nil {
		return err
	}

	p.proc = cmd
	p.stdin = stdin
	p.stdout = stdout
	p.alive = true
	p.lastPong = time.Now()

	go p.readLoop()
	go p.heartbeat()
	return nil
}

// readLoop 逐行解析插件 stdout 的 JSON-RPC（插件 stdout 必须纯净为协议）。
// 兼容两种方向：插件对宿主请求的 Response、插件主动发给内核的 Request（gate.*）。
func (p *Plugin) readLoop() {
	sc := bufio.NewScanner(p.stdout)
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		var raw map[string]json.RawMessage
		if err := json.Unmarshal(sc.Bytes(), &raw); err != nil {
			continue
		}
		if _, isReq := raw["method"]; isReq {
			// 插件 → 内核 请求（gate 服务，FR-7/FR-10）
			var req protocol.Request
			if json.Unmarshal(sc.Bytes(), &req) != nil {
				continue
			}
			p.dispatchGate(req.ID, req.Method, req.Params)
			continue
		}
		var resp protocol.Response
		if err := json.Unmarshal(sc.Bytes(), &resp); err != nil {
			continue
		}
		if resp.ID == 0 {
			continue
		}
		p.mu.Lock()
		ch := p.pending[int(resp.ID)]
		delete(p.pending, int(resp.ID))
		p.mu.Unlock()
		if ch != nil {
			ch <- resp
		}
	}
	p.markDead()
}

// dispatchGate 处理插件对受保护服务的请求：先过权限位图，未授权统一 -32005。
func (p *Plugin) dispatchGate(id int64, method string, params []byte) {
	var code int
	var ok bool
	var result any
	var data any

	switch method {
	case "gate.file_read":
		var pr struct {
			Perm string `json:"perm"`
			Path string `json:"path"`
		}
		_ = json.Unmarshal(params, &pr)
		if pr.Perm == "" {
			pr.Perm = perms.FileRead
		}
		if p.Gate != nil && p.Gate.Check(p.ID, pr.Perm) != nil {
			code, ok, data = protocol.ErrPermDenied, false, map[string]any{"pluginId": p.ID, "perm": pr.Perm}
			break
		}
		b, err := os.ReadFile(pr.Path)
		if err != nil {
			code, ok, data = protocol.ErrIO, false, map[string]any{"error": err.Error()}
			break
		}
		ok, result = true, map[string]any{"path": pr.Path, "bytes": len(b)}
	case "gate.file_exists":
		var pr struct {
			Path string `json:"path"`
		}
		_ = json.Unmarshal(params, &pr)
		_, err := os.Stat(pr.Path)
		ok, result = true, map[string]any{"exists": err == nil}
	case "registry.call":
		// 阶段C：插件 A → 内核 → 插件 B 的跨插件调用（FR-8）
		var pr struct {
			Name   string          `json:"name"`
			Params json.RawMessage `json:"params"`
		}
		_ = json.Unmarshal(params, &pr)
		if p.mgr == nil {
			code, ok, data = protocol.ErrMethodNotFound, false, nil
			break
		}
		var in any
		if len(pr.Params) > 0 {
			_ = json.Unmarshal(pr.Params, &in)
		}
		resp, err := p.mgr.CallFunc(pr.Name, in, 15*time.Second)
		if err != nil {
			code, ok, data = protocol.ErrPluginMissing, false, map[string]any{"error": err.Error()}
			break
		}
		if resp.Error != nil {
			code, ok, data = resp.Error.Code, false, resp.Error.Data
			break
		}
		ok, result = true, map[string]any{"ok": true, "result": resp.Result}
	case "event.emit":
		// 阶段E：插件 → 内核 → 宿主 事件广播
		var pr struct {
			Type string `json:"type"`
			Data any    `json:"data"`
		}
		_ = json.Unmarshal(params, &pr)
		if p.Event != nil {
			p.Event(p.ID, pr.Type, pr.Data)
		}
		ok, result = true, map[string]any{"ok": true}
	default:
		code, ok, data = protocol.ErrMethodNotFound, false, nil
	}
	p.writeGateReply(id, code, ok, result, data)
}

func (p *Plugin) writeGateReply(id int64, code int, ok bool, result, data any) {
	var r protocol.Response
	if ok {
		r = protocol.NewResult(id, result)
	} else {
		r = protocol.NewError(id, code, data)
	}
	b, _ := json.Marshal(r)
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.alive {
		_, _ = p.stdin.Write(append(b, '\n'))
	}
}

func (p *Plugin) markDead() {
	p.mu.Lock()
	p.alive = false
	if p.proc != nil {
		_ = p.proc.Process.Kill()
	}
	// 唤醒所有 pending，避免泄漏
	for id, ch := range p.pending {
		ch <- protocol.NewError(int64(id), protocol.ErrPluginCrashed, nil)
		delete(p.pending, id)
	}
	p.mu.Unlock()
}

func (p *Plugin) Alive() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.alive
}

// Call 向插件发一次性请求，阻塞等响应（带超时）。
func (p *Plugin) Call(method string, params any, timeout time.Duration) (protocol.Response, error) {
	p.mu.Lock()
	if !p.alive {
		p.mu.Unlock()
		return protocol.Response{}, fmt.Errorf("plugin %s not alive", p.ID)
	}
	p.nextID++
	id := p.nextID
	ch := make(chan protocol.Response, 1)
	p.pending[id] = ch
	req := protocol.Request{V: protocol.ProtocolVersion, JSONRPC: "2.0", ID: int64(id), Method: method}
	if params != nil {
		b, _ := json.Marshal(params)
		req.Params = b
	}
	b, _ := json.Marshal(req)
	_, err := p.stdin.Write(append(b, '\n'))
	p.mu.Unlock()
	if err != nil {
		return protocol.Response{}, err
	}

	select {
	case resp := <-ch:
		return resp, nil
	case <-time.After(timeout):
		p.mu.Lock()
		delete(p.pending, id)
		p.mu.Unlock()
		return protocol.Response{}, fmt.Errorf("plugin %s call %s timeout", p.ID, method)
	}
}

// Ping 向插件发心跳。
func (p *Plugin) Ping() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.alive {
		return
	}
	p.nextID++
	id := p.nextID
	req := protocol.Request{V: protocol.ProtocolVersion, JSONRPC: "2.0", ID: int64(id), Method: "ping"}
	b, _ := json.Marshal(req)
	_, _ = p.stdin.Write(append(b, '\n'))
	p.lastPong = time.Now()
}

func (p *Plugin) heartbeat() {
	t := time.NewTicker(HeartbeatInterval)
	defer t.Stop()
	for range t.C {
		if !p.Alive() {
			return
		}
		p.Ping()
	}
}

func (p *Plugin) Stop() {
	p.mu.Lock()
	v := p.alive
	if p.proc != nil {
		_ = p.proc.Process.Kill()
	}
	p.alive = false
	p.mu.Unlock()
	if v {
		p.markDead()
	}
	// 等待进程真正退出、释放文件句柄（否则 Windows 删除 venv 会 Access denied）
	p.waitExited(3 * time.Second)
}

// waitExited 等待进程回收，超时兜底。
func (p *Plugin) waitExited(timeout time.Duration) {
	if p.proc == nil {
		return
	}
	done := make(chan struct{})
	go func() {
		_ = p.proc.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(timeout):
	}
}
