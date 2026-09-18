package supervisor

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/octplugin/kernel/internal/perms"
	"github.com/octplugin/kernel/internal/protocol"
)

// Manifest 描述文件声明（schema）。生命周期策略见 LifecyclePolicy（supervisor/lifecycle.go）。
type Manifest struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Type   string `json:"type"`
	Entry  string `json:"entry"` // 相对插件目录的入口文件，如 main.py
	UIMode string `json:"ui_mode"`
	Dir    string `json:"-"`
	Py3v   string `json:"python_version,omitempty"` // 默认 "3.12"
	LifecyclePolicy
	ManifestFields
}

// Plugin 一个已启动/待启动插件进程的运行实例（低层：进程、stdin/stdout 协议、活性计量）。
// 生命周期状态机（start/idle/stop/backoff-restart）由 Manager 驱动，Plugin 只负责：
//   - 派生子进程、读写 JSON-RPC 行；
//   - 维护 lastPong（任何成功读行视为活性）、lastActivity（空闲回收依据）；
//   - 进程退出回收（Wait/exitCode），并回调 onExit 通知 Manager。
type Plugin struct {
	ID       string
	Manifest Manifest
	Gate     *perms.Gate                     // 敏感操作拦截（插件→内核 gate 请求）
	mgr      *Manager                        // 跨插件 registry.call 路由
	Event    func(src, typ string, data any) // 插件→内核→宿主事件广播出口

	proc        *exec.Cmd
	stdin       io.WriteCloser
	stdout      io.ReadCloser
	limitCloser func()                     // 释放 Job Object 等资源
	onExit      func(crash bool, code int) // 由 Manager 注入；进程退出后回调
	onExitOnce  sync.Once
	waitOnce    sync.Once

	mu           sync.Mutex
	nextID       int
	pending      map[int]chan protocol.Response
	lastPong     time.Time
	lastActivity time.Time
	alive        bool
	exitCode     int
	exitCh       chan struct{} // handleExit 后关闭，供 Stop/supervise 等待
}

// LocatePython 用 uv 定位托管解释器（only-managed，不依赖系统 Python）。
// 带超时保护：uv 缺失/卡住时快速失败，不阻塞内核启动。
func LocatePython(version string) (string, error) {
	uvBin := "uv"
	if v := os.Getenv("OCTRUN_UV"); v != "" {
		uvBin = v
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, uvBin, "python", "find", version).Output()
	if err != nil {
		return "", fmt.Errorf("uv python find %s failed: %w (ensure uv in PATH or OCTRUN_UV)", version, err)
	}
	return strings.TrimSpace(string(out)), nil
}

func New(manifest Manifest) *Plugin {
	return &Plugin{
		ID:       manifest.ID,
		Manifest: manifest,
		pending:  make(map[int]chan protocol.Response),
		exitCh:   make(chan struct{}),
	}
}

// Start 启动插件子进程并开始读循环。解释器由调用方（Manager）解析。
func (p *Plugin) Start(pythonPath string) error {
	pyVersion := p.Manifest.Py3v
	if pyVersion == "" {
		pyVersion = "3.12"
	}
	cmd := exec.Command(pythonPath, filepath.Join(p.Manifest.Dir, p.Manifest.Entry))
	cmd.Dir = p.Manifest.Dir
	hideConsoleWindow(cmd) // Windows: 不弹黑窗口；其它平台：空实现

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

	if closer, err := applyMemLimit(cmd, p.Manifest.Limits.MemBytes); err == nil {
		p.limitCloser = closer
	}

	p.prepare(cmd, stdout, stdin)
	go p.readLoop()
	return nil
}

func (p *Plugin) prepare(cmd *exec.Cmd, stdoutPipe io.ReadCloser, stdinPipe io.WriteCloser) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.proc = cmd
	p.stdin = stdinPipe
	p.stdout = stdoutPipe
	p.alive = true
	p.lastPong = time.Now()
	p.lastActivity = time.Now()
	p.exitCode = -1
}

// readLoop 逐行解析插件 stdout（JSON-RPC）。任何成功读行都刷新 lastPong（活性）与 lastActivity。
// 超长行（> limits.stdout_line_bytes）会被切断丢弃而不终止读循环；EOF 触发回收与 onExit。
func (p *Plugin) readLoop() {
	maxLine := p.Manifest.Limits.StdoutLineBytes
	if maxLine <= 0 {
		maxLine = DefaultStdoutLineBytes
	}
	sc := newStdioScanner(p.stdout, maxLine)
	for {
		line, eof := sc.Next()
		if eof {
			break
		}
		p.touch()
		p.handleLine(line)
	}
	p.handleExit()
}

func (p *Plugin) handleLine(line []byte) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(line, &raw); err != nil {
		return
	}
	if _, isReq := raw["method"]; isReq {
		var req protocol.Request
		if json.Unmarshal(line, &req) != nil {
			return
		}
		p.dispatchGate(req.ID, req.Method, req.Params)
		return
	}
	var resp protocol.Response
	if err := json.Unmarshal(line, &resp); err != nil {
		return
	}
	if resp.ID == 0 {
		return
	}
	p.mu.Lock()
	ch := p.pending[int(resp.ID)]
	delete(p.pending, int(resp.ID))
	p.mu.Unlock()
	if ch != nil {
		ch <- resp
	}
}

// handleExit 进程退出：回收（Wait→exit code），唤醒 pending，回调 onExit。
func (p *Plugin) handleExit() {
	p.waitOnce.Do(func() {
		if p.proc != nil {
			_ = p.proc.Wait()
			if p.proc.ProcessState != nil {
				p.exitCode = p.proc.ProcessState.ExitCode()
			}
		}
	})
	p.mu.Lock()
	p.alive = false
	for id, ch := range p.pending {
		ch <- protocol.NewError(int64(id), protocol.ErrPluginCrashed, nil)
		delete(p.pending, id)
	}
	p.mu.Unlock()
	close(p.exitCh)

	p.onExitOnce.Do(func() {
		if cb := p.onExit; cb != nil {
			cb(true, p.exitCode) // crash=true：受监管期间退出（回收/举报给 Manager 决策）
		}
	})
	if p.limitCloser != nil {
		p.limitCloser()
	}
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

func (p *Plugin) touch() {
	p.mu.Lock()
	p.lastPong = time.Now()
	p.lastActivity = time.Now()
	p.mu.Unlock()
}

// Call 向插件发一次性请求，阻塞等响应（带超时）。发出即视为一次活动。
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
	p.lastActivity = time.Now()
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

	var resp protocol.Response
	var timeoutErr error
	select {
	case resp = <-ch:
		return resp, nil
	case <-time.After(timeout):
		timeoutErr = fmt.Errorf("plugin %s call %s timeout", p.ID, method)
	}
	p.mu.Lock()
	delete(p.pending, id)
	p.mu.Unlock()
	return protocol.Response{}, timeoutErr
}

// Ping 发送心跳（不更新 lastPong——活性以 pong/任何读行为准）。
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
}

func (p *Plugin) Alive() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.alive
}

// LastResponse 最近一次成功读行（活性）时间。
func (p *Plugin) LastResponse() time.Time {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.lastPong
}

// LastActivity 最近一次活动时间（空闲回收依据）。
func (p *Plugin) LastActivity() time.Time {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.lastActivity
}

// ExitCh 进程退出通知 channel（handleExit 后关闭）。
func (p *Plugin) ExitCh() <-chan struct{} { return p.exitCh }

// ExitCode 进程退出码（未退出时为 -1）。
func (p *Plugin) ExitCode() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.exitCode
}

// PendingCount 进行中的业务请求数（空闲回收据此判断是否有在途请求）。
func (p *Plugin) PendingCount() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.pending)
}

// Kill 强制终止进程并等待回收（读循环会在 EOF 后自行 handleExit/onExit）。
func (p *Plugin) Kill() {
	p.mu.Lock()
	alive := p.alive
	p.mu.Unlock()
	if p.proc != nil && alive {
		_ = p.proc.Process.Kill()
	}
}

// Stop 停止：Kill + 等待读循环完成回收（进程真正退出、释放句柄，供 Install 时删 venv）。
func (p *Plugin) Stop() {
	p.mu.Lock()
	alive := p.alive
	p.mu.Unlock()
	if p.proc != nil && alive {
		_ = p.proc.Process.Kill()
	}
	select {
	case <-p.exitCh:
	case <-time.After(3 * time.Second):
	}
}
