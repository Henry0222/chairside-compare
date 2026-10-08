# Chairside Compare · 椅旁预备对比

面向单牙固定修复预备复核：将当前口扫与外部设计的目标预备模型配准，在三维叠加与截面视图中观察表面差异。

**版本：0.6.0 · Windows x64 · Python 3.12 · 配准核心 3.0.0**

## 快速开始：免安装版

从 [Releases](https://github.com/Henry0222/chairside-compare/releases) 下载 Windows x64 Portable ZIP，完整解压后双击 `ChairsideCompare.exe`。

- 无需单独安装 Python，不需要在线下载运行依赖。
- 必须保留同目录的 `_internal` 文件夹，不能只复制 EXE。
- 面向 Windows 10/11 64 位；目标电脑的显卡驱动与三维显示兼容性仍需现场验证。
- 安装包已取消，本项目提供免安装文件夹。

## 主要功能

- **双模型 / 三模型工作流**：目标与当前必填，初诊可选。双模型以目标为参考；三模型将目标和当前分别对齐初诊。
- **拖入即预览**：支持 STL / PLY，可从资源管理器拖到导入框或三维视图；未配准时显示原始位置。
- **全牙列彩虹图**：显示在目标模型上，当前模型为浅绿色；颜色上下限、容许范围、各模型透明度均可调。
- **三维与截面联动**：平行投影，左下角截面小窗可双击与主视图交换；截面平面为橙色，不透明度可调。
- **局部偏差测量**：默认半径 0.1 mm，只显示面积加权平均偏差，文字不被模型遮挡。
- **截面测量**：距离测量及有向角度测量；支持重复点选修正截面中心。
- **病例保存**：记录配准矩阵、诊断、显示参数、测量与视角；每轮扫描独立保存。
- **配准诊断**：可读摘要展示状态、重叠率、表面残差、耗时和方案选择原因，亦可切换原始诊断。

界面参考 [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/) 的布局与视觉层级，采用 Windows 字体与浅色控件。

## 使用流程

1. 在其他软件中生成目标预备体模型。推荐包含邻牙的全牙列数据，单位为毫米。
2. 导入目标模型和当前扫描；初诊模型按需添加。直接逐个拖入时，先目标、后当前；批量拖入后请核对模型角色。
3. 点击“开始配准”，查看结果与诊断，复核共同牙面的对齐情况。
4. 调整模型透明度和彩虹色标；点击模型定位截面中心，可连续重选，再次点击定位按钮退出。
5. 使用三维平均偏差、截面距离或角度测量复核形态；保存视图，或导入下一轮扫描。

## 鼠标操作

| 操作 | 三维视图 | 截面视图 |
| --- | --- | --- |
| 左键拖动 | 旋转模型 | 旋转截面方向 |
| 中键拖动 | 平移 | 平移 |
| 右键拖动 | 缩放 | 缩放 |
| 滚轮 | 缩放 | 移动截面 |
| Ctrl + 滚轮 | 缩放 | 缩放 |
| 双击小窗 | 交换大小视图 | 交换大小视图 |

滚轮经过侧栏参数框时滚动侧栏，不修改参数。截面默认范围为 10 mm；旧病例恢复其保存值。

角度测量中，点 1→2 定义参考方向，点 3→4 定义待测方向。按截面画面从参考转向待测，逆时针为正、顺时针为负，范围为 −180° 至 +180°。取点方向应保持一致；正负表示转向，不自动判定倒凹。

## 从源码运行

仓库包含椅旁应用及配准核心。建议使用 Windows x64 和 Python 3.12。

在仓库根目录运行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ./general_model_registration -e ./chairside_compare
.venv\Scripts\python.exe chairside_compare/run.py
```

完成依赖安装后，也可双击根目录 `run_app.bat`。

主要依赖：PySide6、VTK、Open3D、NumPy、SciPy。具体版本约束见两个子项目的 `pyproject.toml`。


## 数据与目录

```text
chairside_compare/              椅旁应用、测试、打包脚本
  src/chairside_compare/        界面、几何计算、存储与诊断
  scripts/                     回归与查看器辅助脚本
  packaging/                   PyInstaller 配置与第三方许可
  tests/                       核心测试
general_model_registration/    配准核心 3.0.0
run_app.bat                     源码启动入口
```

- 免安装版：病例和设置存入 `%LOCALAPPDATA%\ChairsideCompare`。
- 源码版：数据存入 `chairside_compare/cases`，默认显示设置存入 `.user/display.json`。
- 一个病例包含 `case.json` 和 `geometry.npz`；备份或迁移时应复制完整病例文件夹。
- 仓库不包含患者模型、病例、真实病例截图、密钥、虚拟环境或构建缓存。二进制包放在 Releases，不放进 Git 历史。

## 当前范围

本版本不自动生成理想预备体，也不提供语义牙齿分割或自动牙位编号。自动变化区只用于定位提示。

彩虹图将当前表面的有符号偏差插值映射到目标表面，不能直接解释为车针进给深度。无效对应显示灰色；配准失败时禁用偏差显示。表面匹配残差与真实位置误差不同。

## 许可

附带的配准核心保留其原有 [BSD-3-Clause 许可](general_model_registration/LICENSE)。第三方依赖许可见 `chairside_compare/packaging/third_party`。
