"""
conversion/core/utils/
───────────────────────────────────────────────
通用工具（最小功能单元），1:1 迁移自 OCTools/core/utils/。
"""

from core.utils.file_handler import (  # noqa: F401
    file_exists,
    read_text,
    write_text,
    safe_name,
    ensure_outdir,
)

__all__ = ["file_exists", "read_text", "write_text", "safe_name", "ensure_outdir"]