# Red Seal Detector (红色印章检测) — Dify 插件

用**纯图像处理**统计合同图片/PDF 里的印章数量，不调用大模型、不联网、数据不出本机。
A Dify tool plugin that counts seals/stamps in contract images with pure image processing — no LLM, no network, no data leaves the machine.

- **插件类型**：Tool（工具插件）
- **运行时**：Python 3.12，依赖 `numpy` / `pillow` / `pymupdf` / `scipy`（纯 CPU，无需 GPU）
- **输出**：JSON（印章数量 + 每枚印章的位置）

## 安装（Dify）

插件通过本仓库的 **Release 资产**分发（Dify 从 Release 里取 `.difypkg`）：

1. Dify → 插件 → 安装插件 → **GitHub**
2. 填入仓库地址：`https://github.com/<owner>/red-seal-detector`
3. 选择 Release 版本（如 `v0.0.12`）→ 安装

> 自部署 Dify 默认开启插件签名校验：如果安装时报 `plugin verification has been enabled, and the plugin you want to install has a bad signature`，
> 需要在该实例的 `docker/.env` 中设置 `FORCE_VERIFYING_SIGNATURE=false` 后执行 `docker compose down && docker compose up -d`（`restart` 不生效），
> 或由管理员配置第三方签名公钥（`THIRD_PARTY_SIGNATURE_VERIFICATION_*`）后安装签名版包。

## 使用

工作流中加"工具"节点 → 选择 **红色印章检测** → 输入文件即可。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `files` | files | 是 | 合同图片（jpg/png）或 PDF，可多选 |

### 输出

```json
{
  "印章数量": 3,
  "检测文件数": 2,
  "详情": [
    {
      "filename": "合同.pdf",
      "印章数量": 2,
      "印章位置": [{ "x": 512, "y": 733, "w": 134, "h": 141, "page": 5 }]
    }
  ]
}
```

- 位置坐标是**图片长边缩放到 1000px 之后**的坐标系；PDF 的每枚章带 `page`（从 1 开始）
- 单个文件失败不会中断整体，错误信息在 `"错误"` 字段里

## 判定口径（重要）

统计的是**正文圆形 / 椭圆形公章**：

| 计入 | 不计入 |
|---|---|
| 圆形公章、椭圆形公章（红色、蓝色；黑白扫描页按灰度掩膜检测） | 方形章、方形人名章 |
| 章面有字、环带断续、印油偏淡的真章 | 骑缝章、边角小章（短边 < 95px，约 2.2cm 以下） |
| | 贴边被裁切的残缺章、表格框 / 整段签署区（短边 > 260px，约 7.7cm 以上） |

判定规则（在长边 1000px 归一化空间）：尺寸窗口 95~260px + 外接框四角墨迹占比 ≤ 0.04 + 椭圆归一化径向变异系数 ≤ 0.20（超标触发粘连框拆分）。

## 已知限制

- 纯 CPU 图像处理：80 页合同约 20~35 秒；缺 `scipy` 时连通域标记走 BFS 兜底，黑白扫描页会明显变慢
- 只输出数量和位置，不输出裁剪图/标注图
- 印章与文字严重重叠、印油极淡、被裁掉一半以上的情况可能漏检

## 目录结构

```
manifest.yaml                    # 插件清单（version / author / 入口）
main.py                          # 插件入口
provider/red_seal_detector.py    # Provider（无需凭据）
provider/red_seal_detector.yaml
tools/red_seal_detector.py       # 检测实现（核心）
tools/red_seal_detector.yaml     # 工具定义与输入参数
_assets/icon.svg                 # 图标
requirements.txt                 # 依赖
build_seal_pkg.py                # 本地打包 → dist/red-seal-detector-<version>.difypkg
verify_pkg.py                    # 解包后跑真实样本自检
```

## 本地打包与自检

```bash
python build_seal_pkg.py                                   # 产出 dist/red-seal-detector-<version>.difypkg
python verify_pkg.py dist/red-seal-detector-0.0.12.difypkg <样本.pdf>
```

发布新版本时：改 `manifest.yaml` 的 `version` 和 `meta.version`（两处一起改）→ 打包 → 发 Release，**tag 名与 version 对应**（`v0.0.12` ↔ `0.0.12`），把 `.difypkg` 作为 Release 资产上传。

> ⚠️ `manifest.yaml` 与 `provider/*.yaml` 里的 `author` 必须等于**本仓库所属的 GitHub 用户名**，否则 Dify 安装时报 `plugin_unique_identifier is not valid`。

## 版本

| 版本 | 变更 |
|---|---|
| 0.0.12 | 发布到 GitHub（author 改为仓库所有者）；依赖加 `scipy`（黑白扫描页连通域标记提速） |
| 0.0.11 | 修正 `meta.version` 与顶层版本不一致 |
| 0.0.10 | 只统计圆章：章尺寸窗口 + 四角墨迹过滤 + 粘连框拆分 |
| 0.0.9 | 尺寸下限（滤掉骑缝章/人名章）+ 黑白页四角墨迹过滤 |
| 0.0.8 | 印章位置带页码 |
