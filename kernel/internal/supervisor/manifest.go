package supervisor

// FR-1 描述文件声明（schema v1）。MVP 先落地 permissions / dependencies / commands。

// DepDecl 声明一项依赖文件及其处理方式（FR-3 自动识别）。
type DepDecl struct {
	Path     string `json:"path"`               // 如 requirements.txt、package.json、go.mod、Cargo.toml
	Manager  string `json:"manager"`            // python/uv、npm、go、cargo、maven、bundler、dotnet
	Explicit bool   `json:"explicit,omitempty"` // true=必须显式确认（FR-3）
}

// CommandDecl 声明一条命令（FR-9 命令面板自动补全）。
type CommandDecl struct {
	Name   string         `json:"name"` // 如 /translate
	Desc   string         `json:"desc"`
	Method string         `json:"method,omitempty"` // 可选：执行目标=本插件内方法；空则仅作展示
	Params []CommandParam `json:"params,omitempty"`
}

type CommandParam struct {
	Name  string   `json:"name"`
	Type  string   `json:"type"`            // string/int/bool/string[]
	Needs []string `json:"needs,omitempty"` // 提供给该参数的候选，如语言列表
}

// FnDecl 声明一个可供宿主/其他插件按名调用的共享函数（FR-8，经内核中转）。
type FnDecl struct {
	Name   string `json:"name"`   // 全局唯一，如 np1.version
	Method string `json:"method"` // 插件内响应该方法名
	Desc   string `json:"desc,omitempty"`
}

// UIDecl 声明插件的 iframe 网页界面（阶段G：M3 iframe 插件页）。
// Type=web 表示宿主用 iframe 加载该插件自带 HTML UI；空则插件为无界面后端。
type UIDecl struct {
	Type  string `json:"type"`  // "web"
	Entry string `json:"entry"` // 相对插件根目录，如 "ui/index.html"
}

// ManifestFields 嵌入 Manifest 的声明式字段。
type ManifestFields struct {
	Permissions  []string      `json:"permissions"`
	Dependencies []DepDecl     `json:"dependencies"`
	Commands     []CommandDecl `json:"commands"`
	Functions    []FnDecl      `json:"functions"`
	UI           UIDecl        `json:"ui,omitempty"`
}
