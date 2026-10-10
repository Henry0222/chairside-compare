# 0.7.4 验证记录

验证日期：2026-10-10。配准核心继续使用 3.0.0，未修改数值配准算法。

- PR 提交副本的当前 16 项核心测试全部通过；合成模型剖面交互回归同样通过。
- 多模型显示、模型对切换、拖线剖面与保存恢复已进行交互回归。
- 0.7.4 真实病例副本回归验证控件移除、三个剖面按钮顺序、旧病例统一使用 0.05 mm 步长、平移/拖边缩放/绕自身法向旋转、固定位置透明度面板与保存恢复。患者数据不随仓库发布。
- 法向旋转回归包含正面、斜面及背面观察下的 30 度拖动；法向保持不变，矩形中心及边长保持不变。原生窗口截图检查已完成。
- Windows x64 免安装 EXE 在清空 PYTHONPATH/PYTHONHOME、仅保留 Windows 系统 PATH 后自检 ok/render/measurement 均为 true。合成球面配准返回 warning，未将其解释为临床精度证明。
- 0.7.4 EXE SHA-256：`2a4c00de908f30198affccefa5eeaa1d5494435b73da0ff8b3da61fe61c2fbdb`。
- EXE 与依赖目录为本地构建产物，本 PR 不包含二进制发行包；尚未在另一台物理电脑验证。

可复现命令（仓库根目录）：

```powershell
.venv\Scripts\python.exe -m pytest chairside_compare/tests -q
.venv\Scripts\python.exe chairside_compare/scripts/smoke_multimodel.py
.venv\Scripts\python.exe chairside_compare/scripts/smoke_plane_editor.py
```

以上 UI 脚本无参数时使用合成模型，需要桌面及 OpenGL 环境。
