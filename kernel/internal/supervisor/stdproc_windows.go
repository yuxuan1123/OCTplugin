//go:build windows

package supervisor

import (
	"os/exec"
	"syscall"
)

// hideConsoleWindow 隐藏子进程的控制台窗口（Windows：避免启动 Python/Node 插件时弹出黑窗口）。
func hideConsoleWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
}