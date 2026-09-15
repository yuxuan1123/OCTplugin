//go:build windows

package supervisor

import (
	"fmt"
	"os/exec"
	"syscall"
	"unsafe"
)

// Windows Job Object 实现进程内存上限：进程内存超限即被系统终止（等同先 SIGTERM 再 SIGKILL）。
// 属「尽力而为」：任何一步失败仅打日志，不阻断启动。

const (
	jobObjectExtendedLimitInformation = 9
	jobObjectLimitProcessMemory       = 0x00000100
	jobObjectLimitKillOnJobClose      = 0x00002000
)

type winIOCOUNTERS struct {
	ReadOperationCount  uint64
	WriteOperationCount uint64
	OtherOperationCount uint64
	ReadTransferCount   uint64
	WriteTransferCount  uint64
	OtherTransferCount  uint64
}

type winBasicLimitInformation struct {
	PerProcessUserTimeLimit int64
	PerJobUserTimeLimit     int64
	LimitFlags              uint32
	MinimumWorkingSetSize   uintptr
	MaximumWorkingSetSize   uintptr
	ActiveProcessLimit      uint32
	Affinity                uintptr
	PriorityClass           uint32
	SchedulingClass         uint32
}

type winExtendedLimitInformation struct {
	BasicLimitInformation winBasicLimitInformation
	IoInfo                winIOCOUNTERS
	ProcessMemoryLimit    uintptr
	JobMemoryLimit        uintptr
	PeakProcessMemoryUsed uintptr
	PeakJobMemoryUsed     uintptr
}

// applyMemLimit 用 Windows Job Object 给子进程加内存上限。返回的 closeJob 在进程停止后调用以释放句柄。
func applyMemLimit(cmd *exec.Cmd, memBytes int) (closeJob func(), err error) {
	if memBytes <= 0 || cmd == nil || cmd.Process == nil {
		return func() {}, nil
	}
	mod := syscall.NewLazyDLL("kernel32.dll")
	fCreate := mod.NewProc("CreateJobObjectW")
	fSet := mod.NewProc("SetInformationJobObject")
	fAssign := mod.NewProc("AssignProcessToJobObject")
	fClose := mod.NewProc("CloseHandle")

	job, _, _ := fCreate.Call(0, 0)
	if job == 0 {
		return func() {}, fmt.Errorf("CreateJobObject failed")
	}
	info := winExtendedLimitInformation{}
	info.BasicLimitInformation.LimitFlags = jobObjectLimitProcessMemory | jobObjectLimitKillOnJobClose
	info.ProcessMemoryLimit = uintptr(memBytes)
	rc, _, _ := fSet.Call(job, jobObjectExtendedLimitInformation, uintptr(unsafe.Pointer(&info)), unsafe.Sizeof(info))
	if rc == 0 {
		_, _, _ = fClose.Call(job)
		return func() {}, fmt.Errorf("SetInformationJobObject failed")
	}
	// 通过 pid 打开进程句柄（os.Process.Handle 未导出，跨平台不可用）。
	const procSetQuota = 0x0100
	ph, err := syscall.OpenProcess(procSetQuota|syscall.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if err != nil {
		_, _, _ = fClose.Call(job)
		return func() {}, fmt.Errorf("OpenProcess(pid=%d): %w", cmd.Process.Pid, err)
	}
	rc, _, _ = fAssign.Call(job, uintptr(ph))
	_, _, _ = fClose.Call(uintptr(ph))
	if rc == 0 {
		_, _, _ = fClose.Call(job)
		return func() {}, fmt.Errorf("AssignProcessToJobObject failed")
	}
	return func() { _, _, _ = fClose.Call(job) }, nil
}
