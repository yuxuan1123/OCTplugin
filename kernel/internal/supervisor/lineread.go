package supervisor

import (
	"bufio"
	"bytes"
	"io"
	"log"
)

// stdioScanner 逐行读取插件 stdout，但对超过 maxLine 的超长行做「切断并丢弃、继续读下一行」，
// 而不是像 bufio.Scanner 那样直接 ErrTooLong 终止整个读循环（避免一个大输出误杀插件进程）。
type stdioScanner struct {
	r        *bufio.Reader
	maxLine  int
	pending  []byte // 累积的未到换行的当前行（仅当总长 <= maxLine 时保留）
	oversize bool   // 当前行已确定超长，剩余丢弃
}

func newStdioScanner(r io.Reader, maxLine int) *stdioScanner {
	return &stdioScanner{r: bufio.NewReaderSize(r, 1<<20), maxLine: maxLine}
}

// Next 返回下一行（不含换行符）。可读（无行可读且未 EOF）时返回 ok=false、err=io.EOF 表示结束。
func (s *stdioScanner) Next() (line []byte, eof bool) {
	for {
		frag, err := s.r.ReadSlice('\n')
		// frag 可能同时含多行？ReadSlice 只到第一个 '\n'，不会跨行。
		if !s.oversize {
			if len(s.pending)+len(frag) <= s.maxLine {
				s.pending = append(s.pending, frag...)
			} else {
				s.oversize = true
				s.pending = nil // 停止累积，避免超长行撑爆内存
			}
		}
		switch err {
		case nil: // 拿到一行，含末尾 '\n'
			s.oversize = false // 行已结束复位，供下一行使用
			if len(s.pending) == 0 {
				continue // 超长行被丢弃，继续
			}
			ln := bytes.TrimSuffix(s.pending, []byte{'\n'})
			s.pending = nil
			if len(ln) == 0 {
				// 空行：正常（协议要求处理或忽略，交由上层）
				ln = []byte{}
			}
			return ln, false
		case bufio.ErrBufferFull:
			continue // 行未读完，继续积累/丢弃
		case io.EOF:
			if len(s.pending) > 0 {
				ln := bytes.TrimSuffix(s.pending, []byte{'\n'})
				s.pending = nil
				return ln, false
			}
			return nil, true
		default:
			return nil, true
		}
	}
}

// logSkipped 供上层在丢弃超长行时打告警。
func logSkipped(id string, n int) {
	log.Printf("[plugin %s] stdout line exceeded %d bytes; truncated and skipped", id, n)
}
