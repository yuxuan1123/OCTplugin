package supervisor

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/octplugin/kernel/internal/perms"
	"github.com/octplugin/kernel/internal/protocol"
)

// Fn 一条已注册的共享函数（FR-8 注册表条目）。
type Fn struct {
	Name     string
	Method   string
	Desc     string
	PluginID string
}

type Manager struct {
	mu         sync.Mutex
	pluginsDir string
	plugins    map[string]*Plugin
	pythonPath string
	gate       *perms.Gate
	// venvPython 为插件解析隔离解释器路径（未建 venv 时回退托管 Python）。
	venvPython func(Manifest) string
	// 阶段F：nodeBin 返回 Node 插件的解释器路径（node.exe）及是否为 Node 插件。
	nodeBin func(Manifest) (string, bool)
	// 共享函数注册表（阶段C）：name → Fn
	fnsMu sync.Mutex
	fns   map[string]Fn
}

func NewManager(pluginsDir string, gate *perms.Gate) *Manager {
	return &Manager{
		pluginsDir: pluginsDir,
		plugins:    make(map[string]*Plugin),
		fns:        make(map[string]Fn),
		gate:       gate,
	}
}

// SetVenvResolver 注入依赖隔离的解释器解析器（deps.Installer.VenvPython）。
func (m *Manager) SetVenvResolver(fn func(Manifest) string) {
	m.venvPython = fn
}

// SetNodeResolver 设置 Node 插件解释器解析器（阶段F：返回 nodeBin + 是否 Node 插件）。
func (m *Manager) SetNodeResolver(fn func(Manifest) (string, bool)) {
	m.nodeBin = fn
}

// interp 按插件类型解析解释器路径；第二返回值 true 表示 Node 插件（用 node 跑 entry）。
func (m *Manager) interp(mf Manifest) (string, bool) {
	if m.nodeBin != nil {
		if b, ok := m.nodeBin(mf); ok {
			return b, true
		}
	} else if isNodeType(mf.Type) {
		return "", true // 已声明 Node 但未注册解析器：暴露缺解释器
	}
	py := m.pythonPath
	if m.venvPython != nil {
		if vp := m.venvPython(mf); vp != "" {
			py = vp
		}
	}
	return py, false
}

// isNodeType 判断插件类型是否为 Node/JS（阶段F）。
func isNodeType(t string) bool {
	lt := strings.ToLower(t)
	return strings.Contains(lt, "node") || strings.Contains(lt, "js") || strings.Contains(lt, "javascript")
}

// Discover 扫描 plugins/<id>/manifest.json，返回清单（不含实例）。
func (m *Manager) Discover() ([]Manifest, error) {
	entries, err := os.ReadDir(m.pluginsDir)
	if err != nil {
		return nil, err
	}
	var out []Manifest
	for _, e := range entries {
		if !e.IsDir() || strings.HasPrefix(e.Name(), "_") || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		id := e.Name()
		mf, err := readManifest(m.pluginsDir, id)
		if err != nil {
			log.Printf("[scan] %s: %v", id, err)
			continue
		}
		out = append(out, mf)
	}
	return out, nil
}

func readManifest(dir, id string) (Manifest, error) {
	paths := []string{
		filepath.Join(dir, id, "manifest.json"),
		filepath.Join(dir, id, "plugin.json"),
	}
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err == nil {
			var mf Manifest
			if err := json.Unmarshal(b, &mf); err != nil {
				return mf, err
			}
			mf.ID = id
			mf.Dir = filepath.Join(dir, id)
			return mf, nil
		}
	}
	return Manifest{}, os.ErrNotExist
}

// readManifestInDir 直接解析某目录（如源码目录）下的 manifest.json，保留 manifest 内嵌 id。
func readManifestInDir(dir string) (Manifest, error) {
	b, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return Manifest{}, err
	}
	var mf Manifest
	if err := json.Unmarshal(b, &mf); err != nil {
		return Manifest{}, err
	}
	mf.ID = strings.TrimSpace(mf.ID)
	mf.Dir = dir
	return mf, nil
}

// copyDir 递归复制目录（阶段G：plugin.import 落盘）。跳过目标已有内容。
func copyDir(src, dst string) error {
	info, err := os.Stat(src)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return fmt.Errorf("%s not a directory", src)
	}
	return copyDirRec(src, dst)
}

func copyDirRec(src, dst string) error {
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	for _, e := range entries {
		s := filepath.Join(src, e.Name())
		d := filepath.Join(dst, e.Name())
		if e.IsDir() {
			if strings.HasPrefix(e.Name(), ".") {
				continue // 跳过隐藏/缓存目录
			}
			if err := copyDirRec(s, d); err != nil {
				return err
			}
			continue
		}
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		b, err := os.ReadFile(s)
		if err != nil {
			return err
		}
		if err := os.WriteFile(d, b, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// StartAll 定位托管 Python，并按 manifest 启动每个加载模式为 always 的插件（MVP：全启动）。
func (m *Manager) StartAll() {
	py, err := LocatePython("3.12")
	if err != nil {
		log.Printf("[kernel] %v", err)
		return
	}
	m.pythonPath = py
	manifests, err := m.Discover()
	if err != nil {
		log.Printf("[kernel] discover: %v", err)
		return
	}
	for _, mf := range manifests {
		if m.gate != nil {
			m.gate.Declare(mf.ID, mf.Permissions) // 登记者权限声明
		}
		p := New(mf, "")
		p.Gate = m.gate
		p.mgr = m
		interp, _ := m.interp(mf) // 按类型选 Python(venv)/Node 解释器
		delete(m.plugins, mf.ID)  // 幂等：重启时不覆盖旧实例
		if err := p.Start(interp); err != nil {
			log.Printf("[kernel] start %s: %v", mf.ID, err)
			continue
		}
		m.mu.Lock()
		m.plugins[mf.ID] = p
		m.mu.Unlock()
		m.RegisterFunctions(mf) // 阶段C：注册共享函数
		log.Printf("[kernel] plugin %s started", mf.ID)
	}
}

func (m *Manager) Plugin(id string) *Plugin {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.plugins[id]
}

// StopPlugin 停止并移除插件实例（释放其持有的文件句柄，供安装/卸载前调用）。
func (m *Manager) StopPlugin(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	p, ok := m.plugins[id]
	if !ok {
		return fmt.Errorf("plugin %s not running", id)
	}
	delete(m.plugins, id)
	p.Stop()
	m.UnregisterFunctions(id) // 阶段C：注销该插件的共享函数
	return nil
}

// Restart 停止并重启插件，重新解析隔离解释器（安装依赖后可用 venv）。
// 插件若已停止（如安装依赖中被 StopPlugin 移除），则按 manifest 重建启动。
func (m *Manager) Restart(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	var mf Manifest
	if p, ok := m.plugins[id]; ok {
		p.Stop()
		mf = p.Manifest
	} else {
		mm, err := readManifest(m.pluginsDir, id)
		if err != nil {
			return err
		}
		mf = mm
		if m.gate != nil {
			m.gate.Declare(mf.ID, mf.Permissions)
		}
	}
	interp, _ := m.interp(mf) // 按类型选 Python(venv)/Node 解释器（阶段F）
	np := New(mf, "")
	np.Gate = m.gate
	np.mgr = m
	if err := np.Start(interp); err != nil {
		return err
	}
	m.plugins[id] = np
	m.RegisterFunctions(mf) // 阶段C：重启后重新注册共享函数
	return nil
}

// Import 把 OCTplugin 格式插件目录复制进 plugins/<id> 并注册启动（阶段G「添加插件」）。
func (m *Manager) Import(srcPath string) error {
	mf, err := readManifestInDir(srcPath)
	if err != nil {
		return fmt.Errorf("source has no valid manifest: %w", err)
	}
	if mf.ID == "" {
		return fmt.Errorf("manifest missing id")
	}
	if err := copyDir(srcPath, filepath.Join(m.pluginsDir, mf.ID)); err != nil {
		return fmt.Errorf("copy plugin: %w", err)
	}
	if m.gate != nil {
		m.gate.Declare(mf.ID, mf.Permissions)
	}
	mf2, err := readManifest(m.pluginsDir, mf.ID)
	if err != nil {
		return err
	}
	interp, _ := m.interp(mf2)
	np := New(mf2, "")
	np.Gate = m.gate
	np.mgr = m
	if err := np.Start(interp); err != nil {
		return fmt.Errorf("start imported plugin: %w", err)
	}
	m.mu.Lock()
	m.plugins[mf2.ID] = np
	m.mu.Unlock()
	m.RegisterFunctions(mf2)
	return nil
}

// Remove 停止并永久删除插件（停进程 + 注销共享函数 + 删目录）。
func (m *Manager) Remove(id string) error {
	if err := m.StopPlugin(id); err != nil && !os.IsNotExist(err) {
		return err
	}
	return os.RemoveAll(filepath.Join(m.pluginsDir, id))
}

func (m *Manager) List() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []string
	for id := range m.plugins {
		out = append(out, id)
	}
	return out
}

// ── 阶段C · 共享函数注册表（FR-8） ────────────────

// RegisterFunctions 按 manifest 声明注册该插件可对外按名调用的共享函数。
func (m *Manager) RegisterFunctions(mf Manifest) {
	m.fnsMu.Lock()
	defer m.fnsMu.Unlock()
	for _, f := range mf.Functions {
		m.fns[f.Name] = Fn{Name: f.Name, Method: f.Method, Desc: f.Desc, PluginID: mf.ID}
	}
}

// CommandItem 命令面板中的一条可执行命令（FR-9）。
type CommandItem struct {
	Name     string         `json:"name"`
	Desc     string         `json:"desc"`
	PluginID string         `json:"pluginId,omitempty"`
	Method   string         `json:"method,omitempty"`
	Params   []CommandParam `json:"params,omitempty"`
}

// Commands 聚合所有插件 manifest.commands 中声明了 method 的可执行命令。
func (m *Manager) Commands() []CommandItem {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []CommandItem
	for _, p := range m.plugins {
		for _, c := range p.Manifest.Commands {
			if c.Method == "" {
				continue
			}
			out = append(out, CommandItem{
				Name: c.Name, Desc: c.Desc, PluginID: p.ID, Method: c.Method, Params: c.Params,
			})
		}
	}
	return out
}

// UnregisterFunctions 移除某插件注册的全部共享函数（停止/移除时调用）。
func (m *Manager) UnregisterFunctions(pluginID string) {
	m.fnsMu.Lock()
	defer m.fnsMu.Unlock()
	for k, v := range m.fns {
		if v.PluginID == pluginID {
			delete(m.fns, k)
		}
	}
}

// FuncList 返回当前全部已注册共享函数（按名称排序）。
func (m *Manager) FuncList() []Fn {
	m.fnsMu.Lock()
	defer m.fnsMu.Unlock()
	out := make([]Fn, 0, len(m.fns))
	for _, v := range m.fns {
		out = append(out, v)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// CallFunc 按共享函数名路由到所属插件执行（经内核中转；未注册/未运行 → error）。
func (m *Manager) CallFunc(name string, params any, timeout time.Duration) (protocol.Response, error) {
	m.fnsMu.Lock()
	f, ok := m.fns[name]
	m.fnsMu.Unlock()
	if !ok {
		return protocol.Response{}, fmt.Errorf("function %q not registered", name)
	}
	pl := m.Plugin(f.PluginID)
	if pl == nil {
		return protocol.Response{}, fmt.Errorf("plugin %s not running", f.PluginID)
	}
	if !pl.Alive() {
		return protocol.Response{}, fmt.Errorf("plugin %s down", f.PluginID)
	}
	return pl.Call(f.Method, params, timeout)
}

func (m *Manager) StopAll() {
	m.mu.Lock()
	for _, p := range m.plugins {
		p.Stop()
	}
	m.plugins = make(map[string]*Plugin)
	m.mu.Unlock()
	m.fnsMu.Lock()
	m.fns = make(map[string]Fn)
	m.fnsMu.Unlock()
}
