# -*- coding: utf-8 -*-
"""验证打好的 .difypkg：解包 → 剥掉 dify_plugin 依赖 exec 出纯函数 → 跑真实合同样本

用法：python verify_pkg.py <x.difypkg> [样本PDF...]
默认样本：real_samples/contract0.pdf real_samples/contract1.pdf
"""
import os
import re
import sys
import tempfile
import time
import zipfile

pkg = sys.argv[1] if len(sys.argv) > 1 else "red-seal-detector-0.0.11.difypkg"
samples = sys.argv[2:] or [
    "real_samples/contract0.pdf",
    "real_samples/contract1.pdf",
]

tmp = tempfile.mkdtemp(prefix="sealpkg_")
with zipfile.ZipFile(pkg) as z:
    z.extractall(tmp)

src = os.path.join(tmp, "tools", "red_seal_detector.py")
s = open(src, encoding="utf-8").read()
s = re.sub(r"class ToolParameters\(BaseModel\):.*?(?=\ndef |\nclass )", "", s, flags=re.S)
s = s.split("class RedSealDetectorTool")[0]
s = "\n".join(l for l in s.splitlines() if not l.startswith("from dify_plugin"))
ns = {"File": object}
exec(compile(s, src, "exec"), ns)

try:
    import scipy  # noqa: F401

    backend = f"scipy {scipy.__version__}"
except ImportError:
    backend = "BFS 兜底（无 scipy）"

print(f"pkg     : {pkg}")
print(f"backend : {backend}")
for rel in samples:
    p = os.path.join(os.path.dirname(os.path.abspath(pkg)), rel)
    if not os.path.exists(p):
        p = rel
    t0 = time.time()
    res = ns["detect_seals"](open(p, "rb").read())
    print(f"  {os.path.basename(p):<42} 印章数量={res['印章数量']:<3} 用时={time.time() - t0:.1f}s")
print("OK: 包内代码可正常执行")
