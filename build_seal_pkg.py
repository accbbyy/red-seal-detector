# -*- coding: utf-8 -*-
"""打包 red-seal-detector 插件为 .difypkg（版本号取自 manifest.yaml）

用法：python build_seal_pkg.py
产出：./dist/red-seal-detector-<version>.difypkg
只打包插件运行必需文件（不含 __pycache__、.bak、开发调试脚本）。
"""
import hashlib
import os
import zipfile

SRC = os.path.dirname(os.path.abspath(__file__))  # 仓库根目录 = 插件目录
INCLUDE = [
    "manifest.yaml",
    "main.py",
    "PRIVACY.md",
    "requirements.txt",
    "provider/red_seal_detector.py",
    "provider/red_seal_detector.yaml",
    "tools/red_seal_detector.py",
    "tools/red_seal_detector.yaml",
    "_assets/icon.svg",
]

ver = author = None
with open(os.path.join(SRC, "manifest.yaml"), encoding="utf-8") as fh:
    for line in fh:
        if line.startswith("version:"):
            ver = line.split(":", 1)[1].strip()
        elif line.startswith("author:"):
            author = line.split(":", 1)[1].strip()
assert ver, "manifest.yaml 里找不到 version"

dist = os.path.join(SRC, "dist")
os.makedirs(dist, exist_ok=True)
out = os.path.join(dist, f"red-seal-detector-{ver}.difypkg")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for rel in INCLUDE:
        z.write(os.path.join(SRC, rel), rel)

data = open(out, "rb").read()
with zipfile.ZipFile(out) as z:
    bad = z.testzip()
    names = z.namelist()
src_py = hashlib.sha256(open(os.path.join(SRC, "tools/red_seal_detector.py"), "rb").read()).hexdigest()[:16]
with zipfile.ZipFile(out) as z:
    pkg_py = hashlib.sha256(z.read("tools/red_seal_detector.py")).hexdigest()[:16]

print(f"version   : {ver}")
print(f"author    : {author}   ← 必须等于 GitHub 用户名")
print(f"out       : {out}")
print(f"size      : {len(data)} bytes")
print(f"sha256    : {hashlib.sha256(data).hexdigest()}")
print(f"zip_bad   : {bad}")
print(f"files     : {names}")
print(f"tools.py  : src={src_py} pkg={pkg_py} match={src_py == pkg_py}")
