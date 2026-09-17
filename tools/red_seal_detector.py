# -*- coding: utf-8 -*-
"""v0.0.10 改造：只数圆章
1) 新增圆度判据（椭圆归一化径向变异系数 cv_ell）+ 章尺寸窗口（短边 95~260px）
2) 粘连框（圆章+方章被膨胀连成一块）用"细连通 2px 膨胀"拆开后再各自判圆
3) 其余过滤规则保持原样
"""
import json
import logging
import os
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.file.file import File
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---- 圆章判据阈值（1000px 归一化空间；A4 页宽≈707px，3.4px/mm）----
SEAL_MIN_SIDE = 95    # 章尺寸窗口下限：人名章/骑缝章/小碎片(2.2cm 以下)不算
SEAL_MAX_SIDE = 260   # 上限：表格块/整段签署区(7.7cm 以上)不算
CORNER_INK_MAX = 0.04  # 外接框四角(各 10% 边长)墨迹占比上限：圆章/椭圆章角部必空；方形章、表格块有墨
ROUND_CV_MAX = 0.20    # 椭圆归一化径向变异系数上限（放宽版）：圆形 0.01~0.15；圆章+方章粘连的合并框 ~0.21 → 触发拆分


class ToolParameters(BaseModel):
    files: list[File]


def _load_file_bytes(f: File) -> bytes:
    """加载文件字节：优先完整 URL 下载；相对 URL 拼 INTERNAL_FILES_URL 前缀"""
    url = getattr(f, "url", None) or getattr(f, "remote_url", "") or ""
    if url and not (url.startswith("http://") or url.startswith("https://")):
        base = os.environ.get("INTERNAL_FILES_URL") or os.environ.get("FILES_URL") or ""
        if base:
            url = base.rstrip("/") + "/" + url.lstrip("/")
    if url.startswith("http://") or url.startswith("https://"):
        import httpx
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    return f.blob


def _label_components(dilated):
    """连通域标记。优先 scipy（C 实现，黑白页深色掩膜像素多时快几个数量级），缺则 BFS 兜底。
    返回 list[ndarray]，每个元素 shape (N,2)，列为 (y, x)。"""
    import numpy as np

    try:
        from scipy import ndimage as ndi

        lab, n = ndi.label(dilated, structure=np.ones((3, 3), dtype=np.uint8))
        comps = []
        if n == 0:
            return comps
        # find_objects 第 i 个切片对应 label i+1；切片可能相互重叠，只能按 label 取一次，
        # 否则同一 label 会被多个切片重复 append（导致重复计数）。
        for i, sl in enumerate(ndi.find_objects(lab), start=1):
            if sl is None:
                continue
            sub = lab[sl]
            yy, xx = np.where(sub == i)
            comps.append(np.stack([yy + sl[0].start, xx + sl[1].start], axis=1))
        return comps
    except ImportError:
        pass

    # BFS 兜底（无 scipy 时）
    H0, W0 = dilated.shape
    visited = np.zeros_like(dilated, dtype=bool)
    comps = []
    for y in range(H0):
        for x in range(W0):
            if dilated[y, x] and not visited[y, x]:
                stack = [(y, x)]
                visited[y, x] = True
                pts = []
                while stack:
                    cy, cx = stack.pop()
                    pts.append((cy, cx))
                    for ny in (cy - 1, cy, cy + 1):
                        for nx in (cx - 1, cx, cx + 1):
                            if 0 <= ny < H0 and 0 <= nx < W0 and dilated[ny, nx] and not visited[ny, nx]:
                                visited[ny, nx] = True
                                stack.append((ny, nx))
                comps.append(np.array(pts, dtype=np.int64))
    return comps


def _erode1(mask) -> "np.ndarray":
    """3x3 全邻域腐蚀一次（去掉 ~2px 细笔画：正文文字、表格细线）"""
    import numpy as np

    H, W = mask.shape
    p = np.pad(mask, 1, mode="edge")
    out = np.ones((H, W), dtype=bool)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out &= p[1 + dx:1 + dx + H, 1 + dy:1 + dy + W] > 0
    return out.astype(np.uint8)


def _longest_run(arr) -> int:
    """布尔数组中最长连续 True 段长度"""
    best = 0
    cur = 0
    for v in arr:
        cur = cur + 1 if v else 0
        if cur > best:
            best = cur
    return best


def _radial_cv(mask, minx, miny, maxx, maxy) -> float:
    """候选框内墨迹的"椭圆归一化半径"在 36 个角度扇区上的变异系数。

    圆章 / 椭圆章（用外接椭圆归一化）→ 0.01~0.07（各方向到边界距离几乎相等）
    方形章、长方形（表格块、红框）→ 0.13~0.20（四个角凸出，径向长度忽大忽小）
    圆章与方章被膨胀粘连的合并框 → ~0.19
    解析不出（墨迹太少）→ 返回 9.9（视为非圆章）
    """
    import numpy as np

    x0, y0 = max(int(minx), 0), max(int(miny), 0)
    x1 = min(int(maxx) + 1, mask.shape[1])
    y1 = min(int(maxy) + 1, mask.shape[0])
    if x1 <= x0 or y1 <= y0:
        return 9.9
    sub = mask[y0:y1, x0:x1]
    ys, xs = np.where(sub > 0)
    if len(ys) < 50:
        return 9.9
    w = x1 - x0
    h = y1 - y0
    a2, b2 = max(w / 2.0, 1e-6), max(h / 2.0, 1e-6)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    d = np.sqrt(((xs - cx) / a2) ** 2 + ((ys - cy) / b2) ** 2)
    ang = np.arctan2(ys - cy, xs - cx)
    idx = ((ang + np.pi) / (2 * np.pi) * 36).astype(int) % 36
    bins = np.zeros(36)
    np.maximum.at(bins, idx, d)
    used = bins[bins > 0]
    if len(used) < 6:
        return 9.9
    return float(used.std() / used.mean())


def _corner_ink(mask, minx, miny, maxx, maxy, p=0.10) -> float:
    """外接框四角（各取边长 p 的小方块）里的墨迹占比。
    圆章/椭圆章的角部必空（≈0）；方形章、表格块的角部有边框/文字 → 明显 >0。"""
    x0, y0 = max(int(minx), 0), max(int(miny), 0)
    x1 = min(int(maxx) + 1, mask.shape[1])
    y1 = min(int(maxy) + 1, mask.shape[0])
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return 1.0
    cw, ch = max(int(w * p), 1), max(int(h * p), 1)
    tot, area = 0, 0
    for (ax, ay, bx, by) in ((0, 0, cw, ch), (w - cw, 0, w, ch),
                             (0, h - ch, cw, h), (w - cw, h - ch, w, h)):
        tot += int(mask[y0 + ay:y0 + by, x0 + ax:x0 + bx].sum())
        area += (bx - ax) * (by - ay)
    return tot / max(area, 1)


def _split_boxes(mask, minx, miny, maxx, maxy, pad=2, iters=2):
    """把粘连成一个连通域的候选拆开：只做 2px 膨胀（既能连章内断弧，
    又不会把相邻两枚章粘在一起），再取连通域。返回子块 bbox 列表 [(x0,y0,x1,y1,area)]"""
    import numpy as np

    x0, y0 = max(int(minx) - pad, 0), max(int(miny) - pad, 0)
    x1 = min(int(maxx) + pad + 1, mask.shape[1])
    y1 = min(int(maxy) + pad + 1, mask.shape[0])
    if x1 <= x0 or y1 <= y0:
        return []
    d = mask[y0:y1, x0:x1].astype(bool)
    for _ in range(iters):
        p = np.pad(d, 1, mode="constant")
        nd = np.zeros_like(d)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nd |= p[1 + dx:1 + dx + d.shape[0], 1 + dy:1 + dy + d.shape[1]]
        d = nd
    out = []
    for pts in _label_components(d.astype(np.uint8)):
        if len(pts) < 300:
            continue
        ys, xs = pts[:, 0], pts[:, 1]
        out.append((x0 + int(xs.min()), y0 + int(ys.min()),
                    x0 + int(xs.max()), y0 + int(ys.max()), len(pts)))
    return out


def _check_box(mask, minx, miny, maxx, maxy, W0, H0, total_area, grayscale_mode, area):
    """一个候选框是否算"正文圆章"。返回 (是否通过, 拦截原因列表)。
    规则与 v5.4 一致，另加：章尺寸窗口 + 圆度判据（只数圆章）。"""
    import numpy as np

    reasons = []
    w1 = maxx - minx + 1
    h1 = maxy - miny + 1
    if w1 <= 0 or h1 <= 0:
        return False, ["空框"]
    if area < max(total_area * 0.001, 300):
        return False, ["面积小"]
    aspect = w1 / h1
    if not (0.5 < aspect < 2.2):
        reasons.append("aspect")
    margin = 4
    if minx <= margin or miny <= margin or maxx >= W0 - 1 - margin or maxy >= H0 - 1 - margin:
        reasons.append("贴边")
    fill = area / (w1 * h1)
    if not (0.20 <= fill <= 0.75):
        reasons.append("fill")
    # 只数圆章：章尺寸窗口 + 四角无墨 + 径向不畸形
    if not (SEAL_MIN_SIDE <= min(w1, h1) <= SEAL_MAX_SIDE):
        reasons.append("尺寸窗口")
    cink = _corner_ink(mask, minx, miny, maxx, maxy)
    if cink > CORNER_INK_MAX:
        reasons.append("角墨>%.2f(%.3f)" % (CORNER_INK_MAX, cink))
    cv = _radial_cv(mask, minx, miny, maxx, maxy)
    if cv > ROUND_CV_MAX:
        reasons.append("非圆(cv=%.3f)" % cv)

    x0i, y0i, x1i, y1i = int(minx), int(miny), int(maxx), int(maxy)
    sub = mask[y0i:y1i + 1, x0i:x1i + 1]
    ys, xs = np.where(sub > 0)
    if len(ys) == 0:
        return False, reasons + ["无墨迹"]
    ys = ys + y0i
    xs = xs + x0i
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    R = max(w1, h1) / 2.0 + 1
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    center_ratio = float((d < 0.5 * R).mean())

    if grayscale_mode:
        # 黑白页判章：深色掩膜含全部正文，靠"环形空心结构"区分章 vs 正文块。
        ring_band = float((d > 0.6 * R).sum()) / max(area, 1)
        if ring_band < 0.5:
            reasons.append("ring<0.5")
        core_ratio = float((d < 0.4 * R).sum()) / max(area, 1)
        if core_ratio > 0.25:
            reasons.append("core>0.25")
        corner_w = max(int(w1 * 0.20), 1)
        corner_h = max(int(h1 * 0.20), 1)
        corner_fill = 0
        corner_area = 0
        for (x1, y1_, x2, y2) in [
            (minx, miny, minx + corner_w, miny + corner_h),
            (maxx - corner_w, miny, maxx, miny + corner_h),
            (minx, maxy - corner_h, minx + corner_w, maxy),
            (maxx - corner_w, maxy - corner_h, maxx, maxy),
        ]:
            m = (xs >= x1) & (xs < x2) & (ys >= y1_) & (ys < y2)
            corner_fill += int(m.sum())
            corner_area += (x2 - x1) * (y2 - y1_)
        if corner_fill / max(corner_area, 1) > 0.20:
            reasons.append("角墨>0.2")
    else:
        a2 = w1 / 2.0
        b2 = h1 / 2.0
        d_ell = np.sqrt(((xs - cx) / a2) ** 2 + ((ys - cy) / b2) ** 2)
        if float((d_ell > 1.3).mean()) > 0.12:
            reasons.append("椭圆外>0.12")
        sub_m = mask[int(miny):int(maxy) + 1, int(minx):int(maxx) + 1]
        max_col_run = max(_longest_run(sub_m[:, c]) for c in range(sub_m.shape[1]))
        max_row_run = max(_longest_run(sub_m[r, :]) for r in range(sub_m.shape[0]))
        if max_col_run >= 0.45 * h1 or max_row_run >= 0.45 * w1:
            reasons.append("长直线")
        if aspect < 1.5:
            if center_ratio > 0.25 and area < 2500:
                reasons.append("core>0.25&area<2500")
        else:
            if area < 2500:
                reasons.append("长条area<2500")
    return (len(reasons) == 0), reasons


def _detect_seals_from_image(img) -> dict:
    """对单张 PIL 图片做印章检测（v6：红/蓝章 + 黑白扫描页黑章；只数正文圆章）"""
    import numpy as np

    maxside = 1000
    w, h = img.size
    if max(w, h) > maxside:
        scale = maxside / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    a = np.asarray(img).astype(np.float32) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    diff = mx - mn
    eps = 1e-6
    sat = np.where(mx > eps, diff / np.maximum(mx, eps), 0)
    val = mx
    mask_r = (mx == r) & (diff > eps)
    mask_g = (mx == g) & (diff > eps)
    mask_b = (mx == b) & (diff > eps)
    hue = np.where(mask_r, 60 * (((g - b) / np.maximum(diff, eps)) % 6), 0.0)
    hue = np.where(mask_g, 60 * ((b - r) / np.maximum(diff, eps) + 2), hue)
    hue = np.where(mask_b, 60 * ((r - g) / np.maximum(diff, eps) + 4), hue)
    hue = hue / 360.0 * 180.0

    # 模式判定：红/蓝掩膜里有像样的色块 → 彩色模式；否则视为黑白扫描页 → 深色掩膜找黑章。
    m1 = (hue <= 10) & (sat > 0.30) & (val > 0.30)
    m2 = (hue >= 155) & (sat > 0.30) & (val > 0.30)
    mb = (hue >= 95) & (hue <= 140) & (sat > 0.30) & (val > 0.25)
    mask_color = (m1 | m2 | mb).astype(np.uint8)
    H0, W0 = mask_color.shape
    total_area = H0 * W0

    if int(mask_color.sum()) >= max(total_area * 0.0005, 200):
        grayscale_mode = False
        mask = mask_color
        dil_r = 3
    else:
        grayscale_mode = True
        mask = (val < 0.55).astype(np.uint8)
        dil_r = 1

    pad = np.pad(mask, dil_r, mode="constant")
    dilated = np.zeros_like(mask, dtype=bool)
    H0, W0 = mask.shape
    for dx in range(-dil_r, dil_r + 1):
        for dy in range(-dil_r, dil_r + 1):
            dilated |= pad[dil_r + dx:dil_r + dx + H0, dil_r + dy:dil_r + dy + W0] > 0

    comps = _label_components(dilated)

    seals = []
    for p in comps:
        area = len(p)
        if area < max(total_area * 0.001, 300):
            continue
        p = p.astype(np.float64)
        ys, xs = p[:, 0], p[:, 1]
        minx, maxx = int(xs.min()), int(xs.max())
        miny, maxy = int(ys.min()), int(ys.max())
        ok, _reasons = _check_box(mask, minx, miny, maxx, maxy, W0, H0,
                                  total_area, grayscale_mode, area)
        if ok:
            seals.append({"x": minx, "y": miny, "w": maxx - minx + 1, "h": maxy - miny + 1})
            continue
        # 未通过：可能是圆章与方章/正文粘连成一块 → 拆开后再各自判定（只留圆章）
        for (bx0, by0, bx1, by1, barea) in _split_boxes(mask, minx, miny, maxx, maxy):
            ok2, _r2 = _check_box(mask, bx0, by0, bx1, by1, W0, H0,
                                  total_area, grayscale_mode, barea)
            if ok2:
                seals.append({"x": bx0, "y": by0, "w": bx1 - bx0 + 1, "h": by1 - by0 + 1})

    # 同章碎片合并：两框中心距 < 0.35×较大框半径 视为同一章（取外接框）
    if len(seals) > 1:
        merged = []
        for s in sorted(seals, key=lambda z: -(z["w"] * z["h"])):
            cx_s = s["x"] + s["w"] / 2.0
            cy_s = s["y"] + s["h"] / 2.0
            hit = False
            for m in merged:
                cx_m = m["x"] + m["w"] / 2.0
                cy_m = m["y"] + m["h"] / 2.0
                dist = float(np.hypot(cx_s - cx_m, cy_s - cy_m))
                if dist < 0.35 * max(max(s["w"], s["h"]), max(m["w"], m["h"])) / 2.0:
                    x1 = min(m["x"], s["x"])
                    y1 = min(m["y"], s["y"])
                    x2 = max(m["x"] + m["w"], s["x"] + s["w"])
                    y2 = max(m["y"] + m["h"], s["y"] + s["h"])
                    m.update(x=x1, y=y1, w=x2 - x1, h=y2 - y1)
                    hit = True
                    break
            if not hit:
                merged.append(dict(s))
        seals = merged
    return {"印章数量": len(seals), "印章位置": seals}


def detect_seals(img_bytes: bytes) -> dict:
    """印章检测 v6：支持图片与 PDF（PDF 逐页转图检测）；只统计正文圆章（红/蓝/黑白扫描页黑章）"""
    import io
    from PIL import Image

    if img_bytes[:5] == b"%PDF-":
        import fitz  # PyMuPDF
        doc = fitz.open(stream=img_bytes, filetype="pdf")
        total = 0
        positions = []
        for page_no, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            res = _detect_seals_from_image(img)
            total += res["印章数量"]
            positions.extend({**p, "page": page_no} for p in res["印章位置"])
        doc.close()
        return {"印章数量": total, "印章位置": positions}

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return _detect_seals_from_image(img)


class RedSealDetectorTool(Tool):
    """检测合同图片/PDF中的印章数量（红/蓝/黑，纯图像处理，无需大模型）"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        if tool_parameters.get("files") is None:
            yield self.create_text_message("未提供文件。请上传合同图片或PDF。")
            return

        params = ToolParameters(**tool_parameters)
        files = params.files
        total = 0
        details = []
        errors = []
        for f in files:
            try:
                data = _load_file_bytes(f)
                result = detect_seals(data)
                total += result["印章数量"]
                details.append({"filename": f.filename, **result})
            except Exception as e:  # noqa: BLE001
                logger.exception("seal detect failed")
                errors.append({"filename": getattr(f, "filename", "?"), "error": str(e)})

        payload = {"印章数量": total, "检测文件数": len(files), "详情": details}
        if errors:
            payload["错误"] = errors
        yield self.create_json_message(payload)
