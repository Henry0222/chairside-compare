"""Real Qt events: multi-model overlays, chosen pair, drawn sections and restore."""
from pathlib import Path
import sys
import time
import copy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint,QPointF,Qt,QEvent
from PySide6.QtGui import QMouseEvent,QImage,QPainter
from PySide6.QtTest import QTest
from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
from vtkmodules.util.numpy_support import vtk_to_numpy
from chairside_compare.app import MainWindow,STYLE,InputDialog
import chairside_compare.app as app_module
from chairside_compare.demo import create_demo
from chairside_compare.core import atomic_json,load_case

output = ROOT/'verification'/'multimodel'
output.mkdir(parents=True,exist_ok=True)
app_module.PREFS = output/'preferences.json'
app = QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
window = MainWindow()

def drag(widget,start,end):
    QTest.mousePress(widget,Qt.MouseButton.LeftButton,pos=start)
    event = QMouseEvent(QEvent.Type.MouseMove,QPointF(end),QPointF(widget.mapToGlobal(end)),
                       Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget,event)
    assert window.viewer.main.stroke.isVisible()
    QTest.mouseRelease(widget,Qt.MouseButton.LeftButton,pos=end)
    app.processEvents()

def wait_job():
    deadline = time.monotonic()+30
    while window.job and window.job.isRunning() and time.monotonic()<deadline:
        QTest.qWait(20)
    app.processEvents()
    assert not window.job or not window.job.isRunning()

def capture(path):
    canvas = window.grab()
    painter = QPainter(canvas)
    for pane in (window.viewer.main,window.viewer.section):
        painter.drawPixmap(pane.mapTo(window,QPoint()),pane.grab())
        pane.render()
        capture = vtkWindowToImageFilter()
        capture.SetInput(pane.vtk.GetRenderWindow())
        capture.ReadFrontBufferOff()
        capture.Update()
        frame = capture.GetOutput()
        w,h,_ = frame.GetDimensions()
        pixels = vtk_to_numpy(frame.GetPointData().GetScalars()).reshape(h,w,-1)
        assert pixels.std()>5
        pixels = np.ascontiguousarray(pixels[::-1,:,:3])
        image = QImage(pixels.data,w,h,3*w,QImage.Format.Format_RGB888).copy()
        painter.drawImage(pane.vtk.mapTo(window,QPoint()),image)
        if pane is window.viewer.main:
            panel = window.viewer.opacity_panel
            painter.drawPixmap(panel.mapTo(window,QPoint()),panel.grab())
    painter.end()
    canvas.save(str(path))

try:
    state,arrays,_ = create_demo(output/'fixture')
    # Four meshes in one fixed frame, including a repeated round.
    state['paths']['model_1'] = state['paths']['current']
    for field in ('vertices','triangles'):
        arrays['model_1_'+field] = arrays['current_'+field].copy()
    np.savez_compressed(output/'geometry.npz',**arrays)
    atomic_json(output/'case.json',state)
    window.show()
    app.processEvents()
    window.receive((state,arrays,output/'case.json'))
    app.processEvents()
    viewer = window.viewer
    assert len(viewer.actors)==4
    assert list(viewer.opacity_sliders)==list(state['paths'])
    assert all(Path(state['paths'][k]).name in slider.toolTip() for k,slider in viewer.opacity_sliders.items())
    viewer.opacity_sliders['model_1'].setValue(17)
    assert viewer.actors['model_1'].GetProperty().GetOpacity()==.17
    assert 'https://github.com/Henry0222/chairside-compare' in window.github_link.text()
    assert window.github_link.openExternalLinks()
    original = viewer.section_frame()
    camera = viewer.main.renderer.GetActiveCamera()
    camera_before = (camera.GetPosition(),camera.GetViewUp())
    width,height = viewer.main.vtk.width(),viewer.main.vtk.height()
    start,end = QPoint(width//2-80,height//2),QPoint(width//2+80,height//2+45)
    window.draw_button.click()
    drag(viewer.main.vtk,start,end)
    assert not viewer.main.draw_mode and not window.draw_button.isChecked()
    assert window.clear_section_button.isEnabled()
    assert viewer.original_section==original
    assert (camera.GetPosition(),camera.GetViewUp())==camera_before
    assert abs(np.dot(viewer.normal,camera.GetDirectionOfProjection()))<1e-10
    assert abs(np.dot(viewer.normal,viewer.up))<1e-10
    assert len(viewer.section_segments())>5
    assert viewer.section.renderer.GetActiveCamera().GetParallelProjection()
    # Save/reopen preserves both the custom plane and its pre-draw baseline.
    custom = viewer.section_frame()
    window.save_case(quiet=True)
    window.receive(load_case(output/'case.json'))
    assert viewer.original_section==original and window.clear_section_button.isEnabled()
    assert viewer.section_frame()==custom
    assert viewer.opacity_sliders['model_1'].value()==17
    capture(output/'custom_section.png')
    # Redrawing replaces the custom section, never its original baseline.
    window.draw_button.click()
    drag(viewer.main.vtk,QPoint(width//2,height//2-60),QPoint(width//2,height//2+60))
    assert viewer.original_section==original
    window.clear_section_button.click()
    assert viewer.section_frame()==original
    assert not window.clear_section_button.isEnabled()
    window.draw_button.click()
    QTest.keyClick(viewer.main.vtk,Qt.Key.Key_Escape)
    assert not viewer.main.draw_mode and not window.draw_button.isChecked()
    # Opacity must stay tied to stable model IDs when comparing another round.
    window.comparison_combo.setCurrentIndex(window.comparison_combo.findData('model_1'))
    wait_job()
    assert viewer.comparison_key=='model_1'
    assert viewer.main.pick_actor is viewer.actors['model_1']
    assert viewer.probe is not None
    assert viewer.opacity_sliders['model_1'].value()==17
    window.reference_combo.setCurrentIndex(window.reference_combo.findData('current'))
    wait_job()
    assert viewer.reference_key=='current'
    assert viewer.actors['current'].GetMapper().GetScalarVisibility()
    assert not viewer.actors['target'].GetMapper().GetScalarVisibility()
    assert abs(viewer.arrays['values']).max()<1e-4
    window.save_case(quiet=True)
    restored = load_case(output/'case.json')
    window.receive(restored)
    assert viewer.reference_key=='current' and viewer.comparison_key=='model_1'
    assert abs(viewer.arrays['values']).max()<1e-4
    assert len(viewer.actors)==4
    capture(output/'multiple_models.png')
    # Arbitrary model counts and removal in the import editor.
    dialog = InputDialog(window.paths,window)
    for i in range(4):
        dialog.add_row(state['paths']['target'])
    assert len(dialog.paths())==8
    dialog.validate()
    assert dialog.result()==dialog.DialogCode.Accepted
    dialog.deleteLater()
    atomic_json(output/'result.json',{'ok':True,'models':4,'import_rows':8,'draw_events':True,
                'original_section_restored':True,'pair_switch_reload':True,'parallel_projection':True})
    print('Multi-model UI checks passed: drawing, restore, Esc, opacity order, pair switch and persistence.')
finally:
    wait_job()
    window.close()
    app.processEvents()
