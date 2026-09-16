"""
conversion/services/
───────────────────────────────────────────────
业务逻辑层，1:1 镜像自 OCTools/services/（仅迁移 conversion 子集）。

  conversion/   星型 + 直接转换调度
  merger/       拼接合并（本插件暂不迁移，保留预留）
"""

__all__ = ["conversion"]