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
    QSlider, QFileDialog, QMessageBox, QDialog, QLineEdit, QProgressBar, QFormLayout, QAbstractSpinBox, QListWidget, QPlainTextEdit)

from .core import DisplaySettings, run_case, load_case, atomic_json, load_preview, normalize_paths
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
            arrays = load_preview(self.paths,self.progress.emit)
            if not self.isInterruptionRequested():
                self.completed.emit((self.paths,arrays))
        except Exception as error:
            self.error.emit(str(error))


class Job(QThread):
    progress = Signal(float,str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, paths, previous, parent=None):
        super().__init__(parent)
        self.paths, self.previous = paths, previous

    def run(self):
        try:
            result = run_case(self.paths, ROOT/'cases', self.progress.emit, self.isInterruptionRequested, self.previous)
            self.completed.emit(result)
        except Exception as error:
            self.error.emit(f'{type(error).__name__}: {error}')
            traceback.print_exc()


class InputDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle('导入模型 · 两组或三组')
        self.setAcceptDrops(True)
        self.resize(740, 320)
        layout = QVBoxLayout(self)
        title = QLabel('建立同一病例的比较流程')
        title.setObjectName('heading')
        layout.addWidget(title)
        note = QLabel('从文件夹拖入对应输入框，或拖入窗口依次填充。单位：毫米；支持 STL / PLY。\n仅目标＋当前即可配准；初诊可选。导入后先显示原始位置，点击开始再配准。')
        note.setObjectName('muted')
        layout.addWidget(note)
        self.edits = {}
        for key, title in [('target','01  目标模型（必填）'),('current','02  当前模型（必填）'),('initial','03  初诊模型（可选）')]:
            row = QHBoxLayout()
            label = QLabel(title)
            label.setFixedWidth(145)
            row.addWidget(label)
            edit = ModelPathEdit(paths.get(key,''))
            edit.setPlaceholderText('拖入 STL / PLY 文件…' if key!='initial' else '可留空；两模型时以目标为固定参考')
            edit.setClearButtonEnabled(True)
            row.addWidget(edit,1)
            button = QPushButton('浏览')
            button.clicked.connect(lambda checked=False,e=edit: self.browse(e))
            row.addWidget(button)
            self.edits[key] = edit
            layout.addLayout(row)
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

    def browse(self, edit):
        path,_ = QFileDialog.getOpenFileName(self,'选择全牙列模型',edit.text(),'模型 (*.stl *.ply)')
        if path:
            edit.setText(path)

    def validate(self):
        paths = self.paths()
        if not all(k in paths for k in ('target','current')) or any(not Path(p).is_file() or Path(p).suffix.lower() not in ('.stl','.ply') for p in paths.values()):
            QMessageBox.warning(self,'模型不完整','请导入目标和当前模型；初诊可留空。所有已填项需为存在的 STL / PLY 文件。')
            return
        self.accept()

    def paths(self):
        return {k:e.text().strip() for k,e in self.edits.items() if e.text().strip()}

    def dragEnterEvent(self,event):
        if ModelPathEdit.files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        files = iter(ModelPathEdit.files(event.mimeData()))
        for key in ('target','current','initial'):
            if not self.edits[key].text():
                path = next(files,None)
                if path:
                    self.edits[key].setText(path)
        event.acceptProposedAction()

    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Chairside Compare · 椅旁预备对比 0.6')
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
        self.banner = QLabel('准备就绪  ·  导入目标与当前模型，初诊模型可选')
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
        panel_layout.addWidget(self.button('打开通用配准 3D 查看器',self.general_viewer))
        hint = QLabel('自动定位仅提示变化区域，\n不是牙齿分割或自动编号。')
        hint.setObjectName('muted')
        panel_layout.addWidget(hint)
        self.radius = self.spin(2,20,.5,self.settings.radius)
        form = QFormLayout()
        form.addRow('截面范围 mm',self.radius)
        panel_layout.addLayout(form)
        panel_layout.addWidget(self.button('聚焦治疗区域',self.viewer.focus))
        self.section_label(panel_layout,'模型与偏差')
        self.opacity = {}
        for key,title in [('current','当前牙体'),('target','目标预备体'),('initial','初诊牙列')]:
            line = QHBoxLayout()
            label = QLabel(title)
            label.setMinimumHeight(24)
            line.addWidget(label)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0,100)
            slider.setValue(round(getattr(self.settings,key+'_opacity')*100))
            slider.setToolTip('0% 隐藏，100% 不透明')
            slider.valueChanged.connect(self.settings_changed)
            line.addWidget(slider)
            panel_layout.addLayout(line)
            self.opacity[key] = slider
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
        panel_layout.addWidget(self.button('按当前 3D 视角设截面',self.viewer.section_from_camera))
        panel_layout.addWidget(self.button('交换大小画面',self.viewer.swap))
        self.step = self.spin(.01,2,.05,self.settings.step)
        form = QFormLayout()
        form.addRow('滚轮步长 mm',self.step)
        panel_layout.addLayout(form)
        instruction = QLabel('左拖旋转截面，中拖平移\n滚轮移截面，Ctrl+滚轮缩放\n双击小画面：交换视图')
        instruction.setObjectName('muted')
        panel_layout.addWidget(instruction)
        self.measure_mode = QComboBox()
        self.measure_mode.addItem('截面浏览','none')
        self.measure_mode.addItem('距离测量','distance')
        self.measure_mode.addItem('角度测量','angle')
        self.measure_mode.currentIndexChanged.connect(lambda _:self.viewer.set_measure_mode(self.measure_mode.currentData()))
        panel_layout.addWidget(self.measure_mode)
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
        self.status = QLabel('就绪  ·  第一版功能预览  ·  自动定位后请检查牙位和对齐情况')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
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
        self.loading = True
        for name in ('lower','upper','tolerance','radius','step'):
            getattr(self,name).setValue(getattr(self.settings,name))
        for key,slider in self.opacity.items():
            slider.setValue(round(getattr(self.settings,key+'_opacity')*100))
        self.reverse.setChecked(self.settings.reverse)
        self.color.setChecked(self.settings.color)
        self.loading = False

    def settings_changed(self,*args):
        if self.loading or not hasattr(self,'step'):
            return
        values = {name:getattr(self,name).value() for name in ('lower','upper','tolerance','radius','step')}
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
            self.coverage.setText(f'灰色：不可评价区域\n目标牙列有效顶点：{good} / {total}')

    def drop_models(self,files):
        if self.job is not None and self.job.isRunning():
            self.status.setText('正在处理模型，请等待完成后再拖入。')
            return
        if len(files)>3:
            self.status.setText('一次最多拖入三个模型：目标、当前、可选初诊。')
            return
        if len(files)>=2:
            self.paths = dict(zip(('target','current','initial'),files))
        elif files:
            role = 'target' if 'target' not in self.paths else 'current'
            self.paths[role] = files[0]
        else:
            return
        self.summary()
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
        for key in paths:
            if getattr(self.settings,key+'_opacity')==0:
                setattr(self.settings,key+'_opacity',.4)
        self.sync_controls()
        self.viewer.load(arrays,self.settings,preview=True)
        self.banner.setText('原始模型预览 · 尚未配准 · '+('继续拖入当前模型' if 'current' not in paths else '点击开始自动配准'))
        self.status.setText('拖入规则：首次单文件为目标，随后单文件为当前；同时拖入按目标、当前、初诊排列。可在导入窗口调整。')
        self.refresh_records()
        self.probe_button.setChecked(False)
        self.viewer.measure_enabled = False
        self.progress.setValue(0)

    def summary(self):
        self.file_summary.setText('\n'.join(f'{label}：{Path(self.paths[key]).name}' for key,label in
            [('initial','初诊'),('target','目标'),('current','当前')] if key in self.paths))

    def next_scan(self):
        if 'target' not in self.paths:
            self.import_models()
            return
        path,_ = QFileDialog.getOpenFileName(self,'选择新一轮椅旁扫描','','模型 (*.stl *.ply)')
        if path:
            self.paths['current'] = path
            self.summary()
            self.start()

    def set_busy(self,busy):
        for button in [self.run_button,self.import_button,self.open_button,self.next_button,self.watch_button]:
            button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)

    def start(self):
        if self.job is not None and self.job.isRunning():
            return
        if not all(k in self.paths for k in ('target','current')):
            self.import_models()
            return
        self.save_case(quiet=True)
        self.set_busy(True)
        self.banner.setText('正在配准新扫描 · 下方旧画面仅供查看，尚未更新')
        self.progress.setValue(0)
        self.job = Job(dict(self.paths),self.state or self.registration_cache,self)
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
        if 'display' in state:
            self.settings = DisplaySettings.from_dict(state['display'])
        region = state.get('region_suggestion')
        self.sync_controls()
        center = region['center'] if region else None
        self.viewer.load(arrays,self.settings,center)
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
        self.probe_button.setChecked(False)
        self.viewer.measure_enabled = False
        self.viewer.main.pick_mode = self.pick_button.isChecked()
        if self.pick_button.isChecked():
            self.status.setText('可连续点击修正截面中心；再次点击定位按钮结束定位，拖动仍可旋转。')

    def probe_mode(self):
        if self.viewer.arrays is None or self.viewer.preview or not self.viewer.arrays['valid'].any():
            self.probe_button.setChecked(False)
            self.status.setText('请先完成配准，再开启偏差测量。')
            return
        self.pick_button.setChecked(False)
        self.viewer.main.pick_mode = False
        self.viewer.measure_enabled = self.probe_button.isChecked()
        self.status.setText('短按当前模型标注平均偏差；拖动仍可旋转模型。')

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

    def general_viewer(self):
        if not self.case_path or not self.state or self.state.get('failed'):
            self.status.setText('请先打开已完成配准的病例。')
            return
        self.save_case(quiet=True)
        import subprocess
        try:
            with open(self.case_path.with_name('general_viewer.log'),'a',encoding='utf-8') as log:
                subprocess.Popen(([sys.executable,'--general-viewer',str(self.case_path)] if getattr(sys,'frozen',False)
                                  else [sys.executable,str(ROOT/'scripts'/'general_review.py'),str(self.case_path)]),
                             stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.status.setText('正在打开通用 3D 查看器，已开启点击偏差标注。')
        except OSError as error:
            self.status.setText(f'无法打开通用查看器：{error}')

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
                    if QMessageBox.question(self,'发现新扫描',f'{path.name}\n作为当前病例的新扫描导入并配准？') == QMessageBox.StandardButton.Yes:
                        self.paths['current'] = str(path)
                        self.summary()
                        self.start()
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
