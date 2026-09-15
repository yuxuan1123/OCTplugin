# M1 · 内核 ↔ 宿主 WebSocket 消息 Schema

> 传输：Electron Main 为 WS 客户端，Go 内核 daemon 为 WS 服务端，`localhost` 单连接（1:1）。
> 通用封装：**JSON-RPC 2.0 扩展**（保留 `id/result/error`，新增 `stream` 与事件）。
> 约定：协议版本字段 `v`（当前 `1`）；所有 id 用整型；UTF-8。
> 依据：要求.md FR-6/FR-7/FR-8。冲突时以要求.md 为准。

---

## 1. 通用信封

### 1.1 请求（宿主→内核）

```jsonc
{
  "v": 1,
  "jsonrpc": "2.0",
  "id": 1,                 // 整型，宿主自增，贯穿单连接
  "method": "plugin.call",
  "params": { ... }        // 按方法定，可省略
}
```

### 1.2 响应（内核→宿主）

```jsonc
{ "v": 1, "jsonrpc": "2.0", "id": 1, "result": { ... } }
// 或错误
{ "v": 1, "jsonrpc": "2.0", "id": 1,
  "error": { "code": -32000, "message": "plugin not running", "data": { ... } } }
```

### 1.3 通知（无 id，单向；两端均可用）

```jsonc
{ "v": 1, "jsonrpc": "2.0", "method": "plugin.stateChanged",
  "params": { "pluginId": "home", "state": "RUNNING" } }
```

### 1.4 流式（stream 标记）

- 一个**方法请求**若返回流，首个响应 `result` 携带 `"stream": {"id": "...", "meta": {...}}`。
- 后续数据走**通知** `stream.data`；结束/失败走 `stream.end`。

```jsonc
// 请求 stream 化方法
{ "v":1, "jsonrpc":"2.0", "id":7, "method":"plugin.stream",
  "params": { "pluginId":"translation", "method":"translate.stream", "params":{...} } }

// ① 头（内核→宿主）
{ "v":1, "jsonrpc":"2.0", "id":7,
  "result": { "stream": { "id": "s_001", "meta": { "mime":"text/plain", "encoding":"utf-8" } } } }

// ② 数据通知（可多帧）
{ "v":1, "jsonrpc":"2.0", "method":"stream.data",
  "params": { "streamId":"s_001", "seq":1, "chunk":"..." } }

// ③ 结束 / 失败
{ "v":1, "jsonrpc":"2.0", "method":"stream.end",
  "params": { "streamId":"s_001", "ok":true, "error": null } }
```

---

## 2. 方法命名空间总表

| 命名空间 | 方向 | 说明 |
|---|---|---|
| `kernel.*` | 双向 | 连接握手、心跳、capabilities |
| `plugin.*` | 宿主→内核 | 插件枚举/生命周期 |
| `deps.*` | 宿主→内核 | 依赖查询/安装触发 |
| `perms.*` | 双向 | 权限位图：申请/答复/撤销 |
| `registry.*` | 宿主→内核 | 共享函数注册表查询/调用 |
| `command.*` | 宿主→内核 | 命令候选（补全） |
| `ui.*` | 宿主→内核 | 宿主 UI 侧动作（弹窗/Toast）结果上报 |
| `stream.*` | 内核→宿主 | 流式数据帧（见 1.4） |

---

## 3. 方法明细

### 3.1 `kernel.hello` 连接握手

```
宿主→内核   kernel.hello  { "client": "octplugin-host", "protocolVersion":1 }
内核→宿主   result        { "kernel":"kerneld", "protocolVersion":1,
                            "capabilities":{ "python":"3.12-tv", "uv":true,
                                             "limits":{ "maxPlugins":64 } } }
```

### 3.2 `kernel.ping` 心跳（app 级，非插件心跳）

```
宿主→内核   kernel.ping  {}
内核→宿主   result       {  }
```

### 3.3 `plugin.list`

```
宿主→内核   plugin.list   {}
内核→宿主   result        { "plugins":[ { "pluginId":"home", "name":"首页",
                            "type":"Python", "state":"RUNNING",
                            "loadMode":"lazy", "enabled":true,
                            "permissions":["network","execute_command"],
                            "hasUI":true } ] }
```

### 3.4 生命周期操作（`plugin.enable` / `plugin.disable` / `plugin.setLoadMode` / `plugin.reload`）

```
宿主→内核   plugin.setLoadMode  { "pluginId":"home", "loadMode":"auto_recycle",
                                  "idleTimeoutMin":10 }
内核→宿主   result              { "pluginId":"home", "loadMode":"auto_recycle" }
```

错误：`PLUGIN_NOT_FOUND` / `PLUGIN_INVALID_STATE`。

### 3.5 `plugin.call` 一次性调用

```
宿主→内核   plugin.call   { "pluginId":"translation",
                            "method":"translate", "params":{ "text":"..." } }
宿主→内核   plugin.call   { "pluginId":"translation", "method":"translate",
                            "params":{...}, "timeoutMs":15000 }   // 可选
内核→宿主   result        { "ok": true, "result": { "translated":"..." } }
```

- 触发**按需启动**：目标插件未运行（lazy/auto_recycle）时，内核先启动再调用（内部排队）。
- 失败返回结构化错误，`error.data` 含 `pluginId/method/state`。

### 3.6 `plugin.invokeAction` desc/iframe 控件动作

```
宿主→内核   plugin.invokeAction { "pluginId":"spell", "objectName":"btn_gen",
                                  "action":"generate", "value": null }
内核→宿主   result              { "ok":true, "ui": { ... }, "toast":{ "message":"已生成","kind":"success" } }
```

### 3.7 流式调用（见 1.4 示例）

适用于实时日志、逐句翻译、大文件。宿主不必攒完，逐帧刷新 UI。

---

## 4. 依赖管理

### 4.1 `deps.installRequest` 安装前确认（走 UI）

```
宿主→内核   deps.install { "pluginId":"spell" }
内核→宿主   result { "ok":true, "step":"preview",
                     "deps":[ { "name":"numpy","version":"2.0.2","source":"pypi" } ] }
            // deps 为空则直接进入安装
内核→宿主（通知） deps.status { "pluginId":"spell", "progress":42, "status":"downloading" }
内核→宿主   result 的完成或失败：
            { "ok":true, "installed":[ ... ] } | { "ok":false, "error":"..." }
```

- 依赖**共享缓存**在 `~/.plugin-cache`，**运行环境隔离**（要求.md D1）。
- Python 一律走 uv + `only-managed` + 托管 3.12（见架构落地拆分 D3/D4）。

---

## 5. 权限位图（要求.md FR-7）

### 5.1 首次授权 / 新权限

```
内核→宿主（通知） perms.request
        { "requestId":"r_1", "pluginId":"home", "permission":"execute_command",
          "detail":"执行 net.ps1 以切换网络加速", "highRisk": true }
宿主→内核        perms.answer
        { "requestId":"r_1", "granted": true, "remember": false }
内核→宿主（通知） perms.resolved { "requestId":"r_1", "granted":true }
```

- `highRisk:true` 的操作即使已授权，宿主仍弹**二次确认**（要求.md FR-10）。

### 5.2 运行时撤销

```
宿主→内核   perms.revoke { "pluginId":"home", "permission":"file_write" }
内核→宿主   result       { "pluginId":"home", "revoked":"file_write" }
内核→宿主（通知） perms.changed { "pluginId":"home",
                                  "permissions":["network","execute_command"] }
```

### 5.3 未授权拦截

```
宿主→内核   plugin.call { "pluginId":"x", "method":"writeFile", ... }
内核→宿主   error { "code": -32005, "message":"permission denied: file_write",
                    "data":{ "permission":"file_write", "granted":false } }
```

权限枚举：`file_read` `file_write` `network` `execute_command` `spawn_process` `local_model`。

---

## 6. 共享函数注册表（要求.md FR-8）

### 6.1 `registry.list` 查询

```
宿主→内核   registry.list {}
内核→宿主   result { "functions":[ { "name":"ocr.localize",
                            "provider":"translation",
                            "paramsSchema":{...}, "returns":"text" } ] }
```

### 6.2 `registry.call` 路由到提供方

```
宿主→内核   registry.call { "function":"ocr.localize", "params":{...},
                            "timeoutMs": 10000 }
内核→宿主   result        { "ok":true, "result":{ ... } }
```

- 内核内部完成"目标插件未运行则按需启动" + 白名单校验（拒绝特定调用方）。

---

## 7. 命令 / 热键

### 7.1 `command.suggest` 参数级补全

```
宿主→内核   command.suggest { "command":"translate", "args":{"--from":"","--to":""} }
内核→宿主   result { "suggestions":{ "--from":["en","zh","ja"], "--to":["zh"] } }
```

### 7.2 `command.execute`

```
宿主→内核   command.execute { "command":"translate",
                              "args":{"--from":"en","--to":"zh","text":"..."} }
内核→宿主   result { "ok":true, "result":{...} }
```

### 7.3 热键冲突

```
宿主→内核   hotkey.register { "spec":"ctrl+shift+t", "action":"toggle_translate",
                              "pluginId":"translation" }
内核→宿主   error { "code":-32006, "message":"hotkey conflict", "data":{ "holder":"spell" } }
宿主→内核   用户裁决后 hotkey.register / hotkey.release
```

---

## 8. 宿主 UI 事件

```
内核→宿主（通知） ui.toast        { "kind":"success", "message":"翻译完成" }
宿主→内核       ui.dialogResult { "dialogId":"d_1", "confirmed":true }
```

---

## 9. 错误码

| code | 含义 |
|---|---|
| -32700 | 解析错误 |
| -32601 | 方法不存在 |
| -32000 | 插件进程未运行 / 启动失败 |
| -32001 | 调用超时（含 timeoutMs） |
| -32002 | 插件崩溃 / 已停止 |
| -32003 | 插件不存在（PLUGIN_NOT_FOUND） |
| -32004 | 插件状态不合法（disabled/install_failed） |
| -32005 | 权限被拦截（FR-7） |
| -32006 | 热键冲突 |
| -32007 | 依赖未安装 |
| -32008 | 依赖安装失败 |
| -32009 | 流式流不存在 / 已关闭 |

---

## 10. 连接生命周期

```
宿主启动 → 拉起内核子进程 → WS connect → kernel.hello(握手)
         → 恢复持久状态(权限/启用/加载模式) → 通知 UI 渲染
         → 心跳 kernel.ping(每 15s; 12s 无响应视为宿主断开)
宿主退出 → 有意关闭(active close) → 内核回收插件并退出
内核异常 → 宿主监听到进程退出 → 重启内核 → 重新握手 + 幂等恢复
```

- **消息幂等**：带副作用的方法（enable/setLoadMode/perms/install）要求幂等键或由宿主在断线重连后按持久快照重放。

---

## 11. 推荐实现顺序（M1 验收）

1. `kernel.hello` / `kernel.ping` 握手与心跳。
2. `plugin.list` + `plugin.stateChanged` 事件。
3. 用 **home** 插件做首个联调：`plugin.call` 触发 `home.checkNetwork`（走权限门 `execute_command`）。
4. 用 `stream.*` 验证一条流式数据（实时日志）。
- 验收：握手≤1s、`plugin.call` 本地往返≤5ms、流首字节≤50ms。