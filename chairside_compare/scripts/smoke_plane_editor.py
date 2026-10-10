"""Native-window regression for drawing and editing finite section planes."""
from pathlib import Path
import sys
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtCore import QPoint,QPointF,Qt,QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from chairside_compare.app import MainWindow,STYLE
import chairside_compare.app as module
from chairside_compare.demo import create_demo
from chairside_compare.core import atomic_json,load_case

out=ROOT/'verification'/('plane_editor_v074_real' if '--case' in sys.argv else 'plane_editor_v074')
out.mkdir(parents=True,exist_ok=True)
module.PREFS=out/'preferences.json'
app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
w=MainWindow();w.show();w.raise_();w.activateWindow()
v=w.viewer
def capture(name):
    QTest.qWait(200)
    pix=app.primaryScreen().grabWindow(w.winId())
    assert not pix.isNull()
    pix.save(str(out/(name+'.png')))
def point(x): return QPoint(int(x[0]),int(x[1]))
def drag(a,b,mod=Qt.KeyboardModifier.NoModifier,preview=False):
    widget=v.main.vtk
    QTest.mousePress(widget,Qt.MouseButton.LeftButton,mod,a)
    for f in np.linspace(.1,1,10):
        pos=QPointF(a)+(QPointF(b)-QPointF(a))*f
        app.sendEvent(widget,QMouseEvent(QEvent.Type.MouseMove,pos,QPointF(widget.mapToGlobal(pos.toPoint())),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,mod))
    if preview:
        assert v.main.stroke.isVisible()
        assert v.main.stroke.data.GetNumberOfLines()==1
        assert v.main.stroke.data.GetNumberOfPoints()==2
        capture('drag_in_progress')
    QTest.mouseRelease(widget,Qt.MouseButton.LeftButton,mod,b)
    app.processEvents()
try:
    capture('startup')
    state,arrays,_=load_case(Path(sys.argv[sys.argv.index('--case')+1])/'case.json') if '--case' in sys.argv else create_demo(out/'fixture')
    np.savez_compressed(out/'geometry.npz',**arrays);atomic_json(out/'case.json',state)
    w.receive((state,arrays,out/'case.json'));app.processEvents()
    assert not v.empty_hint.isVisible()
    assert not hasattr(w,'radius') and not hasattr(w,'step')
    buttons=[b.text() for b in w.findChildren(QPushButton)]
    assert '聚焦治疗区域' not in buttons and '交换大小画面' not in buttons
    layout=w.draw_button.parentWidget().layout()
    camera_button=next(b for b in w.findChildren(QPushButton) if b.text()=='按当前 3D 视角设截面')
    assert layout.indexOf(camera_button)==layout.indexOf(w.draw_button)+1
    assert layout.indexOf(w.clear_section_button)==layout.indexOf(camera_button)+1
    assert w.settings.step==.05 and v.settings.step==.05
    original=v.section_frame()
    width,height=v.main.vtk.width(),v.main.vtk.height()
    w.draw_button.click()
    drag(QPoint(width//2-60,height//2),QPoint(width//2+60,height//2+30),preview=True)
    v.offset=0
    v.scroll(1)
    assert abs(v.offset-.05)<1e-12
    v.scroll(-1)
    assert abs(v.offset)<1e-12
    camera=v.main.renderer.GetActiveCamera()
    origin=v.center+v.normal*v.offset
    camera.SetFocalPoint(*origin);camera.SetPosition(*(origin+v.normal*100));camera.SetViewUp(*v.up);camera.SetParallelScale(22)
    v.main.render()
    before=v.center.copy();pose=camera.GetPosition()
    drag(QPoint(width//2,height//2),QPoint(width//2+20,height//2+10),Qt.KeyboardModifier.AltModifier)
    assert np.linalg.norm(v.center-before)>.1 and camera.GetPosition()==pose
    w.pick_button.click();assert v.main.pick_mode
    corners=v.screen_points(v.plane_corners());edge=point((corners[1]+corners[2])/2)
    bounds=v.bounds().copy();drag(edge,edge+QPoint(25,0))
    assert v.bounds()[1]>bounds[1] and np.allclose(v.bounds()[[0,2,3]],bounds[[0,2,3]])
    before=v.normal.copy();up_before=v.up.copy();pivot_before=v.plane_corners().mean(axis=0);bounds=v.bounds().copy();corner=point(v.screen_points(v.plane_corners())[2])
    drag(corner,corner+QPoint(20,15))
    assert np.array_equal(v.normal,before) and np.linalg.norm(v.up-up_before)>.01 and np.allclose(bounds,v.bounds())
    assert np.allclose(pivot_before,v.plane_corners().mean(axis=0))
    assert v.plane_outline_data.GetNumberOfLines()==1 and v.plane_outline_data.GetNumberOfPoints()==5
    corners=v.plane_corners();assert abs(np.dot(corners[1]-corners[0],corners[3]-corners[0]))<1e-8
    # All four edges and corner rotation must also work in deviation mode.
    w.probe_button.click();assert v.measure_enabled
    for edge_index,coordinate in enumerate([2,1,3,0]):
        origin=v.center+v.normal*v.offset
        camera.SetFocalPoint(*origin);camera.SetPosition(*(origin+v.normal*100));camera.SetViewUp(*v.up)
        v.main.render()
        corners=v.screen_points(v.plane_corners())
        midpoint=point((corners[edge_index]+corners[(edge_index+1)%4])/2)
        change=QPoint(0,-15) if coordinate>=2 else QPoint(15,0)
        old=v.bounds().copy();drag(midpoint,midpoint+change)
        assert abs(v.bounds()[coordinate]-old[coordinate])>.1
        other=[i for i in range(4) if i!=coordinate]
        assert np.allclose(v.bounds()[other],old[other])
    before=v.normal.copy();up_before=v.up.copy();corner=point(v.screen_points(v.plane_corners())[2])
    drag(corner,corner+QPoint(15,10));assert np.array_equal(v.normal,before) and np.linalg.norm(v.up-up_before)>.01
    # Face-on, oblique and back views: a 30 degree pointer arc stays in-plane.
    for side in (1.,.5,-1.):
        pivot=v.plane_corners().mean(axis=0);normal=v.normal.copy();up0=v.up.copy()
        axis=np.cross(up0,normal)
        camera.SetFocalPoint(*pivot);camera.SetPosition(*(pivot+normal*100*side+axis*50));camera.SetViewUp(*up0)
        v.main.render()
        offset=v.plane_corners()[2]-pivot
        angle=np.deg2rad(30)
        destination=pivot+offset*np.cos(angle)+np.cross(normal,offset)*np.sin(angle)
        start,end=v.screen_points([v.plane_corners()[2],destination])
        drag(point(start),point(end))
        assert np.array_equal(v.normal,normal)
        np.testing.assert_allclose(v.plane_corners().mean(axis=0),pivot,atol=1e-10)
        actual=np.arctan2(np.dot(normal,np.cross(up0,v.up)),np.dot(up0,v.up))
        assert abs(np.rad2deg(actual)-30)<1.5
    assert not v.probes, 'Handle drag must not add a deviation measurement'
    w.probe_button.click()
    assert camera.GetParallelProjection()
    frame=v.section_frame();w.save_case(quiet=True);w.receive(load_case(out/'case.json'))
    for key,value in frame.items():
        np.testing.assert_allclose(v.section_frame()[key],value,rtol=0,atol=1e-12,err_msg=key)
    capture('edited_plane')
    fixed=v.opacity_panel.geometry();v.swap();app.processEvents()
    assert v.opacity_panel.parent() is v
    assert v.opacity_panel.geometry()==fixed
    assert v.opacity_panel.width()==230
    capture('swapped')
    w.resize(1120,780);app.processEvents()
    assert v.opacity_panel.geometry().right()==v.width()-13
    v.swap();app.processEvents();w.clear_section_button.click()
    assert v.section_frame()==original
    for mode,button in w.measure_buttons.items():
        button.click();assert button.isChecked()
        assert sum(b.isChecked() for b in w.measure_buttons.values())==1
    assert len({b.y() for b in w.measure_buttons.values()})==1
    assert not any('通用配准' in b.text() for b in w.findChildren(QPushButton))
    atomic_json(out/'result.json',{'ok':True,'native_screenshots':True,'draw_preview':True,'translate_resize_rotate':True,'rotation_preserves_normal_and_pivot':True,'outline_without_markers':True,'save_restore':True,'opacity_fixed_to_display_area':True,'handles_with_pick_and_probe_modes':True})
    print('Plane editor native-window regression passed.')
finally:
    w.close();app.processEvents()
