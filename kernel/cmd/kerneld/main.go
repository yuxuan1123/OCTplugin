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
	root, err := filepath.Abs(filepath.Dir(os.Args[0]))
	if err != nil {
		root, _ = os.Getwd()
	}
	// 内核 stdout 只用于输出 auth 行（D8），其余日志一律走 stderr。
	log.SetOutput(os.Stderr)

	pluginsDir := filepath.Join(root, "..", "plugins")
	storeDir := filepath.Join(root, "..", "store")
	resourcesDir := filepath.Join(filepath.Dir(root), "resources") // 阶段E：共享资源根目录

	// 授权持久化 store/perms.json（阶段A）
	gate := perms.NewGate(filepath.Join(storeDir, "perms.json"))

	manager := supervisor.NewManager(pluginsDir, gate)
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
	manager.SetNodeResolver(installer.NodeInterp) // 阶段F：Node 插件用 node 解释器

	token := newToken(32)
	srv := ipc.NewServer(token, manager, gate, installer, resourcesDir, pluginsDir)

	ln, port, err := srv.Listen("127.0.0.1:0")
	if err != nil {
		log.Fatalf("listen: %v", err)
	}

	manager.StartAll()
	defer manager.StopAll()
	srv.WireEvents() // 阶段E：插件事件广播出口（StartAll 之后才可注入）

	go srv.Serve(ln)

	auth, _ := json.Marshal(map[string]any{
		"auth": map[string]any{"port": port, "token": token},
	})
	fmt.Println(string(auth))
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
