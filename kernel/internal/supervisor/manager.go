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

// RegState 插件生命周期状态（Phase 1/2 状态机：registered→running→idle→stopped）。
type RegState int

const (
	RegRegistered RegState = iota // 已注册，未启动（lazy/prewarm 或等待）
	RegStarting                   // 正在启动（GetOrStart 并发等待）
	RegRunning                    // 运行中（健康）
	RegIdle                       // 已空闲回收，可随时按需重启
	RegStopped                    // 停止（禁用/手动停/退避超限/正常退出）
)

func (s RegState) String() string {
	switch s {
	case RegRegistered:
		return "REGISTERED"
	case RegStarting:
		return "STARTING"
	case RegRunning:
		return "RUNNING"
	case RegIdle:
		return "IDLE"
	default:
		return "STOPPED"
	}
}

// recycleCheckInterval 空闲回收扫描周期（Phase 1 约定 10s）。
const recycleCheckInterval = 10 * time.Second

// reg 一个插件的生命周期登记项。
type reg struct {
	id string
	mf Manifest

	disabled    bool // load_mode == disabled
	depsPending bool // 依赖未就绪（如 Node 无解释器），暂不可启动

	mu      sync.Mutex
	state   RegState
	running *Plugin
	startCh chan struct{} // 一次启动尝试的完成信号（close 表示本次尝试已结束）
	crash   []time.Time   // 滑动窗口内崩溃时间戳（退避重启依据）
}

type Manager struct {
	mu         sync.Mutex
	pluginsDir string
	storeDir   string
	regs       map[string]*reg
	overrides  overridesFile // 用户覆盖 map[pluginID]Override（持久化 store/overrides.json）
	pythonPath string
	gate       *perms.Gate
	venvPython func(Manifest) string
	depsReady  func(Manifest) bool // Python 依赖就绪判定（RequirementsSatisfied）；nil 时默认就绪
	nodeBin    func(Manifest) (string, bool)
	onSpawn    func(*Plugin)          // 注入 Event 广播出口（内核装配）
	onState    func(id, state string) // 进程状态变更回调（内核装配→宿主刷新 UI）

	fnsMu sync.Mutex
	fns   map[string]Fn
}

func NewManager(pluginsDir string, gate *perms.Gate) *Manager {
	return &Manager{
		pluginsDir: pluginsDir,
		regs:       make(map[string]*reg),
		fns:        make(map[string]Fn),
		gate:       gate,
	}
}

// SetVenvResolver 注入依赖隔离的解释器解析器（deps.Installer.VenvPython）。
func (m *Manager) SetVenvResolver(fn func(Manifest) string) {
	m.mu.Lock()
	m.venvPython = fn
	m.mu.Unlock()
}

// SetDepsReadyResolver 注入 Python 依赖就绪判定（deps.Installer.RequirementsSatisfied）。
// 未注入视为无条件就绪（等价于旧行为）。
func (m *Manager) SetDepsReadyResolver(fn func(Manifest) bool) {
	m.mu.Lock()
	m.depsReady = fn
	m.mu.Unlock()
}

// SetNodeResolver 设置 Node 插件解释器解析器。
func (m *Manager) SetNodeResolver(fn func(Manifest) (string, bool)) {
	m.mu.Lock()
	m.nodeBin = fn
	m.mu.Unlock()
}

// SetOnSpawn 设置插件启动后的钩子（内核用它注入 Event 广播出口）。
func (m *Manager) SetOnSpawn(fn func(*Plugin)) {
	m.mu.Lock()
	m.onSpawn = fn
	m.mu.Unlock()
}

// SetOnState 注入进程状态变更回调（内核装配→宿主实时刷新侧栏圆点）。
func (m *Manager) SetOnState(fn func(id, state string)) {
	m.mu.Lock()
	m.onState = fn
	m.mu.Unlock()
}

// emitState 触发状态变更回调（异步，避免持有锁时回调卡住启动路径）。
func (m *Manager) emitState(r *reg, state string) {
	m.mu.Lock()
	fn := m.onState
	m.mu.Unlock()
	if fn != nil {
		go fn(r.id, state)
	}
}

func (m *Manager) applySpawnWire(p *Plugin) {
	m.mu.Lock()
	fn := m.onSpawn
	m.mu.Unlock()
	if fn != nil {
		fn(p)
	}
}

// interp 按插件类型解析解释器路径；第二返回值 true 表示 Node 插件。
func (m *Manager) interp(mf Manifest) (string, bool) {
	// 读取快照（可在启动时安全调用）
	m.mu.Lock()
	nodeBin, venvPython := m.nodeBin, m.venvPython
	pythonPath := m.pythonPath
	m.mu.Unlock()

	if nodeBin != nil {
		if b, ok := nodeBin(mf); ok {
			return b, true
		}
	} else if isNodeType(mf.Type) {
		return "", true // 已声明 Node 但未注册解析器：暴露缺解释器
	}
	py := pythonPath
	if venvPython != nil {
		if vp := venvPython(mf); vp != "" {
			py = vp
		}
	}
	return py, false
}

func isNodeType(t string) bool {
	lt := strings.ToLower(t)
	return strings.Contains(lt, "node") || strings.Contains(lt, "js") || strings.Contains(lt, "javascript")
}

// pluginReady 判断插件是否可启动（依赖是否就绪）。
// Node：需 node 解释器可用；Python：需已注入的就绪判定通过（默认通过，等价旧行为）。
func (m *Manager) pluginReady(mf Manifest) bool {
	if isNodeType(mf.Type) {
		m.mu.Lock()
		nodeBin := m.nodeBin
		m.mu.Unlock()
		if nodeBin == nil {
			return false
		}
		if _, ok := nodeBin(mf); !ok {
			return false
		}
		return true
	}
	m.mu.Lock()
	dr := m.depsReady
	m.mu.Unlock()
	if dr == nil {
		return true
	}
	return dr(mf)
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
				return mf, fmt.Errorf("manifest parse: %w", err)
			}
			mf.ID = id
			mf.Dir = filepath.Join(dir, id)
			return mf, normalizeManifest(&mf)
		}
	}
	return Manifest{}, os.ErrNotExist
}

func readManifestInDir(dir string) (Manifest, error) {
	b, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return Manifest{}, err
	}
	var mf Manifest
	if err := json.Unmarshal(b, &mf); err != nil {
		return Manifest{}, fmt.Errorf("manifest parse: %w", err)
	}
	mf.ID = strings.TrimSpace(mf.ID)
	mf.Dir = dir
	return mf, normalizeManifest(&mf)
}

// normalizeManifest 对已解析的 manifest 应用默认值并校验：旧清单缺失新字段自动回填；非法取值上抛跳过。
func normalizeManifest(mf *Manifest) error {
	mf.LifecyclePolicy.ApplyDefaults()
	if err := mf.LifecyclePolicy.Validate(); err != nil {
		return fmt.Errorf("manifest %s lifecycle invalid: %w", mf.ID, err)
	}
	return nil
}

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
				continue
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

// register 以 manifest 创建登记项并声明权限。返回已有登记（幂等）。
func (m *Manager) register(mf Manifest) *reg {
	// 合并用户覆盖（override 优先于 manifest 默认值）
	mf.LifecyclePolicy = m.OverrideFor(mf.ID).apply(mf.LifecyclePolicy)
	if m.gate != nil {
		m.gate.Declare(mf.ID, mf.Permissions)
	}
	local := &reg{
		id:       mf.ID,
		mf:       mf,
		disabled: mf.LoadMode == LoadModeDisabled,
		startCh:  make(chan struct{}),
	}
	// 注意：登记阶段不探测依赖（pluginReady 可能同步 import，慢），只做快速扫描，
	// 保证 plugin.list / 侧栏「注册表预显示」立即可用。依赖就绪在真正启动时判定（startAlways / GetOrStart）。
	m.mu.Lock()
	if old, ok := m.regs[mf.ID]; ok {
		m.mu.Unlock()
		return old
	}
	m.regs[mf.ID] = local
	m.mu.Unlock()
	return local
}

func (m *Manager) getReg(id string) *reg {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.regs[id]
}

// RegisterAll 扫描并快速登记全部插件（不探测依赖），供 plugin.list / 侧栏预显示。
// 在打印 auth 前同步调用，保证宿主首屏即拿到完整插件清单。
func (m *Manager) RegisterAll() {
	manifests, err := m.Discover()
	if err != nil {
		log.Printf("[kernel] discover: %v", err)
		return
	}
	for _, mf := range manifests {
		m.register(mf)
	}
}

// StartAll 启动常驻（load_mode=always）插件。每个插件在独立 goroutine 中
// 先做依赖就绪判定（带超时）再启动——一个插件依赖卡住只影响它自己，
// 不会阻塞其它插件或整体启动。
func (m *Manager) StartAll() {
	if py, err := LocatePython("3.12"); err != nil {
		log.Printf("[kernel] %v", err)
	} else {
		m.mu.Lock()
		m.pythonPath = py
		m.mu.Unlock()
	}
	for _, r := range m.snapshotRegs() {
		if r.disabled || r.mf.LoadMode != LoadModeAlways {
			continue
		}
		r := r
		go m.startAlways(r)
	}
}

// startAlways 启动单个常驻插件：先判定依赖就绪（导入探测，超时内完成），
// 未就绪则标记 depsPending 并保持停置，仅影响该插件自身。
func (m *Manager) startAlways(r *reg) {
	ready := m.pluginReady(r.mf)
	r.mu.Lock()
	r.depsPending = !ready
	r.mu.Unlock()
	if !ready {
		log.Printf("[kernel] plugin %s deps not ready; held", r.id)
		return
	}
	if _, err := m.startNow(r); err != nil {
		log.Printf("[kernel] start always %s: %v", r.id, err)
	}
}

func (m *Manager) snapshotRegs() []*reg {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]*reg, 0, len(m.regs))
	for _, r := range m.regs {
		out = append(out, r)
	}
	return out
}

// Plugin 返回当前运行中的实例（未运行返回 nil）。
func (m *Manager) Plugin(id string) *Plugin {
	r := m.getReg(id)
	if r == nil {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.state != RegRunning {
		return nil
	}
	return r.running
}

// State 返回插件生命周期状态字符串。
func (m *Manager) State(id string) string {
	r := m.getReg(id)
	if r == nil {
		return "UNKNOWN"
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.state.String()
}

// LifecycleView 返回某插件的生命周期策略视图：默认值(manifest) / 用户覆盖 / 最终生效。
type LifecycleView struct {
	PluginID  string          `json:"pluginId"`
	Defaults  LifecyclePolicy `json:"defaults"`  // manifest 声明值（未覆盖）
	Override  Override        `json:"override"`  // 用户覆盖（nil 字段=未覆盖）
	Effective LifecyclePolicy `json:"effective"` // 最终生效（default+override）
	State     string          `json:"state"`
}

// Lifecycle 返回某插件的生命周期策略信息（供设置 UI 渲染）。
func (m *Manager) Lifecycle(id string) LifecycleView {
	r := m.getReg(id)
	if r == nil {
		return LifecycleView{PluginID: id}
	}
	m.mu.Lock()
	ov := m.overrides[id]
	m.mu.Unlock()
	// defaults：重读 manifest 原文得到未覆盖前的策略默认值。
	mf, err := readManifest(m.pluginsDir, id)
	if err != nil {
		mf = r.mf
		mf.LifecyclePolicy = r.mf.LifecyclePolicy
	}
	effective := ov.apply(mf.LifecyclePolicy)
	effective.ApplyDefaults()
	return LifecycleView{
		PluginID:  id,
		Defaults:  mf.LifecyclePolicy,
		Override:  ov,
		Effective: effective,
		State:     m.State(id),
	}
}

// SetLifecycle 保存某插件的用户覆盖并立即生效：持久化后按新策略重启该插件。
func (m *Manager) SetLifecycle(id string, ov Override) error {
	if err := ValidateOverride(ov); err != nil {
		return err
	}
	if err := m.SaveOverride(id, ov); err != nil {
		return err
	}
	// 立即应用：刷新登记项里的策略（load_mode 变化需要重新评估是否常驻）。
	r := m.getReg(id)
	if r != nil {
		var mf Manifest
		rmf, err := readManifest(m.pluginsDir, id)
		if err == nil {
			mf = rmf
		} else {
			r.mu.Lock()
			mf = r.mf
			r.mu.Unlock()
		}
		mf.LifecyclePolicy = ov.apply(mf.LifecyclePolicy)
		r.mu.Lock()
		r.mf = mf
		r.disabled = mf.LoadMode == LoadModeDisabled
		r.mu.Unlock()
		// 重启以应用新资源/心跳/回收策略；disabled 则仅停止运行实例但保留登记。
		if r.disabled {
			r.mu.Lock()
			if p := r.running; p != nil {
				p.Stop()
			}
			r.state = RegStopped
			r.running = nil
			r.mu.Unlock()
			m.emitState(r, "STOPPED")
			return nil
		}
		_ = m.Restart(id)
	}
	return nil
}

// GetOrStart 按需启动（懒启动核心）。running→直接返回；starting→等待；idle/stopped→重新 spawn。
func (m *Manager) GetOrStart(id string) (*Plugin, error) {
	r := m.getReg(id)
	if r == nil {
		return nil, fmt.Errorf("unknown plugin %q", id)
	}
	if err := m.beginIfNeeded(r); err != nil {
		return nil, err
	}
	// 登记阶段未探测依赖，这里按需判定（带超时），保持 lazy/prewarm 的就绪门槛不变。
	if !m.pluginReady(r.mf) {
		r.mu.Lock()
		r.depsPending = true
		r.mu.Unlock()
		return nil, fmt.Errorf("plugin %s dependencies not installed", r.id)
	}
	return m.startNow(r)
}

// beginIfNeeded 在启动前置检查：禁用/依赖未就绪直接返回错误。
func (m *Manager) beginIfNeeded(r *reg) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.disabled {
		return fmt.Errorf("plugin %s is disabled", r.id)
	}
	if r.depsPending {
		return fmt.Errorf("plugin %s dependencies not installed", r.id)
	}
	return nil
}

// startNow 真正启动一次插件（幂等；并发安全）。状态字段由 r.mu 保护。
func (m *Manager) startNow(r *reg) (*Plugin, error) {
	r.mu.Lock()
	switch r.state {
	case RegRunning:
		p := r.running
		r.mu.Unlock()
		return p, nil
	case RegStarting:
		ch := r.startCh
		r.mu.Unlock()
		select {
		case <-ch:
		case <-time.After(10 * time.Second):
			return nil, fmt.Errorf("plugin %s start timed out", r.id)
		}
		r.mu.Lock()
		p := r.running
		st := r.state
		r.mu.Unlock()
		if st != RegRunning || p == nil {
			return nil, fmt.Errorf("plugin %s failed to start", r.id)
		}
		return p, nil
	default:
		r.state = RegStarting
		sig := make(chan struct{})
		r.startCh = sig // 本次尝试的信号，供并发调用方等待
		r.mu.Unlock()
		m.emitState(r, RegStarting.String())
		return m.spawn(r, sig)
	}
}

func (m *Manager) spawn(r *reg, sig chan struct{}) (*Plugin, error) {
	interp, _ := m.interp(r.mf)
	p := New(r.mf)
	p.Gate = m.gate
	p.mgr = m
	if err := p.Start(interp); err != nil {
		r.mu.Lock()
		r.state = RegStopped
		r.mu.Unlock()
		m.emitState(r, "STOPPED")
		close(sig)
		return nil, err
	}
	// 回收后每次 spawn 重新注入崩溃回调（用当前 reg 与当前实例，避免旧实例退出误伤新实例）
	p.onExit = func(_ bool, code int) { m.onProcessExit(r, p, code) }

	r.mu.Lock()
	r.running = p
	r.state = RegRunning
	r.mu.Unlock()
	m.emitState(r, "RUNNING")
	close(sig)

	m.applySpawnWire(p)
	m.RegisterFunctions(r.mf) // lazy 启动同样注册共享函数（幂等）
	log.Printf("[kernel] plugin %s started (load_mode=%s)", r.id, r.mf.LoadMode)
	go m.supervise(r, p)
	return p, nil
}

// supervise 运行期守护：心跳(发 ping)、活性检查(超时踢除)、空闲回收、进程退出→退避重启。
func (m *Manager) supervise(r *reg, p *Plugin) {
	hbMs := r.mf.Heartbeat.IntervalMs
	if hbMs <= 0 {
		hbMs = DefaultHeartbeatIntervalMs
	}
	toMs := r.mf.Heartbeat.TimeoutMs
	if toMs <= 0 {
		toMs = DefaultHeartbeatTimeoutMs
	}
	hb := time.NewTicker(time.Duration(hbMs) * time.Millisecond)
	defer hb.Stop()
	chk := time.NewTicker(time.Second)
	defer chk.Stop()
	rec := time.NewTicker(recycleCheckInterval)
	defer rec.Stop()

	for {
		select {
		case <-p.ExitCh():
			m.onProcessExit(r, p, p.ExitCode())
			return
		case <-hb.C:
			p.Ping()
		case <-chk.C:
			if p.Alive() && time.Since(p.LastResponse()) > time.Duration(toMs)*time.Millisecond {
				log.Printf("[kernel] plugin %s heartbeat timeout (%.0fs no pong); kicking", r.id, time.Since(p.LastResponse()).Seconds())
				p.Kill() // 触发 ExitCh → onProcessExit → 退避重启
			}
		case <-rec.C:
			if m.recycleDue(r, p) {
				m.recycle(r, p)
				return
			}
		}
	}
}

// recycleDue 是否满足空闲回收条件：允许回收、非常驻、无在途请求、空闲超阈值。
func (m *Manager) recycleDue(r *reg, p *Plugin) bool {
	rc := r.mf.Recycle
	if !rc.IdleRecycle || rc.MaxIdleMs <= 0 || rc.BackgroundTasks {
		return false
	}
	if r.mf.LoadMode == LoadModeAlways {
		return false // 常驻不回
	}
	if p.PendingCount() > 0 {
		return false
	}
	return time.Since(p.LastActivity()) > time.Duration(rc.MaxIdleMs)*time.Millisecond
}

// recycle 优雅回收：摘除运行态→发 shutdown→Kill→状态置 idle。
func (m *Manager) recycle(r *reg, p *Plugin) {
	r.mu.Lock()
	if r.state != RegRunning {
		r.mu.Unlock()
		return
	}
	r.state = RegIdle
	r.running = nil
	r.mu.Unlock()
	m.emitState(r, "IDLE")
	log.Printf("[kernel] plugin %s idle; recycling", r.id)

	grace := r.mf.Recycle.GracefulShutdownMs
	if grace <= 0 {
		grace = DefaultGracefulShutdownMs
	}
	done := make(chan struct{})
	go func() {
		_, _ = p.Call("shutdown", nil, time.Duration(grace)*time.Millisecond)
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(time.Duration(grace)*time.Millisecond + time.Second):
	}
	p.Stop() // 等进程真正退出
}

// onProcessExit 进程退出/崩溃：按 resilience 决策是否退避重启。
// 只有当退出的实例仍是当前运行实例时才处理（避免旧实例退出误伤新实例）。
func (m *Manager) onProcessExit(r *reg, p *Plugin, code int) {
	r.mu.Lock()
	if r.state != RegRunning || r.running != p {
		r.mu.Unlock()
		return // 已被回收/主动停止/已被新实例替代，不重启
	}
	r.state = RegStopped
	r.running = nil
	delay, allow := m.nextBackoffLocked(r, code)
	r.mu.Unlock()
	m.emitState(r, "STOPPED")

	if !allow {
		log.Printf("[kernel] plugin %s paused (exit code %d): auto-restart exhausted/disabled", r.id, code)
		return
	}
	log.Printf("[kernel] plugin %s crashed (exit %d); restarting in %v", r.id, code, delay)
	time.AfterFunc(delay, func() {
		if _, err := m.startNow(r); err != nil {
			log.Printf("[kernel] plugin %s restart failed: %v", r.id, err)
		}
	})
}

// nextBackoffLocked 用滑动窗口计算下次重启延迟。调用方须持有 r.mu。
// 正常退出码（crash_exit_codes）或禁用自动重启 → allow=false。
func (m *Manager) nextBackoffLocked(r *reg, code int) (time.Duration, bool) {
	rc := r.mf.Resilience
	if rc.AutoRestart == nil || !*rc.AutoRestart {
		return 0, false
	}
	for _, ec := range rc.CrashExitCodes {
		if ec == code {
			return 0, false // 正常退出，不重启
		}
	}
	now := time.Now()
	win := time.Duration(rc.MaxRestartsWindowMs) * time.Millisecond
	if win <= 0 {
		win = DefaultMaxRestartsWindowMs * time.Millisecond
	}
	max := rc.MaxRestarts
	if max <= 0 {
		max = DefaultMaxRestarts
	}
	cutoff := now.Add(-win)
	fresh := r.crash[:0]
	for _, t := range r.crash {
		if t.After(cutoff) {
			fresh = append(fresh, t)
		}
	}
	if len(fresh) >= max {
		r.crash = fresh
		return 0, false // 窗口内超限：暂停自动重启
	}
	r.crash = append(fresh, now)

	attempt := len(r.crash) - 1
	base := rc.BackoffBaseMs
	if base <= 0 {
		base = DefaultBackoffBaseMs
	}
	backoffMax := rc.BackoffMaxMs
	if backoffMax <= 0 {
		backoffMax = DefaultBackoffMaxMs
	}
	d := base << attempt // 指数退避 base*2^attempt
	if d > backoffMax || attempt > 30 {
		d = backoffMax
	}
	return time.Duration(d) * time.Millisecond, true
}

// ── 生命周期相关管理接口 ────────────────────────────

// StopPlugin 停止并移除插件实例（释放文件句柄，供安装/卸载前调用）。
func (m *Manager) StopPlugin(id string) error {
	r := m.getReg(id)
	if r == nil {
		return fmt.Errorf("plugin %s not found", id)
	}
	r.mu.Lock()
	if p := r.running; p != nil {
		p.Stop()
	}
	r.state = RegStopped
	r.running = nil
	r.mu.Unlock()
	m.UnregisterFunctions(id)
	m.mu.Lock()
	delete(m.regs, id)
	m.mu.Unlock()
	return nil
}

// Restart 停止并重启插件，重新解析隔离解释器（安装依赖后可用 venv）。
func (m *Manager) Restart(id string) error {
	r := m.getReg(id)
	if r == nil {
		mf, err := readManifest(m.pluginsDir, id)
		if err != nil {
			return err
		}
		r = m.register(mf)
	}
	r.mu.Lock()
	if p := r.running; p != nil {
		p.Stop()
	}
	r.state = RegStopped
	r.running = nil
	r.mu.Unlock()
	_, err := m.startNow(r)
	if err == nil {
		// 依赖安装/修复后重启成功：解除 depsPending，恢复 GetOrStart/按需启动。
		r.mu.Lock()
		r.depsPending = false
		r.mu.Unlock()
	}
	return err
}

// Import 把 OCTplugin 格式插件目录复制进 plugins/<id> 并启动。
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
	mf2, err := readManifest(m.pluginsDir, mf.ID)
	if err != nil {
		return err
	}
	r := m.register(mf2)
	_, err = m.startNow(r)
	return err
}

// Remove 停止并永久删除插件（停进程 + 注销共享函数 + 删目录）。
func (m *Manager) Remove(id string) error {
	if err := m.StopPlugin(id); err != nil && !os.IsNotExist(err) {
		return err
	}
	return os.RemoveAll(filepath.Join(m.pluginsDir, id))
}

// List 返回当前运行中的插件 id。
func (m *Manager) List() []string {
	out := []string{}
	for _, r := range m.snapshotRegs() {
		r.mu.Lock()
		if r.state == RegRunning {
			out = append(out, r.id)
		}
		r.mu.Unlock()
	}
	sort.Strings(out)
	return out
}

// PluginSummary 一个插件注册摘要（含未启动的 lazy/idle/disabled）。
type PluginSummary struct {
	Manifest
	State    string `json:"state"`
	Disabled bool   `json:"disabled,omitempty"`
}

// All 返回全部已注册插件的摘要（含未启动），供宿主渲染侧栏/管理列表 —— 不要求 running。
// lazy/prewarm/disabled 插件即使未启动也能被列出与详情查看。
func (m *Manager) All() []PluginSummary {
	regs := m.snapshotRegs()
	out := make([]PluginSummary, 0, len(regs))
	for _, r := range regs {
		r.mu.Lock()
		out = append(out, PluginSummary{
			Manifest: r.mf,
			State:    r.state.String(),
			Disabled: r.disabled,
		})
		r.mu.Unlock()
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Describe 返回某插件的注册信息（即使未运行），ok=false 表示未知插件。
func (m *Manager) Describe(id string) (Manifest, bool) {
	r := m.getReg(id)
	if r == nil {
		return Manifest{}, false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.mf, true
}

// ── 阶段C · 共享函数注册表（FR-8） ────────────────

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
	var out []CommandItem
	for _, r := range m.snapshotRegs() {
		for _, c := range r.mf.Commands {
			if c.Method == "" {
				continue
			}
			out = append(out, CommandItem{
				Name: c.Name, Desc: c.Desc, PluginID: r.id, Method: c.Method, Params: c.Params,
			})
		}
	}
	return out
}

func (m *Manager) UnregisterFunctions(pluginID string) {
	m.fnsMu.Lock()
	defer m.fnsMu.Unlock()
	for k, v := range m.fns {
		if v.PluginID == pluginID {
			delete(m.fns, k)
		}
	}
}

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

// CallFunc 按共享函数名路由到所属插件执行（懒启动：未运行先 GetOrStart）。
func (m *Manager) CallFunc(name string, params any, timeout time.Duration) (protocol.Response, error) {
	m.fnsMu.Lock()
	f, ok := m.fns[name]
	m.fnsMu.Unlock()
	if !ok {
		return protocol.Response{}, fmt.Errorf("function %q not registered", name)
	}
	pl, err := m.GetOrStart(f.PluginID)
	if err != nil {
		return protocol.Response{}, err
	}
	if !pl.Alive() {
		return protocol.Response{}, fmt.Errorf("plugin %s down", f.PluginID)
	}
	return pl.Call(f.Method, params, timeout)
}

// StopAll 停止全部运行中的插件（内核退出时调用）。
func (m *Manager) StopAll() {
	for _, r := range m.snapshotRegs() {
		r.mu.Lock()
		if p := r.running; p != nil {
			p.Stop()
		}
		r.state = RegStopped
		r.running = nil
		r.mu.Unlock()
	}
	m.fnsMu.Lock()
	m.fns = make(map[string]Fn)
	m.fnsMu.Unlock()
}
