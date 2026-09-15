package protocol

import "encoding/json"

// JSON-RPC 2.0 扩展信封（M1-WS消息Schema）。v=1。

const ProtocolVersion = 1

type Request struct {
	V       int             `json:"v"`
	JSONRPC string          `json:"jsonrpc"`
	ID      int64           `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type Response struct {
	V       int       `json:"v"`
	JSONRPC string    `json:"jsonrpc"`
	ID      int64     `json:"id,omitempty"`
	Result  any       `json:"result,omitempty"`
	Error   *RPCError `json:"error,omitempty"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type Notification struct {
	V       int    `json:"v"`
	JSONRPC string `json:"jsonrpc"`
	Method  string `json:"method"`
	Params  any    `json:"params,omitempty"`
}

// 业务错误码（对应 M1-WS消息Schema §9）
const (
	ErrParse          = -32700
	ErrMethodNotFound = -32601
	ErrPluginDown     = -32000
	ErrTimeout        = -32001
	ErrPluginCrashed  = -32002
	ErrPluginMissing  = -32003
	ErrPluginState    = -32004
	ErrPermDenied     = -32005
	ErrHotkeyConflict = -32006
	ErrDepsMissing    = -32007
	ErrDepsInstall    = -32008
	ErrStreamClosed   = -32009
	ErrIO             = -32010
)

var ErrText = map[int]string{
	ErrParse:          "parse error",
	ErrMethodNotFound: "method not found",
	ErrPluginDown:     "plugin process not running",
	ErrTimeout:        "call timeout",
	ErrPluginCrashed:  "plugin crashed",
	ErrPluginMissing:  "plugin not found",
	ErrPluginState:    "plugin invalid state",
	ErrPermDenied:     "permission denied",
	ErrHotkeyConflict: "hotkey conflict",
	ErrDepsMissing:    "dependency not installed",
	ErrDepsInstall:    "dependency install failed",
	ErrStreamClosed:   "stream closed or missing",
	ErrIO:             "io error",
}

func NewResult(id int64, res any) Response {
	return Response{V: ProtocolVersion, JSONRPC: "2.0", ID: id, Result: res}
}

func NewError(id int64, code int, data any) Response {
	return Response{V: ProtocolVersion, JSONRPC: "2.0", ID: id,
		Error: &RPCError{Code: code, Message: ErrText[code], Data: data}}
}

func (e Response) Marshal() []byte {
	b, _ := json.Marshal(e)
	return b
}
