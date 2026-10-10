from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json
import os
import sys
import traceback

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QObject, QEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSplitter, QScrollArea, QDoubleSpinBox, QComboBox, QCheckBox,
    QSlider, QFileDialog, QMessageBox, QDialog, QLineEdit, QProgressBar, QFormLayout, QAbstractSpinBox, QListWidget, QPlainTextEdit, QButtonGroup)

from .core import DisplaySettings, run_case, load_case, atomic_json, load_preview, normalize_paths, pair_arrays, snapshot_paths
from .viewer import CompareViewer

ROOT = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ChairsideCompare'
        if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2])
PREFS = ROOT / '.user' / 'display.json'

STYLE = '''
QMainWindow,QDialog {background:#f5f5f7;color:#1d1d1f;}
QWidget {font-family:"Segoe UI","Microsoft YaHei UI";font-size:13px;color:#1d1d1f;}
QWidget#controls {background:#ffffff;border-radius:16px;}
QLabel#heading {font-size:25px;font-weight:600;color:#1d1d1f;}
QLabel#muted {color:#6e6e73;font-size:12px;}
QLabel#section {color:#1d1d1f;font-size:15px;font-weight:600;padding-top:18px;padding-bottom:8px;border-bottom:1px solid #e8e8ed;}
QPushButton {background:#ffffff;border:1px solid #d2d2d7;border-radius:9px;padding:9px 14px;min-height:18px;}
QPushButton:hover {background:#f0f5fc;border-color:#0071e3;}
QPushButton:pressed {background:#e4edf9;}
QPushButton:focus {border:1px solid #0071e3;}
QPushButton:disabled {color:#a1a1a6;background:#f5f5f7;border-color:#e8e8ed;}
QPushButton#primary {background:#0071e3;border:1px solid #0071e3;color:white;font-weight:600;}
QPushButton#primary:hover {background:#0077ed;}
QPushButton:checked {border-color:#0071e3;background:#eaf3ff;color:#0066cc;}
QLineEdit,QDoubleSpinBox,QComboBox,QListWidget {background:#ffffff;color:#1d1d1f;border:1px solid #d2d2d7;border-radius:7px;padding:6px;selection-background-color:#0071e3;selection-color:white;}
QLineEdit:focus,QDoubleSpinBox:focus,QComboBox:focus {border-color:#0071e3;}
QPlainTextEdit {background:#ffffff;color:#1d1d1f;border:1px solid #d2d2d7;selection-background-color:#0071e3;selection-color:white;padding:12px;}
QComboBox QAbstractItemView {background:#ffffff;color:#1d1d1f;selection-background-color:#eaf3ff;selection-color:#0066cc;}
QScrollArea {border:none;background:transparent;}
QScrollBar:vertical {background:transparent;width:8px;margin:4px 0;}
QScrollBar::handle:vertical {background:#c7c7cc;border-radius:4px;min-height:36px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0px;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QSlider::groove:horizontal {height:4px;background:#e5e5ea;border-radius:2px;}
QSlider::sub-page:horizontal {background:#0071e3;border-radius:2px;}
QSlider::handle:horizontal {background:#ffffff;border:1px solid #b5b5bb;width:16px;margin:-7px 0;border-radius:8px;}
QProgressBar {border:none;background:#e5e5ea;border-radius:2px;height:4px;}
QProgressBar::chunk {background:#0071e3;border-radius:2px;}
QToolTip {background:#ffffff;color:#1d1d1f;border:1px solid #d2d2d7;padding:5px;}
'''


class ParameterWheelBlocker(QObject):
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Wheel:
            control = watched
            while control is not None:
                if isinstance(control,(QAbstractSpinBox,QSlider,QComboBox)):
                    parent = control.parentWidget()
                    while parent is not None and not isinstance(parent,QScrollArea):
                        parent = parent.parentWidget()
                    if parent is not None:
                        viewport = parent.viewport()
                        forwarded = QWheelEvent(viewport.mapFromGlobal(event.globalPosition().toPoint()),
                            event.globalPosition(),event.pixelDelta(),event.angleDelta(),event.buttons(),
                            event.modifiers(),event.phase(),event.inverted())
                        QApplication.sendEvent(viewport,forwarded)
                    return True
                control = control.parent()
        return False


class ModelPathEdit(QLineEdit):
    def __init__(self,text='',parent=None):
        super().__init__(text,parent)
        self.setAcceptDrops(True)

    @staticmethod
    def files(mime):
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile() and
                Path(url.toLocalFile()).suffix.lower() in ('.stl','.ply') and Path(url.toLocalFile()).is_file()]

    def dragEnterEvent(self,event):
        if self.files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        files = self.files(event.mimeData())
        if files:
            self.setText(files[0])
            event.acceptProposedAction()

    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)


class PreviewJob(QThread):
    progress = Signal(float,str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self,paths,parent=None):
        super().__init__(parent)
        self.paths = dict(paths)

    def run(self):
        try:
            self.progress.emit(0,'保存本轮导入副本…')
            self.paths = snapshot_paths(self.paths,ROOT/'imports')
            arrays = load_preview(self.paths,self.progress.emit)
            if not self.isInterruptionRequested():
                self.completed.emit((self.paths,arrays))
        except Exception as error:
            self.error.emit(str(error))


class Job(QThread):
    progress = Signal(float,str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, paths, previous, parent=None, reference=None, comparison=None):
        super().__init__(parent)
        self.paths, self.previous = paths, previous
        self.reference, self.comparison = reference, comparison

    def run(self):
        try:
            result = run_case(self.paths, ROOT/'cases', self.progress.emit, self.isInterruptionRequested,
                              self.previous, self.reference, self.comparison)
            self.completed.emit(result)
        except Exception as error:
            self.error.emit(f'{type(error).__name__}: {error}')
            traceback.print_exc()


class PairJob(QThread):
    completed = Signal(object)
    error = Signal(str)

    def __init__(self,arrays,pair,failed,parent=None):
        super().__init__(parent)
        self.arrays,self.pair,self.failed = arrays,pair,failed

    def run(self):
        try:
            data = pair_arrays(self.arrays,*self.pair,calculate=True,failed=self.failed)
            if self.isInterruptionRequested():
                self.error.emit('已取消切换')
            else:
                self.completed.emit((self.pair,data))
        except Exception as error:
            self.error.emit(str(error))


class InputDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle('模型列表 · 按导入顺序排列')
        self.setAcceptDrops(True)
        self.resize(800, 480)
        layout = QVBoxLayout(self)
        title = QLabel('建立同一病例的比较流程')
        title.setObjectName('heading')
        layout.addWidget(title)
        note = QLabel('可连续追加多个 STL / PLY，也可从文件夹拖入。单位：毫米。\n导入后先预览，再选择参考与比较模型；配准时所有模型独立对齐参考。')
        note.setObjectName('muted')
        layout.addWidget(note)
        self.edits = {}
        self.rows = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.rows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        layout.addWidget(scroll,1)
        for key,path in paths.items():
            self.add_row(path,key)
        add = QPushButton('添加模型…')
        add.clicked.connect(self.add_files)
        layout.addWidget(add)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        accept = QPushButton('使用这些模型')
        accept.setObjectName('primary')
        accept.clicked.connect(self.validate)
        buttons.addWidget(accept)
        layout.addLayout(buttons)

    def add_row(self,path,key=None):
        key = key or new_model_key(self.edits)
        holder = QWidget()
        row = QHBoxLayout(holder)
        edit = ModelPathEdit(path)
        edit.setPlaceholderText('拖入 STL / PLY 文件…')
        row.addWidget(edit,1)
        browse = QPushButton('浏览')
        browse.clicked.connect(lambda:self.browse(edit))
        row.addWidget(browse)
        remove = QPushButton('移除')
        remove.clicked.connect(lambda:self.remove_row(key,holder))
        row.addWidget(remove)
        self.rows.addWidget(holder)
        self.edits[key] = edit

    def remove_row(self,key,holder):
        self.edits.pop(key,None)
        holder.deleteLater()

    def add_files(self):
        files,_ = QFileDialog.getOpenFileNames(self,'添加模型','','模型 (*.stl *.ply)')
        for path in files:
            self.add_row(path)

    def browse(self, edit):
        path,_ = QFileDialog.getOpenFileName(self,'选择全牙列模型',edit.text(),'模型 (*.stl *.ply)')
        if path:
            edit.setText(path)

    def validate(self):
        paths = self.paths()
        if not paths or any(not Path(p).is_file() or Path(p).suffix.lower() not in ('.stl','.ply') for p in paths.values()):
            QMessageBox.warning(self,'模型不完整','请添加至少一个存在的 STL / PLY 文件；配准需要两个模型。')
            return
        self.accept()

    def paths(self):
        return {k:e.text().strip() for k,e in self.edits.items() if e.text().strip()}

    def dragEnterEvent(self,event):
        if ModelPathEdit.files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        for path in ModelPathEdit.files(event.mimeData()):
            self.add_row(path)
        event.acceptProposedAction()

    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)


def new_model_key(paths):
    if not paths:
        return 'target'
    if 'current' not in paths and 'target' in paths:
        return 'current'
    index = 1
    while f'model_{index}' in paths:
        index += 1
    return f'model_{index}'


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Chairside Compare · 多模型对比 0.7.4')
        self.wheel_blocker = ParameterWheelBlocker(self)
        QApplication.instance().installEventFilter(self.wheel_blocker)
        self.resize(1540,960)
        self.setMinimumSize(1080,740)
        self.settings = DisplaySettings()
        if PREFS.exists():
            try:
                self.settings = DisplaySettings.from_dict(json.loads(PREFS.read_text(encoding='utf-8')))
            except (ValueError,TypeError,OSError):
                pass
        self.paths, self.state, self.case_path, self.job = {}, None, None, None
        self.registration_cache = None
        self.pair_cache = {}
        self.loading = False
        self.watch_folder, self.watch_seen = None, {}
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24,20,24,16)
        outer.setSpacing(12)
        header = QHBoxLayout()
        branding = QVBoxLayout()
        name = QLabel('Chairside Compare')
        name.setObjectName('heading')
        branding.addWidget(name)
        sub = QLabel('椅旁预备对比  ·  扫描、对齐与精细复核')
        sub.setObjectName('muted')
        branding.addWidget(sub)
        header.addLayout(branding)
        header.addStretch()
        self.open_button = self.button('打开病例',self.open_case)
        self.import_button = self.button('导入模型',self.import_models)
        self.save_button = self.button('保存视图',self.save_case)
        for button in [self.open_button,self.import_button,self.save_button]:
            header.addWidget(button)
        outer.addLayout(header)
        self.banner = QLabel('准备就绪  ·  导入两个或多个模型，选择参考与比较模型')
        self.banner.setStyleSheet('background:#e4f2f0;color:#246b65;padding:10px;border-radius:6px;')
        outer.addWidget(self.banner)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.viewer = CompareViewer()
        self.viewer.region_changed.connect(self.region_changed)
        self.viewer.message.connect(lambda text:self.status.setText(text))
        self.viewer.records_changed.connect(self.refresh_records)
        self.viewer.files_dropped.connect(self.drop_models)
        splitter.addWidget(self.viewer)
        self.panel = QWidget()
        self.panel.setObjectName('controls')
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(20,8,20,20)
        panel_layout.setSpacing(8)
        self.section_label(panel_layout,'扫描与定位')
        self.file_summary = QLabel('尚未导入模型')
        self.file_summary.setWordWrap(True)
        self.file_summary.setObjectName('muted')
        panel_layout.addWidget(self.file_summary)
        self.run_button = self.button('开始配准', self.start)
        self.run_button.setObjectName('primary')
        panel_layout.addWidget(self.run_button)
        self.cancel_button = self.button('取消计算',self.cancel)
        self.cancel_button.setVisible(False)
        panel_layout.addWidget(self.cancel_button)
        self.next_button = self.button('导入下一轮扫描',self.next_scan)
        panel_layout.addWidget(self.next_button)
        self.watch_button = self.button('监测导出文件夹',self.watch)
        panel_layout.addWidget(self.watch_button)
        self.pick_button = self.button('点击模型定位牙位', self.pick_mode)
        self.pick_button.setCheckable(True)
        panel_layout.addWidget(self.pick_button)
        self.probe_button = self.button('点选测量模型偏差',self.probe_mode)
        self.probe_button.setCheckable(True)
        panel_layout.addWidget(self.probe_button)
        self.probe_radius = self.spin(.05,3,.05,.1)
        self.probe_radius.valueChanged.connect(lambda value:setattr(self.viewer,'probe_radius',value))
        probe_form = QFormLayout()
        probe_form.addRow('平均半径 mm',self.probe_radius)
        panel_layout.addLayout(probe_form)
        self.clear_probe_button = self.button('清除偏差测量',lambda:self.viewer.clear_measurements('probes'))
        panel_layout.addWidget(self.clear_probe_button)
        self.draw_button = self.button('拖线创建剖面',self.draw_section_mode)
        self.draw_button.setCheckable(True)
        self.draw_button.setToolTip('左键拖出直线，沿当前观察方向构建剖面；Esc 取消。')
        panel_layout.addWidget(self.draw_button)
        panel_layout.addWidget(self.button('按当前 3D 视角设截面',self.viewer.section_from_camera))
        self.clear_section_button = self.button('清除当前剖面',self.clear_custom_section)
        self.clear_section_button.setEnabled(False)
        panel_layout.addWidget(self.clear_section_button)
        self.viewer.drawing_changed.connect(self.draw_button.setChecked)
        self.viewer.custom_section_changed.connect(self.clear_section_button.setEnabled)
        self.section_label(panel_layout,'模型与偏差')
        self.opacity = {}
        self.reference_combo,self.comparison_combo = QComboBox(),QComboBox()
        pair_form = QFormLayout()
        pair_form.addRow('参考模型',self.reference_combo)
        pair_form.addRow('比较模型',self.comparison_combo)
        panel_layout.addLayout(pair_form)
        self.reference_combo.currentIndexChanged.connect(self.pair_changed)
        self.comparison_combo.currentIndexChanged.connect(self.pair_changed)
        hint = QLabel('彩虹图显示在参考模型上。\n各模型不透明度在 3D 视图右上角调整。')
        hint.setObjectName('muted')
        panel_layout.addWidget(hint)
        self.color = QCheckBox('显示全牙列彩虹图')
        self.color.setChecked(True)
        self.color.toggled.connect(self.settings_changed)
        panel_layout.addWidget(self.color)
        self.reverse = QCheckBox('反转偏差方向（法向校正）')
        self.reverse.toggled.connect(self.settings_changed)
        panel_layout.addWidget(self.reverse)
        form = QFormLayout()
        self.lower = self.spin(-5,-.01,.1,self.settings.lower)
        self.upper = self.spin(.01,5,.1,self.settings.upper)
        self.tolerance = self.spin(.01,1,.01,self.settings.tolerance)
        form.addRow('冷色下限 mm',self.lower)
        form.addRow('暖色上限 mm',self.upper)
        form.addRow('容许范围 ±mm',self.tolerance)
        panel_layout.addLayout(form)
        self.coverage = QLabel('灰色：不可评价区域')
        self.coverage.setWordWrap(True)
        self.coverage.setObjectName('muted')
        panel_layout.addWidget(self.coverage)
        self.section_label(panel_layout,'截面对比')
        line = QHBoxLayout()
        line.addWidget(QLabel('截面不透明度'))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0,100)
        slider.setValue(round(self.settings.section_opacity*100))
        slider.setToolTip('三维橙色截面：0% 隐藏，100% 不透明')
        slider.valueChanged.connect(self.settings_changed)
        line.addWidget(slider)
        panel_layout.addLayout(line)
        self.opacity['section'] = slider
        instruction = QLabel('3D 剖面：Alt＋左拖移动；拖边缩放，拖角绕法向旋转\n截面窗：左拖旋转，中拖平移\n滚轮移截面 0.05 mm，Ctrl+滚轮缩放；双击交换')
        instruction.setWordWrap(True)
        instruction.setObjectName('muted')
        panel_layout.addWidget(instruction)
        self.measure_buttons = {}
        self.measure_group = QButtonGroup(self)
        measure_row = QHBoxLayout()
        measure_row.setSpacing(3)
        for title,mode in [('截面浏览','none'),('距离测量','distance'),('角度测量','angle')]:
            button = QPushButton(title)
            button.setCheckable(True)
            button.setStyleSheet('QPushButton {padding:7px 3px;font-size:12px;}')
            button.clicked.connect(lambda checked=False,m=mode:self.viewer.set_measure_mode(m))
            self.measure_group.addButton(button)
            self.measure_buttons[mode] = button
            measure_row.addWidget(button,1)
        self.measure_buttons['none'].setChecked(True)
        panel_layout.addLayout(measure_row)
        measurement_hint = QLabel('点击截线吸附取点。\n角度：1→2 为参考，3→4 为待测方向。\n两线均按颈部→咬合面顺序取点。\n从参考转到待测：逆时针为正，顺时针为负。\n正负表示转向，不自动判定倒凹。\n改变截面清除未完成取点。')
        measurement_hint.setWordWrap(True)
        measurement_hint.setObjectName('muted')
        panel_layout.addWidget(measurement_hint)
        self.records = QListWidget()
        self.records.setMinimumHeight(110)
        self.records.setMaximumHeight(160)
        self.records.itemDoubleClicked.connect(self.restore_record)
        panel_layout.addWidget(self.records)
        panel_layout.addWidget(self.button('清除截面测量',lambda:self.viewer.clear_measurements('section')))
        panel_layout.addWidget(self.button('查看配准诊断',self.diagnostics))
        panel_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        scroll.setMinimumWidth(310)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0,85)
        splitter.setStretchFactor(1,15)
        splitter.setSizes([1250,270])
        outer.addWidget(splitter,1)
        self.progress = QProgressBar()
        self.progress.setRange(0,100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        outer.addWidget(self.progress)
        self.status = QLabel('就绪  ·  支持多模型比较与拖线剖面')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        footer = QHBoxLayout()
        footer.addWidget(self.status,1)
        self.github_link = QLabel('<a href="https://github.com/Henry0222/chairside-compare" style="color:#0071e3;text-decoration:none;">Henry Van · GitHub ↗</a>')
        self.github_link.setOpenExternalLinks(True)
        self.github_link.setToolTip('打开 Chairside Compare GitHub 仓库')
        footer.addWidget(self.github_link,0,Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(footer)
        self.setCentralWidget(root)
        self.sync_controls()
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(2000)
        self.watch_timer.timeout.connect(self.check_watch)
        self.autosave = QTimer(self)
        self.autosave.setInterval(15000)
        self.autosave.timeout.connect(lambda: self.save_case(quiet=True))
        self.autosave.start()

    def button(self,title,callback):
        button = QPushButton(title)
        button.clicked.connect(lambda checked=False: callback())
        return button

    def section_label(self,layout,title):
        label = QLabel(title)
        label.setObjectName('section')
        layout.addWidget(label)

    def spin(self,minimum,maximum,step,value):
        widget = QDoubleSpinBox()
        widget.setRange(minimum,maximum)
        widget.setDecimals(2)
        widget.setSingleStep(step)
        widget.setValue(value)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(self.settings_changed)
        return widget

    def sync_controls(self):
        self.settings.step = .05  # Also migrate old case/preferences wheel steps.
        self.loading = True
        for name in ('lower','upper','tolerance'):
            getattr(self,name).setValue(getattr(self.settings,name))
        for key,slider in self.opacity.items():
            slider.setValue(round(getattr(self.settings,key+'_opacity')*100))
        self.reverse.setChecked(self.settings.reverse)
        self.color.setChecked(self.settings.color)
        self.loading = False

    def settings_changed(self,*args):
        if self.loading or not hasattr(self,'measure_buttons'):
            return
        values = asdict(self.settings)
        values.update({name:getattr(self,name).value() for name in ('lower','upper','tolerance')})
        values.update({key+'_opacity':slider.value()/100 for key,slider in self.opacity.items()})
        values.update(tooth=self.settings.tooth,reverse=self.reverse.isChecked(),color=self.color.isChecked())
        try:
            self.settings = DisplaySettings.from_dict(values)
        except ValueError as error:
            self.status.setText(str(error)+'；暂时保留上一组有效设置。')
            return
        self.viewer.apply_settings(self.settings)
        self.update_coverage()
        self.refresh_records()
        try:
            atomic_json(PREFS,asdict(self.settings))
        except OSError as error:
            self.status.setText(f'显示已更新，默认设置保存失败：{error}')

    def update_coverage(self):
        import numpy as np
        if self.viewer.arrays is not None:
            arrays = self.viewer.arrays
            valid = self.viewer.target_valid
            total = len(valid)
            good = int(valid.sum())
            self.coverage.setText(f'灰色：不可评价区域\n参考模型有效顶点：{good} / {total}')

    def drop_models(self,files):
        if self.job is not None and self.job.isRunning():
            self.status.setText('正在处理模型，请等待完成后再拖入。')
            return
        if not files:
            return
        self.save_case(quiet=True)
        for path in files:
            self.paths[new_model_key(self.paths)] = path
        self.summary()
        self.comparison_combo.blockSignals(True)
        self.comparison_combo.setCurrentIndex(len(self.paths)-1)
        self.comparison_combo.blockSignals(False)
        self.preview_models()

    def import_models(self):
        dialog = InputDialog(self.paths,self)
        if dialog.exec():
            paths = dialog.paths()
            if paths != self.paths:
                self.paths = paths
                self.summary()
                self.preview_models()

    def preview_models(self):
        self.save_case(quiet=True)
        self.registration_cache = self.state or self.registration_cache
        self.state,self.case_path = None,None
        self.set_busy(True)
        self.banner.setText('加载原始模型…尚未配准，不显示偏差或测量')
        self.job = PreviewJob(self.paths,self)
        self.job.progress.connect(self.progress_update)
        self.job.completed.connect(self.receive_preview)
        self.job.error.connect(self.error)
        self.job.finished.connect(lambda:self.set_busy(False))
        self.job.start()

    def receive_preview(self,result):
        paths,arrays = result
        self.paths = paths
        self.summary()
        for key in paths:
            if getattr(self.settings,key+'_opacity',.4)==0:
                setattr(self.settings,key+'_opacity',.4)
        self.sync_controls()
        pair = self.selected_pair()
        import numpy as np
        arrays['values'] = np.zeros(len(arrays.get((pair[1] if pair else 'current')+'_vertices',[])))
        arrays['valid'] = np.zeros(len(arrays['values']),bool)
        self.viewer.load(arrays,self.settings,preview=True,paths=paths,pair=pair)
        self.pair_cache.clear()
        self.banner.setText('原始模型预览 · 尚未配准 · '+('继续拖入模型' if len(paths)<2 else '点击开始自动配准'))
        self.status.setText('模型按导入顺序追加；可在导入窗口移除。配准时所有模型分别对齐所选参考模型。')
        self.refresh_records()
        self.probe_button.setChecked(False)
        self.viewer.measure_enabled = False
        self.progress.setValue(0)

    def summary(self):
        self.file_summary.setText('\n'.join(f'{i} · {Path(path).name}' for i,path in enumerate(self.paths.values(),1)))
        for combo,default in [(self.reference_combo,0),(self.comparison_combo,max(0,len(self.paths)-1))]:
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for i,(key,path) in enumerate(self.paths.items(),1):
                combo.addItem(f'{i} · {Path(path).name}',key)
                combo.setItemData(combo.count()-1,path,Qt.ItemDataRole.ToolTipRole)
            index = combo.findData(selected)
            combo.setCurrentIndex(index if index>=0 else default)
            combo.blockSignals(False)
        if len(self.paths)>1 and self.reference_combo.currentData()==self.comparison_combo.currentData():
            reference = self.reference_combo.currentData()
            self.select_pair_controls((reference,next(k for k in self.paths if k!=reference)))

    def selected_pair(self):
        pair = (self.reference_combo.currentData(),self.comparison_combo.currentData())
        return pair if None not in pair and pair[0]!=pair[1] else None

    def select_pair_controls(self,pair):
        for combo,key in zip((self.reference_combo,self.comparison_combo),pair):
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(key))
            combo.blockSignals(False)

    def pair_changed(self,*args):
        if self.loading or self.viewer.arrays is None:
            return
        pair = self.selected_pair()
        if pair is None:
            # Keep two distinct models selected when the reference changes.
            keys = list(self.paths)
            if len(keys)<2:
                return
            reference = self.reference_combo.currentData()
            pair = (reference,next(k for k in keys if k!=reference))
            self.select_pair_controls(pair)
        if self.viewer.preview:
            import numpy as np
            arrays = dict(self.viewer.arrays)
            arrays['values'] = np.zeros(len(arrays[pair[1]+'_vertices']))
            arrays['valid'] = np.zeros(len(arrays['values']),bool)
            view = self.viewer.view_state()
            self.viewer.load(arrays,self.settings,preview=True,paths=self.paths,pair=pair)
            self.viewer.restore_view(view)
            return
        if not self.state or pair == tuple(self.state.get('pair',('target','current'))):
            return
        if self.job and self.job.isRunning():
            return
        if pair in self.pair_cache:
            self.receive_pair((pair,self.pair_cache[pair]))
            return
        self.set_busy(True)
        self.status.setText('正在计算所选模型之间的偏差…')
        self.job = PairJob(self.viewer.arrays,pair,self.state.get('failed',False),self)
        self.job.completed.connect(self.receive_pair)
        self.job.error.connect(self.pair_error)
        self.job.finished.connect(lambda:self.set_busy(False))
        self.job.start()

    def pair_error(self,message):
        self.select_pair_controls(self.state.get('pair',('target','current')))
        self.status.setText('切换比较失败，保留原比较：'+message)

    def receive_pair(self,result):
        pair,data = result
        self.pair_cache[pair] = {'values':data['values'],'valid':data['valid']}
        arrays = dict(self.viewer.arrays)
        arrays.update(values=data['values'],valid=data['valid'])
        view = self.viewer.view_state()
        view['probes'],view['measurements'] = [],[]
        self.state['pair'] = list(pair)
        self.viewer.load(arrays,self.settings,paths=self.paths,pair=pair)
        self.viewer.restore_view(view)
        self.update_coverage()
        self.refresh_records()
        self.save_case(quiet=True)
        self.status.setText('已切换比较；旧模型对的测量已清除。彩虹图显示在所选参考模型上。')

    def draw_section_mode(self):
        if self.viewer.arrays is None:
            self.draw_button.setChecked(False)
            self.status.setText('请先导入模型。')
            return
        self.pick_button.setChecked(False)
        self.probe_button.setChecked(False)
        self.viewer.set_draw_mode(self.draw_button.isChecked())
        if self.draw_button.isChecked():
            self.status.setText('在 3D 视图左键拖线创建剖面；松开生成，Esc 取消。')

    def clear_custom_section(self):
        self.viewer.clear_custom_section()
        self.measure_buttons['none'].setChecked(True)

    def next_scan(self):
        if not self.paths:
            self.import_models()
            return
        path,_ = QFileDialog.getOpenFileName(self,'选择新一轮椅旁扫描','','模型 (*.stl *.ply)')
        if path:
            self.drop_models([path])

    def set_busy(self,busy):
        for button in [self.run_button,self.import_button,self.open_button,self.next_button,self.watch_button]:
            button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.reference_combo.setEnabled(not busy)
        self.comparison_combo.setEnabled(not busy)

    def start(self):
        if self.job is not None and self.job.isRunning():
            return
        if len(self.paths)<2:
            self.import_models()
            return
        pair = self.selected_pair()
        if pair is None:
            self.status.setText('请选择两个不同模型。')
            return
        self.save_case(quiet=True)
        self.set_busy(True)
        self.banner.setText('正在配准新扫描 · 下方旧画面仅供查看，尚未更新')
        self.progress.setValue(0)
        self.job = Job(dict(self.paths),self.state or self.registration_cache,self,reference=pair[0],comparison=pair[1])
        self.job.progress.connect(self.progress_update)
        self.job.completed.connect(self.receive)
        self.job.error.connect(self.error)
        self.job.finished.connect(lambda: self.set_busy(False))
        self.job.start()

    def progress_update(self,fraction,message):
        self.progress.setValue(round(fraction*100))
        self.status.setText(message)

    def cancel(self):
        if self.job:
            self.job.requestInterruption()
            self.status.setText('正在取消，将在当前原生计算阶段结束后停止…')

    def error(self,message):
        self.banner.setText('本轮未完成 · 仍显示上一轮结果')
        self.status.setText(message)
        QMessageBox.warning(self,'本轮未完成',message)

    def receive(self,result):
        state,arrays,path = result
        previous_view = None
        reference = state.get('reference','initial')
        if self.state and self.state.get('hashes',{}).get(reference) == state.get('hashes',{}).get(reference):
            previous_view = self.viewer.view_state()
            # Measurements describe a specific scan pair and cannot migrate to a new scan.
            previous_view.pop('probes',None)
            previous_view.pop('measurements',None)
        self.state,self.case_path = state,Path(path)
        self.paths = dict(state['paths'])
        self.summary()
        pair = tuple(state.get('pair',('target','current')))
        self.state.setdefault('geometry_pair',list(pair))
        self.state['pair'] = list(pair)
        self.select_pair_controls(pair)
        self.pair_cache = {pair:{'values':arrays['values'],'valid':arrays['valid']}}
        if 'display' in state:
            self.settings = DisplaySettings.from_dict(state['display'])
        region = state.get('region_suggestion')
        self.sync_controls()
        center = region['center'] if region else None
        self.viewer.load(arrays,self.settings,center,paths=self.paths,pair=pair)
        if state.get('view') or previous_view:
            self.viewer.restore_view(state.get('view') or previous_view)
        elif center is not None:
            self.viewer.focus()
        failed = state['failed']
        warning = any(r['status']=='warning' or r.get('warnings') for r in state['registrations'].values())
        if failed:
            title = '配准未通过 · 仅供位置复核，彩虹提示已禁用'
        elif warning:
            title = '配准完成 · 核心有警告，请查看诊断并复核共同牙面'
        else:
            title = '配准完成 · 请检查牙位与共同牙面的对齐情况'
        self.banner.setText(title)
        self.banner.setStyleSheet(f'background:{"#fce9e9" if failed else "#fff4dc" if warning else "#e4f2f0"};color:#51462e;padding:10px;border-radius:6px;')
        self.progress.setValue(100)
        self.status.setText(f'结果时间 {state["created"][:19]}  ·  '+('已恢复 / 定位局部区域，可点击修正。' if self.viewer.center is not None else '当前显示全模型偏差；可点击模型定位治疗区域。'))
        self.update_coverage()
        self.refresh_records()
        self.save_case(quiet=True)

    def pick_mode(self):
        self.viewer.set_draw_mode(False)
        self.probe_button.setChecked(False)
        self.viewer.measure_enabled = False
        self.viewer.main.pick_mode = self.pick_button.isChecked()
        if self.pick_button.isChecked():
            self.status.setText('可连续点击修正截面中心；再次点击定位按钮结束定位，拖动仍可旋转。')

    def probe_mode(self):
        self.viewer.set_draw_mode(False)
        if self.viewer.arrays is None or self.viewer.preview or not self.viewer.arrays['valid'].any():
            self.probe_button.setChecked(False)
            self.status.setText('请先完成配准，再开启偏差测量。')
            return
        self.pick_button.setChecked(False)
        self.viewer.main.pick_mode = False
        self.viewer.measure_enabled = self.probe_button.isChecked()
        self.status.setText('短按所选比较模型标注平均偏差；拖动仍可旋转模型。')

    def refresh_records(self):
        if not hasattr(self,'records'):
            return
        self.records.clear()
        for record in self.viewer.measurements:
            text = f'{record["value"]:.3f} mm' if record['mode']=='distance' else f'{record["value"]:+.2f}°'
            self.records.addItem(text)

    def restore_record(self,item):
        index = self.records.row(item)
        if index>=0:
            self.viewer.restore_measurement(index)

    def region_changed(self,center):
        self.update_coverage()
        self.status.setText('已更新截面中心；可继续点击修正，再次点击定位按钮结束。')

    def save_case(self,quiet=False):
        if self.state is None or self.case_path is None:
            return
        self.state['display'] = asdict(self.settings)
        self.state['view'] = self.viewer.view_state()
        try:
            atomic_json(self.case_path,self.state)
            if not quiet:
                self.status.setText('病例视图已保存：颜色、透明度、牙位、截面和相机位置。')
        except OSError as error:
            self.status.setText(f'病例保存失败：{error}')

    def open_case(self):
        path,_ = QFileDialog.getOpenFileName(self,'打开病例',str(ROOT/'cases'),'病例 (case.json)')
        if path:
            try:
                self.save_case(quiet=True)
                self.receive(load_case(path))
            except Exception as error:
                QMessageBox.warning(self,'无法打开病例',str(error))

    def diagnostics(self):
        if not self.state:
            return
        from .diagnostics import diagnostic_summary
        dialog = QDialog(self)
        dialog.setWindowTitle('配准诊断 · 3.0.0')
        dialog.resize(740,620)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(diagnostic_summary(self.state))
        layout.addWidget(report)
        raw = QPushButton('查看原始诊断')
        raw.setCheckable(True)
        raw.toggled.connect(lambda checked:report.setPlainText(
            json.dumps({k:self.state.get(k) for k in ['created','paths','registrations','mesh_warnings']},ensure_ascii=False,indent=2)
            if checked else diagnostic_summary(self.state)))
        layout.addWidget(raw)
        close = QPushButton('关闭')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def watch(self):
        if self.watch_folder:
            self.watch_folder = None
            self.watch_timer.stop()
            self.watch_button.setText('监测导出文件夹')
            return
        folder = QFileDialog.getExistingDirectory(self,'选择本病例导出文件夹')
        if folder:
            self.watch_folder = Path(folder)
            self.watch_seen = {}
            self.check_watch(initial=True)
            self.watch_timer.start()
            self.watch_button.setText('停止目录监测')
            self.status.setText('目录监测已启用；新文件写入稳定后将提示导入，避免误用其他病例。')

    def check_watch(self,initial=False):
        if not self.watch_folder or (self.job and self.job.isRunning()):
            return
        try:
            for path in self.watch_folder.iterdir():
                if path.suffix.lower() not in ('.stl','.ply') or not path.is_file():
                    continue
                stat = path.stat()
                signature = (stat.st_size,stat.st_mtime_ns)
                entry = self.watch_seen.get(path)
                if entry is None or entry[0] != signature:
                    self.watch_seen[path] = (signature,0,initial)
                    continue
                signature,count,notified = entry
                self.watch_seen[path] = (signature,count+1,notified)
                if not notified and count>=2 and stat.st_size>0:
                    self.watch_seen[path] = (signature,count+1,True)
                    if QMessageBox.question(self,'发现新扫描',f'{path.name}\n追加到当前病例并预览？确认模型后可点击开始配准。') == QMessageBox.StandardButton.Yes:
                        self.drop_models([str(path)])
                        break
        except OSError as error:
            self.status.setText(f'目录暂不可读取：{error}')

    def closeEvent(self,event):
        if self.job and self.job.isRunning():
            self.cancel()
            self.status.setText('已请求取消；计算停止后即可关闭窗口。')
            event.ignore()
            return
        self.save_case(quiet=True)
        self.viewer.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    if '--case' in sys.argv:
        index = sys.argv.index('--case')
        if index+1<len(sys.argv):
            QTimer.singleShot(250,lambda:window.receive(load_case(sys.argv[index+1])))
    app.exec()
