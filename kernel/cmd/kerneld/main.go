package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/octplugin/kernel/internal/deps"
	"github.com/octplugin/kernel/internal/ipc"
	"github.com/octplugin/kernel/internal/perms"
	"github.com/octplugin/kernel/internal/supervisor"
)

func main() {
	// 项目根 = 内核二进制所在目录的上一级（.../kernel/ → 项目根）。
	// 依赖隔离 deps/<id>、宿主 host、store 等均以项目根为基准（见 environment.md）。
	bin := os.Args[0]
	if exe, e2 := os.Executable(); e2 == nil && exe != "" {
		bin = exe
	}
	root, err := filepath.Abs(filepath.Dir(filepath.Dir(bin)))
	if err != nil {
		root, _ = os.Getwd()
	}
	// 内核 stdout 只用于输出 auth 行（D8），其余日志一律走 stderr。
	log.SetOutput(os.Stderr)

	pluginsDir := filepath.Join(root, "plugins")
	storeDir := filepath.Join(root, "store")
	resourcesDir := filepath.Join(root, "resources") // 阶段E：共享资源根目录

	// 授权持久化 store/perms.json（阶段A）
	gate := perms.NewGate(filepath.Join(storeDir, "perms.json"))

	manager := supervisor.NewManager(pluginsDir, gate)
	// 阶段三：用户生命周期覆盖持久化 store/overrides.json
	manager.SetStoreDir(storeDir)
	// 阶段B：依赖隔离——uv 从环境注入，失败回退 "uv"
	uvBin := os.Getenv("OCTRUN_UV")
	if uvBin == "" {
		uvBin = "uv"
	}
	installer := deps.New(root, uvBin)
	// 依赖源设置优先级：UI 持久化配置(store/deps.json) 为基础，环境变量临时覆盖。
	installer.UseConfigFile(filepath.Join(storeDir, "deps.json"))
	if _, err := installer.LoadConfig(); err != nil {
		log.Printf("[kernel] load deps.json: %v", err)
	}
	// 下载镜像源：OCTPY_INDEX 可多个，空格/逗号分隔，首个最优先
	if v := os.Getenv("OCTPY_INDEX"); v != "" {
		var urls []string
		for _, u := range strings.FieldsFunc(v, func(r rune) bool { return r == ',' || r == ' ' || r == ';' }) {
			if u = strings.TrimSpace(u); u != "" {
				urls = append(urls, u)
			}
		}
		installer.SetIndex(urls)
	}
	// 自定义下载缓存目录：OCTUV_CACHE_DIR
	if v := os.Getenv("OCTUV_CACHE_DIR"); v != "" {
		installer.SetCacheDir(v)
	}
	manager.SetVenvResolver(installer.VenvPython)
	manager.SetDepsReadyResolver(installer.RequirementsSatisfied) // 依赖就绪判定（导入探测）
	manager.SetNodeResolver(installer.NodeInterp)                 // 阶段F：Node 插件用 node 解释器

	token := newToken(32)
	srv := ipc.NewServer(token, manager, gate, installer, resourcesDir, pluginsDir)

	ln, port, err := srv.Listen("127.0.0.1:0")
	if err != nil {
		log.Fatalf("listen: %v", err)
	}

	// 事件广播/状态回调先装配好，供随后异步启动的插件实例使用。
	manager.SetOnState(func(id, state string) { srv.NotifyState(id, state) })
	manager.SetOnSpawn(func(p *supervisor.Plugin) { p.Event = srv.EventSink() })
	srv.WireEvents()
	defer manager.StopAll()

	// 同步快速登记全部插件：plugin.list / 侧栏「注册表预显示」在 auth 前即完整。
	manager.RegisterAll()

	go srv.Serve(ln)

	auth, _ := json.Marshal(map[string]any{
		"auth": map[string]any{"port": port, "token": token},
	})
	fmt.Println(string(auth))
	// 插件异步、逐插件并发启动：不阻塞 auth；各插件状态经 plugin.state 事件
	// 实时推送（灰色→启动→运行），单个插件依赖卡住不影响其他插件与整体 UI。
	go manager.StartAll()
	// 阻塞
	select {}
}

func newToken(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
