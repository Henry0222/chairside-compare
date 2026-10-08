"""Exercise actual Qt/VTK rendering and capture both layouts for visual review."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run  # configures explicit source paths
from PySide6.QtWidgets import QApplication,QDialog,QScrollArea
from chairside_compare.app import MainWindow, STYLE
from chairside_compare.core import DisplaySettings
from PySide6.QtCore import QPoint
from PySide6.QtCore import QPointF,Qt,QEvent
from PySide6.QtGui import QImage,QPainter
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
from vtkmodules.util.numpy_support import vtk_to_numpy
import numpy as np

def capture(window,path):
    # Qt QWidget.grab does not include native VTK child windows on Windows.
    # Composite their actual rendered framebuffers at their Qt geometry.
    canvas = window.grab()
    painter = QPainter(canvas)
    large,small = (window.viewer.section,window.viewer.main) if window.viewer.swapped else (window.viewer.main,window.viewer.section)
    for pane in (large,small):
        pane.render()
        capture = vtkWindowToImageFilter()
        capture.SetInput(pane.vtk.GetRenderWindow())
        capture.ReadFrontBufferOff()
        capture.Update()
        frame = capture.GetOutput()
        width,height,_ = frame.GetDimensions()
        pixels = vtk_to_numpy(frame.GetPointData().GetScalars()).reshape(height,width,-1)
        assert pixels.std()>5, 'Empty VTK render'
        pixels = np.ascontiguousarray(pixels[::-1,:,:3])
        qimage = QImage(pixels.data,width,height,3*width,QImage.Format.Format_RGB888).copy()
        origin = pane.mapTo(window,QPoint(0,0))
        painter.drawPixmap(origin,pane.grab())
        origin = pane.vtk.mapTo(window,QPoint(0,0))
        painter.drawImage(origin,qimage)
    painter.end()
    canvas.save(str(path))

app = QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
window = MainWindow()
window.show()
app.processEvents()
from chairside_compare.core import load_case,atomic_json
import chairside_compare.app as app_module
from chairside_compare.app import InputDialog
from PySide6.QtCore import QMimeData,QUrl
from PySide6.QtGui import QDragEnterEvent,QDropEvent,QMouseEvent

if '--case' in sys.argv:
    source = Path(sys.argv[sys.argv.index('--case')+1])
    if source.is_dir():
        source = source/'case.json'
    state,arrays,_ = load_case(source)
else:
    from chairside_compare.demo import create_demo
    state,arrays,_ = create_demo(ROOT/'verification'/'test_fixture')
copy = ROOT/'verification'/'ui_copy'
copy.mkdir(parents=True,exist_ok=True)
np.savez_compressed(copy/'geometry.npz',**arrays)
atomic_json(copy/'case.json',state)
app_module.PREFS = copy/'preferences.json'
window.receive((state,arrays,copy/'case.json'))
app.processEvents()
assert not hasattr(window,'demo_button')
assert window.viewer.main.renderer.GetBackground()==(1.,1.,1.)
assert window.viewer.main.renderer.GetActiveCamera().GetParallelProjection()
assert window.viewer.section.renderer.GetActiveCamera().GetParallelProjection()
assert window.probe_radius.value()==.1 and window.viewer.probe_radius==.1
assert window.viewer.probe is not None  # prepared during loading, not the first click
assert window.viewer.plane_actor.GetProperty().GetColor()==(1.,.38,.04)
assert not window.viewer.marker_actor.GetVisibility()
assert window.viewer.actors['target'].GetMapper().GetScalarVisibility()
assert not window.viewer.actors['current'].GetMapper().GetScalarVisibility()
np.testing.assert_allclose(window.viewer.actors['current'].GetProperty().GetColor(),(.62,.85,.64))
window.opacity['section'].setValue(65)
assert window.viewer.plane_actor.GetProperty().GetOpacity()==.65
window.opacity['section'].setValue(30)
# Repositioning remains active after each click until explicitly switched off.
center_before = window.viewer.center.copy()
window.pick_button.click()
window.viewer.set_center(center_before+[.1,0,0])
assert window.pick_button.isChecked() and window.viewer.main.pick_mode
window.viewer.set_center(center_before)
assert window.pick_button.isChecked() and window.viewer.main.pick_mode
window.pick_button.click()
assert not window.viewer.main.pick_mode
output = ROOT/'preview'
output.mkdir(exist_ok=True)
capture(window,output/'revision_overview.png')
original = window.viewer.view_state()
QTest.mouseDClick(window.viewer.section.vtk,Qt.MouseButton.LeftButton,pos=QPoint(100,100))
event = QWheelEvent(QPointF(100,100),QPointF(100,100),QPoint(),QPoint(0,360),
    Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
app.sendEvent(window.viewer.section.vtk,event)
app.processEvents()
capture(window,output/'revision_section.png')
assert window.viewer.swapped
assert abs(window.viewer.offset-original['offset']-.3)<1e-6
window.save_case(quiet=True)
from chairside_compare.core import load_case
saved = load_case(window.case_path)
window.receive(saved)
assert window.viewer.swapped
assert abs(window.viewer.offset-original['offset']-.3)<1e-6
def drag(widget,button,start,end):
    QTest.mousePress(widget,button,pos=start)
    move = QMouseEvent(QEvent.Type.MouseMove,QPointF(end),QPointF(widget.mapToGlobal(end)),
        Qt.MouseButton.NoButton,button,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget,move)
    QTest.mouseRelease(widget,button,pos=end)
    app.processEvents()

normal = window.viewer.normal.copy()
drag(window.viewer.section.vtk,Qt.MouseButton.LeftButton,QPoint(200,200),QPoint(250,220))
assert not np.allclose(normal,window.viewer.normal)
pan = window.viewer.section_pan.copy()
drag(window.viewer.section.vtk,Qt.MouseButton.MiddleButton,QPoint(250,220),QPoint(320,260))
assert not np.allclose(pan,window.viewer.section_pan)
focal = window.viewer.section.renderer.GetActiveCamera().GetFocalPoint()
window.viewer.apply_settings(window.settings)
np.testing.assert_allclose(focal,window.viewer.section.renderer.GetActiveCamera().GetFocalPoint())

# Wheel over controls continues scrolling without changing their values.
assert not hasattr(window,'tooth') and not hasattr(window,'probe_records')
scrollbar = window.findChild(QScrollArea).verticalScrollBar()
for control in (window.lower,window.upper,window.tolerance,window.opacity['current'],window.measure_mode):
    scrollbar.setValue(scrollbar.maximum())
    before_scroll = scrollbar.value()
    value = control.value() if hasattr(control,'value') else control.currentIndex()
    wheel = QWheelEvent(QPointF(4,4),QPointF(4,4),QPoint(),QPoint(0,120),
        Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    app.sendEvent(control,wheel)
    assert (control.value() if hasattr(control,'value') else control.currentIndex())==value
    assert scrollbar.value()<before_scroll
scrollbar.setValue(0)

window.viewer.restore_view(original)
if not window.viewer.swapped:
    window.viewer.swap()

def screen(point,pane):
    camera = pane.renderer.GetActiveCamera()
    up = np.asarray(camera.GetViewUp())
    right = np.cross(up,-np.asarray(camera.GetDirectionOfProjection()))
    d = np.asarray(point)-np.asarray(camera.GetFocalPoint())
    scale = pane.vtk.height()/(2*camera.GetParallelScale())
    return QPoint(round(pane.vtk.width()/2+np.dot(d,right)*scale),round(pane.vtk.height()/2-np.dot(d,up)*scale))

# Downward pointer movement must move a front-facing point down on screen.
saved_view = window.viewer.view_state()
camera = window.viewer.main.renderer.GetActiveCamera()
camera.SetPosition(0,0,100)
camera.SetFocalPoint(0,0,0)
camera.SetViewUp(0,1,0)
before = screen([0,0,10],window.viewer.main)
drag(window.viewer.main.vtk,Qt.MouseButton.LeftButton,QPoint(200,150),QPoint(200,190))
after = screen([0,0,10],window.viewer.main)
assert after.y()>before.y()
window.viewer.restore_view(saved_view)

segments = window.viewer.section_segments()
assert len(segments)>10
midpoints = segments.mean(axis=1)
camera = window.viewer.section.renderer.GetActiveCamera()
right = np.cross(camera.GetViewUp(),-np.asarray(camera.GetDirectionOfProjection()))
up = np.asarray(camera.GetViewUp())
points = [midpoints[np.argmin(midpoints@right)],midpoints[np.argmax(midpoints@right)],
          midpoints[np.argmin(midpoints@up)],midpoints[np.argmax(midpoints@up)]]
window.viewer.set_measure_mode('distance')
for point in points[:2]:
    QTest.mouseClick(window.viewer.section.vtk,Qt.MouseButton.LeftButton,pos=screen(point,window.viewer.section))
assert len(window.viewer.measurements)==1
assert window.viewer.measurements[0]['value']>0
window.viewer.set_measure_mode('angle')
for point in points:
    QTest.mouseClick(window.viewer.section.vtk,Qt.MouseButton.LeftButton,pos=screen(point,window.viewer.section))
assert len(window.viewer.measurements)==2
capture(window,output/'revision_measurements.png')
window.viewer.swap()
window.probe_button.setChecked(True)
window.probe_mode()
current = window.viewer.arrays['current_vertices']
nearest = np.argsort(np.linalg.norm(current-window.viewer.center,axis=1))[:400:20]
for index in nearest:
    QTest.mouseClick(window.viewer.main.vtk,Qt.MouseButton.LeftButton,pos=screen(current[index],window.viewer.main))
    if window.viewer.probes:
        break
assert len(window.viewer.probes)>0,window.status.text()
assert 'point_mm' not in window.viewer.probes[-1]
assert not hasattr(window,'probe_records')
assert window.records.count()==len(window.viewer.measurements)
capture(window,output/'revision_probe.png')
window.save_case(quiet=True)
window.receive(load_case(window.case_path))
assert len(window.viewer.measurements)==2
assert len(window.viewer.probes)>0

# File manager drag/drop into each role, then validate a two-file import.
dialog = InputDialog({},window)
for key in ('target','current'):
    edit = dialog.edits[key]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(state['paths'][key])])
    enter = QDragEnterEvent(QPoint(5,5),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(edit,enter)
    drop = QDropEvent(QPointF(5,5),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(edit,drop)
    assert Path(edit.text()).resolve()==Path(state['paths'][key]).resolve(),repr(edit.text())
assert 'initial' not in dialog.paths()
dialog.validate()
assert dialog.result()==QDialog.DialogCode.Accepted

# Raw meshes display before any registration and measurement is disabled.
paths = dialog.paths()
from chairside_compare.core import load_preview
window.receive_preview((paths,load_preview(paths)))
assert set(window.viewer.actors)=={'target','current'}
assert window.viewer.preview and not window.viewer.scalar.GetVisibility()
assert all(actor.GetVisibility() for actor in window.viewer.actors.values())
window.state = None
window.case_path = None
window.paths = {}
import time
for key in ('target','current'):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(state['paths'][key])])
    surface = window.viewer.main.vtk
    enter = QDragEnterEvent(QPoint(150,100),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(surface,enter)
    drop = QDropEvent(QPointF(150,100),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(surface,drop)
    deadline = time.monotonic()+25
    while window.job is not None and window.job.isRunning() and time.monotonic()<deadline:
        QTest.qWait(30)
    app.processEvents()
    assert not window.job.isRunning()
    assert Path(window.paths[key]).resolve()==Path(state['paths'][key]).resolve()
    assert set(window.viewer.actors)==({'target'} if key=='target' else {'target','current'})
    assert window.viewer.preview
window.close()
app.processEvents()
print('UI smoke passed: full arch, mean-only probe, drag direction, orange plane, section measurement, persistence, one/two file drops into 3D view.')
