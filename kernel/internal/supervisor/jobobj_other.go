//go:build !windows

package supervisor

import "os/exec"

// applyMemLimit 非 Windows 平台：暂无 Job Object/cgroup，先以空实现占位（后续可接 systemd/rLimit）。
func applyMemLimit(cmd *exec.Cmd, memBytes int) (closeJob func(), err error) {
	return func() {}, nil
}
