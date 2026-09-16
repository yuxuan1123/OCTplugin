# -*- coding: utf-8 -*-
"""临时：验证 pypandoc 缺失时插件仍可启动，且 md->docx 走纯 Python 引擎。用完即删。"""
import os
import sys
import tempfile

# 用一个会在导入时报 ImportError 的 pypandoc 桩，精确模拟「venv 缺 pypandoc」
_stub = tempfile.mkdtemp(prefix="stub_")
open(os.path.join(_stub, "pypandoc.py"), "w", encoding="utf-8").write(
    "raise ImportError('No module named pypandoc')\n")
sys.path.insert(0, _stub)

sys.path.insert(0, r"d:\Project\ElectronProject\OCTplugin\plugins\conversion")
os.chdir(r"d:\Project\ElectronProject\OCTplugin\plugins\conversion")

# 1) 引擎可导入（不因 pypandoc 崩溃）
from core.engines import document_engine as d
assert d.pypandoc is None, "pypandoc 应被屏蔽为 None"
print("OK: document_engine imports, pypandoc =", d.pypandoc)

# 2) 整个转换入口可导入
import services.conversion as C
print("OK: services.conversion imports")

# 3) md->docx 应走纯 Python 高级引擎
import tempfile
tmp = tempfile.mkdtemp()
md = os.path.join(tmp, "a.md")
open(md, "w", encoding="utf-8").write("# 标题\n\n正文内容。\n")
out = os.path.join(tmp, "a.docx")
logs = []
ok = C.convert(md, out, log=lambda m: logs.append(str(m)))
print("OK: md->docx via pure-python:", ok, os.path.exists(out))
for l in logs[-3:]:
    print("  ", l)

print("ALL PASS")