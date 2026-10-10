"""Two persistent VTK views: swap layout, not cameras or scene objects."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal, QPointF, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QScrollArea
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkInteractionStyle  # noqa: F401
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonCore import vtkPoints, vtkLookupTable
from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray, vtkPlane, vtkPlanes, vtkStaticCellLocator
from vtkmodules.vtkFiltersCore import vtkPlaneCutter, vtkClipPolyData, vtkPolyDataNormals
from vtkmodules.vtkFiltersSources import vtkPlaneSource, vtkSphereSource, vtkLineSource
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera, vtkInteractorStyleImage
from vtkmodules.vtkRenderingCore import vtkActor, vtkActor2D, vtkPolyDataMapper2D, vtkPolyDataMapper, vtkRenderer, vtkCellPicker, vtkTextActor
from vtkmodules.vtkRenderingAnnotation import vtkScalarBarActor
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from .core import colors, target_color_values, pair_arrays
from .measurements import DeviationProbe, line_angle, snap_to_segments


def polydata(vertices, faces):
    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.asarray(vertices, dtype=float), deep=True))
    cells = vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(np.arange(0, 3*len(faces)+1, 3, dtype=np.int64), deep=True),
                  numpy_to_vtkIdTypeArray(np.asarray(faces, dtype=np.int64).ravel(), deep=True))
    data = vtkPolyData()
    data.SetPoints(points)
    data.SetPolys(cells)
    return data


class StrokeOverlay:
    """A VTK line, never a transparent Qt window over the native GL surface."""
    def __init__(self,pane):
        self.pane = pane
        self.points = None
        self.data = vtkPolyData()
        mapper = vtkPolyDataMapper2D()
        mapper.SetInputData(self.data)
        self.actor = vtkActor2D()
        self.actor.SetMapper(mapper)
        self.actor.GetProperty().SetColor(1,.42,0)
        self.actor.GetProperty().SetLineWidth(2)
        self.actor.PickableOff()
        self.actor.VisibilityOff()
        pane.renderer.AddActor2D(self.actor)

    def update(self):
        if self.points:
            ratio = self.pane.vtk.devicePixelRatioF()
            points = vtkPoints()
            for p in self.points:
                points.InsertNextPoint(p.x()*ratio,(self.pane.vtk.height()-1-p.y())*ratio,0)
            cells = vtkCellArray()
            cells.InsertNextCell(2)
            cells.InsertCellPoint(0)
            cells.InsertCellPoint(1)
            self.data.SetPoints(points)
            self.data.SetLines(cells)
            self.pane.render()

    def show(self):
        self.pane.renderer.AddActor2D(self.actor)
        self.actor.VisibilityOn()
        self.update()

    def hide(self):
        self.actor.VisibilityOff()
        self.pane.render()

    def isVisible(self):
        return bool(self.actor.GetVisibility())


class Pane(QWidget):
    double_clicked = Signal()
    scroll_section = Signal(float)
    picked = Signal(object)
    clicked = Signal(object)
    dragged = Signal(object, float, float)
    files_dropped = Signal(object)
    line_drawn = Signal(object, object)
    drawing_cancelled = Signal()
    viewport_changed = Signal()

    def __init__(self, title, section=False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('viewPane')
        self.setStyleSheet('QWidget#viewPane {background:#ffffff;border:1px solid #e5e5ea;border-radius:10px;}')
        self.section = section
        self.pick_mode = False
        self.draw_mode = False
        self.plane_editor = None
        self.pick_actor = None
        self.pick_locator = None
        self.pick_face_id = None
        self.setAcceptDrops(not section)
        self.button = self.press = self.last = None
        self.moved = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self.label = QLabel(title)
        self.label.setStyleSheet('background:#fafafa;color:#6e6e73;padding:9px;font-size:12px;border-bottom:1px solid #e8e8ed;')
        layout.addWidget(self.label)
        self.vtk = QVTKRenderWindowInteractor(self)
        self.vtk.setAcceptDrops(not section)
        self.vtk.setMouseTracking(True)
        layout.addWidget(self.vtk, 1)
        self.renderer = vtkRenderer()
        self.renderer.SetBackground(1,1,1)
        self.renderer.SetUseFXAA(True)
        self.renderer.SetUseDepthPeeling(True)
        self.renderer.SetMaximumNumberOfPeels(80)
        self.renderer.SetOcclusionRatio(.1)
        window = self.vtk.GetRenderWindow()
        window.SetAlphaBitPlanes(1)
        window.SetMultiSamples(0)
        window.AddRenderer(self.renderer)
        self.renderer.GetActiveCamera().ParallelProjectionOn()
        self.style = vtkInteractorStyleImage() if section else vtkInteractorStyleTrackballCamera()
        self.vtk.GetRenderWindow().GetInteractor().SetInteractorStyle(self.style)
        self.vtk.installEventFilter(self)
        self.vtk.Initialize()
        self.stroke = StrokeOverlay(self)

    def showEvent(self,event):
        super().showEvent(event)
        QTimer.singleShot(0,self.render)

    def eventFilter(self, watched, event):
        if event.type()==QEvent.Type.Resize:
            self.viewport_changed.emit()
            QTimer.singleShot(0,self.render)
        if not self.draw_mode and self.plane_editor and self.plane_editor(event):
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.button = None
            self.stroke.hide()
            self.drawing_cancelled.emit()
            return True
        if not self.section and event.type() in (QEvent.Type.DragEnter,QEvent.Type.DragMove,QEvent.Type.Drop):
            files = self.drop_files(event)
            if files:
                event.acceptProposedAction()
                if event.type()==QEvent.Type.Drop:
                    self.files_dropped.emit(files)
                return True
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self.button = None
            self.double_clicked.emit()
            return True
        if event.type() == QEvent.Type.Wheel:
            steps = event.angleDelta().y()/120
            if not self.section or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                camera = self.renderer.GetActiveCamera()
                camera.SetParallelScale(max(.05, camera.GetParallelScale()*1.15**(-steps)))
                self.render()
            else:
                self.scroll_section.emit(steps)
            return True
        if event.type() == QEvent.Type.MouseButtonPress:
            self.button = event.button()
            self.press = self.last = QPointF(event.position())
            self.moved = False
            if self.draw_mode and self.button == Qt.MouseButton.LeftButton:
                self.vtk.setFocus()
                self.stroke.points = (self.press, self.press)
                self.stroke.show()
            return True
        if event.type() == QEvent.Type.MouseMove and self.button is not None:
            pos = QPointF(event.position())
            total = pos-self.press
            self.moved |= total.x()**2+total.y()**2>16
            if self.moved:
                delta = pos-self.last
                if self.draw_mode and self.button == Qt.MouseButton.LeftButton:
                    self.stroke.points = (self.press, pos)
                    self.stroke.update()
                else:
                    self.dragged.emit(self.button,delta.x(),delta.y())
            self.last = pos
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self.button is not None:
            if self.draw_mode and self.button == Qt.MouseButton.LeftButton:
                self.stroke.hide()
                self.line_drawn.emit(self.press, QPointF(event.position()))
            elif self.button == Qt.MouseButton.LeftButton and not self.moved:
                self.clicked.emit(QPointF(event.position()))
            self.button = None
            return True
        if event.type() in (QEvent.Type.FocusOut,QEvent.Type.Hide):
            self.button = None
            self.stroke.hide()
        return super().eventFilter(watched, event)

    def pick_surface(self, position):
        if self.pick_actor is not None and self.pick_actor.GetVisibility():
            picker = vtkCellPicker()
            picker.SetTolerance(.005)
            picker.PickFromListOn()
            picker.AddPickList(self.pick_actor)
            if self.pick_locator is not None:
                picker.AddLocator(self.pick_locator)
            scale = self.vtk.devicePixelRatioF()
            if picker.Pick(position.x()*scale,(self.vtk.height()-position.y()-1)*scale,0,self.renderer):
                self.pick_face_id = picker.GetCellId()
                return np.asarray(picker.GetPickPosition())
        return None

    @staticmethod
    def drop_files(event):
        return [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()
                and Path(u.toLocalFile()).suffix.lower() in ('.stl','.ply') and Path(u.toLocalFile()).is_file()]

    def dragEnterEvent(self,event):
        if not self.section and self.drop_files(event):
            event.acceptProposedAction()

    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)

    def dropEvent(self,event):
        files = self.drop_files(event)
        if not self.section and files:
            event.acceptProposedAction()
            self.files_dropped.emit(files)

    def render(self):
        if not self.vtk.isVisible() or getattr(self,'closed',False):
            return
        self.renderer.GetActiveCamera().ParallelProjectionOn()
        self.renderer.ResetCameraClippingRange()
        if getattr(self,'before_render',None):
            self.before_render()
        self.vtk.GetRenderWindow().Render()


class CompareViewer(QWidget):
    region_changed = Signal(object)
    message = Signal(str)
    records_changed = Signal()
    files_dropped = Signal(object)
    drawing_changed = Signal(bool)
    custom_section_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 440)
        self.main = Pane('3D 叠加  ·  左拖旋转 / 中拖平移 / 滚轮缩放', parent=self)
        self.section = Pane('截面对比  ·  滚轮移截面 / Ctrl+滚轮缩放 / 双击交换', section=True, parent=self)
        self.empty_hint = QLabel('拖入模型，开始对比\n\n支持两个或多个模型\n也可以使用右上角「导入模型」', self.main)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty_hint.setStyleSheet('background:#ffffff;color:#86868b;font-size:17px;border:none;')
        self.swapped = False
        self.main.double_clicked.connect(lambda: self.swap() if self.swapped else None)
        self.section.double_clicked.connect(lambda: self.swap() if not self.swapped else None)
        self.main.picked.connect(self.set_center)
        self.main.files_dropped.connect(self.files_dropped.emit)
        self.main.clicked.connect(self.click_main)
        self.main.line_drawn.connect(self.section_from_line)
        self.main.drawing_cancelled.connect(lambda: self.set_draw_mode(False))
        self.section.clicked.connect(self.click_section)
        self.main.dragged.connect(lambda b,x,y: self.drag(False,b,x,y))
        self.section.dragged.connect(lambda b,x,y: self.drag(True,b,x,y))
        self.section.scroll_section.connect(self.scroll)
        self.actors, self.data, self.cutters, self.section_actors = {}, {}, {}, {}
        self.arrays = self.settings = None
        self.center = None
        self.original_section = None
        self.section_bounds = None
        self.plane_gesture = None
        self.main.plane_editor = self.edit_plane_event
        self.reference_key, self.comparison_key = 'target', 'current'
        self.model_paths, self.model_opacities, self.opacity_sliders = {}, {}, {}
        self.opacity_panel = QScrollArea(self)
        self.opacity_panel.setWidgetResizable(True)
        self.opacity_panel.setStyleSheet('QScrollArea {background:#ffffff;border:1px solid #e5e5ea;border-radius:8px;}')
        self.opacity_panel.hide()
        self.main.viewport_changed.connect(self.layout_overlays)
        self.normal, self.up = np.array([0.,0.,1.]), np.array([0.,1.,0.])
        self.offset = 0.
        self.section_pan = np.zeros(2)
        self.preview = False
        self.measure_enabled = False
        self.probe_radius = .1
        self.probe = None
        self.probes, self.measurements, self.pending = [], [], []
        self.measure_mode = 'none'
        self.annotation_actors = {'main':[], 'section':[]}
        self.scalar = vtkScalarBarActor()
        self.scalar.SetTitle('mm')
        self.scalar.SetOrientationToVertical()
        self.scalar.SetPosition(.018,.43)
        self.scalar.SetWidth(.11)
        self.scalar.SetHeight(.49)
        self.scalar.SetNumberOfLabels(7)
        self.scalar.SetLabelFormat('%+.3f')
        self.scalar.UnconstrainedFontSizeOn()
        self.scalar.SetMaximumWidthInPixels(125)
        self.scalar.GetLabelTextProperty().SetFontSize(14)
        self.scalar.GetTitleTextProperty().SetFontSize(15)
        self.scalar.GetLabelTextProperty().BoldOff()
        self.scalar.GetLabelTextProperty().ItalicOff()
        self.scalar.GetTitleTextProperty().BoldOff()
        self.scalar.GetTitleTextProperty().ItalicOff()
        self.scalar.SetBarRatio(.23)
        self.scalar.GetLabelTextProperty().SetColor(.15,.2,.26)
        self.scalar.GetTitleTextProperty().SetColor(.15,.2,.26)
        self.scalar.GetLabelTextProperty().ShadowOff()
        self.scalar.GetTitleTextProperty().ShadowOff()
        self.plane = vtkPlane()
        self.crop = vtkPlanes()
        # Hidden contours may still update before a section center is selected.
        self.crop.SetBounds(-10,10,-10,10,-10,10)
        self.plane_source = vtkPlaneSource()
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(self.plane_source.GetOutputPort())
        self.plane_actor = vtkActor()
        self.plane_actor.SetMapper(mapper)
        self.plane_actor.GetProperty().SetColor(1.,.38,.04)
        self.plane_actor.GetProperty().SetOpacity(.3)
        self.plane_actor.GetProperty().EdgeVisibilityOn()
        self.plane_actor.GetProperty().SetEdgeColor(1.,.38,.04)
        self.plane_actor.PickableOff()
        self.marker_source = vtkSphereSource()
        self.marker_source.SetRadius(.22)
        marker_mapper = vtkPolyDataMapper()
        marker_mapper.SetInputConnection(self.marker_source.GetOutputPort())
        self.marker_actor = vtkActor()
        self.marker_actor.SetMapper(marker_mapper)
        self.marker_actor.GetProperty().SetColor(1,.78,.25)
        self.marker_actor.PickableOff()
        self.plane_outline_data = vtkPolyData()
        outline_mapper = vtkPolyDataMapper2D()
        outline_mapper.SetInputData(self.plane_outline_data)
        self.plane_outline = vtkActor2D()
        self.plane_outline.SetMapper(outline_mapper)
        self.plane_outline.GetProperty().SetColor(1,.38,.04)
        self.plane_outline.GetProperty().SetLineWidth(2)
        self.plane_outline.PickableOff()
        self.plane_outline.VisibilityOff()
        self.main.before_render = self.update_plane_outline

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout_views()

    def layout_views(self):
        large, small = (self.section,self.main) if self.swapped else (self.main,self.section)
        large.setGeometry(self.rect())
        width = min(440, max(280, int(self.width()*.35)))
        height = min(320, max(215, int(self.height()*.35)))
        small.setGeometry(16, self.height()-height-16, width, height)
        large.lower()
        small.raise_()
        self.empty_hint.setGeometry(0, 80, self.main.width(), max(150,self.main.height()//2))
        self.empty_hint.setVisible(self.arrays is None)
        if self.arrays is None:
            self.empty_hint.raise_()
        self.layout_overlays()
        if self.settings is not None:
            self.update_legend_ticks()

    def layout_overlays(self):
        if not hasattr(self,'opacity_panel'):
            return
        w,h = self.width(),self.height()
        self.empty_hint.setGeometry(self.main.vtk.geometry())
        width = 230
        self.opacity_panel.setGeometry(max(0,w-width-12),44,width,
                                       min(220, 18+len(self.opacity_sliders)*32, max(45,h//2)))
        self.opacity_panel.raise_()

    def swap(self):
        self.swapped = not self.swapped
        self.layout_views()
        self.render()

    def rebuild_opacity_panel(self):
        content = QWidget()
        content.setStyleSheet('QWidget {background:#ffffff;color:#1d1d1f;}')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10,6,10,6)
        layout.setSpacing(4)
        self.opacity_sliders = {}
        for index,(key,path) in enumerate(self.model_paths.items(),1):
            row = QHBoxLayout()
            label = QLabel(str(index))
            color = self.actors[key].GetProperty().GetColor()
            label.setStyleSheet('color:rgb(%d,%d,%d);font-weight:600;' % tuple(int(v*255) for v in color))
            row.addWidget(label)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0,100)
            slider.setValue(round(self.model_opacities[key]*100))
            tooltip = f'{Path(path).name}\n{path}\n不透明度：0% 隐藏，100% 不透明'
            slider.setToolTip(tooltip)
            label.setToolTip(tooltip)
            value = QLabel(f'{slider.value()}%')
            value.setFixedWidth(34)
            value.setToolTip(tooltip)
            slider.valueChanged.connect(lambda v,k=key,l=value:self.set_model_opacity(k,v,l))
            row.addWidget(slider,1)
            row.addWidget(value)
            layout.addLayout(row)
            self.opacity_sliders[key] = slider
        old = self.opacity_panel.takeWidget()
        if old:
            old.deleteLater()
        self.opacity_panel.setWidget(content)
        self.opacity_panel.setVisible(bool(self.model_paths))
        self.layout_views()

    def set_model_opacity(self,key,value,label=None):
        self.model_opacities[key] = value/100
        self.actors[key].GetProperty().SetOpacity(value/100)
        self.actors[key].SetVisibility(value>0)
        self.section_actors[key].SetVisibility(value>0 and self.center is not None)
        if label:
            label.setText(f'{value}%')
        self.render()

    def set_draw_mode(self,enabled):
        enabled = bool(enabled and self.arrays is not None)
        self.main.draw_mode = enabled
        self.main.stroke.hide()
        self.main.vtk.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        if enabled:
            self.main.pick_mode = self.measure_enabled = False
            self.main.vtk.setFocus()
        self.drawing_changed.emit(enabled)

    def section_frame(self):
        return {'center':None if self.center is None else self.center.tolist(),
                'normal':self.normal.tolist(),'up':self.up.tolist(),'offset':self.offset,
                'pan':self.section_pan.tolist(),
                'bounds':self.section_bounds,
                'scale':self.section.renderer.GetActiveCamera().GetParallelScale()}

    def bounds(self):
        r = self.settings.radius if self.settings else 10.
        return np.asarray(self.section_bounds if self.section_bounds is not None else [-r,r,-r,r],float)

    def plane_corners(self):
        left,right,bottom,top = self.bounds()
        origin = self.center+self.normal*self.offset
        axis = np.cross(self.up,self.normal)
        return np.array([origin+x*axis+y*self.up for x,y in
                         [(left,bottom),(right,bottom),(right,top),(left,top)]])

    def screen_points(self,points):
        camera = self.main.renderer.GetActiveCamera()
        normal = -np.asarray(camera.GetDirectionOfProjection())
        up = np.asarray(camera.GetViewUp())
        up -= normal*np.dot(up,normal)
        up /= np.linalg.norm(up)
        right = np.cross(up,normal)
        d = np.asarray(points)-camera.GetFocalPoint()
        scale = self.main.vtk.height()/(2*camera.GetParallelScale())
        return np.column_stack((self.main.vtk.width()/2+d@right*scale,
                                self.main.vtk.height()/2-d@up*scale))

    def plane_hit(self,position):
        if self.center is None or self.settings.section_opacity<=0:
            return None
        p = np.array([position.x(),position.y()])
        corners = self.screen_points(self.plane_corners())
        distances = np.linalg.norm(corners-p,axis=1)
        if distances.min()<11:
            return ('rotate',int(distances.argmin()))
        for i in range(4):
            a,b = corners[i],corners[(i+1)%4]
            segment = b-a
            length = np.dot(segment,segment)
            if length<25:
                continue
            fraction = np.clip(np.dot(p-a,segment)/length,0,1)
            if np.linalg.norm(p-a-fraction*segment)<8:
                return ('edge',i)
        return None

    def update_plane_outline(self):
        """Screen-sized handles share hit-test coordinates, above occluding meshes."""
        visible = self.center is not None and self.settings is not None and self.settings.section_opacity>0
        self.plane_outline.SetVisibility(visible)
        if not visible:
            return
        corners = self.screen_points(self.plane_corners())
        points,lines = vtkPoints(),vtkCellArray()
        ratio = self.main.vtk.devicePixelRatioF()
        height = self.main.vtk.height()
        def path(vertices):
            lines.InsertNextCell(len(vertices))
            for x,y in vertices:
                index = points.InsertNextPoint(x*ratio,(height-y)*ratio,0)
                lines.InsertCellPoint(index)
        path(np.vstack((corners,corners[0])))
        self.plane_outline_data.SetPoints(points)
        self.plane_outline_data.SetLines(lines)
        self.plane_outline_data.Modified()

    def edit_plane_event(self,event):
        kind = event.type()
        if self.plane_gesture and kind in (QEvent.Type.FocusOut,QEvent.Type.Hide):
            self.plane_gesture = None
        if self.plane_gesture and kind==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape:
            frame = self.plane_gesture['frame']
            self.center = np.asarray(frame['center'],float)
            self.normal,self.up = np.asarray(frame['normal']),np.asarray(frame['up'])
            self.offset,self.section_bounds = frame['offset'],frame['bounds']
            self.plane_gesture = None
            self.update_section()
            return True
        if kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton and self.center is not None:
            hit = ('move',0) if event.modifiers() & Qt.KeyboardModifier.AltModifier else self.plane_hit(event.position())
            # Handles own their small hit regions even during picking/measurement.
            # All other clicks continue to use the active model-picking mode.
            if hit:
                frame = self.section_frame()
                if self.original_section is None:
                    self.original_section = frame
                self.plane_gesture = {'hit':hit,'position':QPointF(event.position()),'frame':frame}
                self.custom_section_changed.emit(True)
                self.pending.clear()
                return True
        if kind==QEvent.Type.MouseMove:
            if self.plane_gesture:
                gesture = self.plane_gesture
                delta = event.position()-gesture['position']
                gesture['position'] = QPointF(event.position())
                camera = self.main.renderer.GetActiveCamera()
                outward = -np.asarray(camera.GetDirectionOfProjection())
                up = np.asarray(camera.GetViewUp())
                up -= outward*np.dot(up,outward)
                up /= np.linalg.norm(up)
                right = np.cross(up,outward)
                scale = 2*camera.GetParallelScale()/max(1,self.main.vtk.height())
                mode,index = gesture['hit']
                if mode=='move':
                    self.center += (right*delta.x()-up*delta.y())*scale
                elif mode=='rotate':
                    # In-plane rotation only: keep the normal and plane equation fixed.
                    bounds = self.bounds()
                    axis = np.cross(self.up,self.normal)
                    pivot = self.plane_corners().mean(axis=0)
                    matrix = np.array([[np.dot(axis,right),np.dot(self.up,right)],
                                       [-np.dot(axis,up),-np.dot(self.up,up)]])
                    pointer = np.array([event.position().x(),event.position().y()])
                    previous = pointer-np.array([delta.x(),delta.y()])
                    projected_pivot = self.screen_points([pivot])[0]
                    if abs(np.linalg.det(matrix))>.05:
                        a,b = np.linalg.solve(matrix,np.column_stack((previous-projected_pivot,pointer-projected_pivot))).T
                        if min(np.linalg.norm(a),np.linalg.norm(b))<8:
                            return True
                        angle = np.arctan2(a[0]*b[1]-a[1]*b[0],np.dot(a,b))
                    else:
                        # Near edge-on, use the selected corner's projected tangent.
                        tangent = np.cross(self.normal,self.plane_corners()[index]-pivot)
                        screen_tangent = np.array([np.dot(tangent,right),-np.dot(tangent,up)])/scale
                        norm = np.dot(screen_tangent,screen_tangent)
                        if norm<1:
                            self.message.emit('当前角点接近侧视，请稍微调整观察角度后旋转。')
                            return True
                        angle = np.clip(np.dot([delta.x(),delta.y()],screen_tangent)/norm,-.25,.25)
                    self.up = self.up*np.cos(angle)+np.cross(self.normal,self.up)*np.sin(angle)
                    self.up /= np.linalg.norm(self.up)
                    self.center = pivot-self.normal*self.offset-np.cross(self.up,self.normal)*(bounds[0]+bounds[1])/2-self.up*(bounds[2]+bounds[3])/2
                else:
                    # Four edges map to bottom/right/top/left bounds; hold opposite edge fixed.
                    coordinate = [2,1,3,0][index]
                    axis = self.up if coordinate>=2 else np.cross(self.up,self.normal)
                    projected = np.array([np.dot(axis,right),-np.dot(axis,up)])
                    norm = np.dot(projected,projected)
                    if norm>.025:
                        change = np.dot([delta.x(),delta.y()],projected)*scale/norm
                        bounds = self.bounds()
                        opposite = coordinate^1
                        bounds[coordinate] += change
                        if coordinate%2==0:
                            bounds[coordinate] = np.clip(bounds[coordinate],bounds[opposite]-400,bounds[opposite]-.2)
                        else:
                            bounds[coordinate] = np.clip(bounds[coordinate],bounds[opposite]+.2,bounds[opposite]+400)
                        self.section_bounds = bounds.tolist()
                    else:
                        self.message.emit('当前边接近侧视，先旋转模型视角再拉伸该边。')
                self.update_section()
                return True
            if self.main.button is None:
                hit = self.plane_hit(event.position()) if self.center is not None else None
                cursor = Qt.CursorShape.OpenHandCursor if hit and hit[0]=='rotate' else Qt.CursorShape.SizeAllCursor if hit else Qt.CursorShape.ArrowCursor
                self.main.vtk.setCursor(cursor)
        if kind==QEvent.Type.MouseButtonRelease and self.plane_gesture:
            self.plane_gesture = None
            self.message.emit('已更新剖面；Alt＋左拖移动，拖边缩放，拖角绕法向旋转。')
            return True
        return False

    def section_from_line(self,start,end):
        if self.arrays is None:
            return
        delta = end-start
        if delta.x()**2+delta.y()**2 < 64:
            self.message.emit('线段太短，请拖动至少 8 像素；Esc 取消绘制。')
            return
        camera = self.main.renderer.GetActiveCamera()
        outward = -np.asarray(camera.GetDirectionOfProjection())
        up = np.asarray(camera.GetViewUp())
        up -= outward*np.dot(up,outward)
        up /= np.linalg.norm(up)
        right = np.cross(up,outward)
        scale = 2*camera.GetParallelScale()/max(1,self.main.vtk.height())
        mid = (start+end)*.5
        center = (np.asarray(camera.GetFocalPoint()) + right*(mid.x()-self.main.vtk.width()/2)*scale
                  + up*(self.main.vtk.height()/2-mid.y())*scale)
        # Pick any visible model at the midpoint to center the finite section
        # around its surface. The plane itself is independent of this depth.
        picker = vtkCellPicker()
        picker.PickFromListOn()
        for actor in self.actors.values():
            if actor.GetVisibility():
                picker.AddPickList(actor)
        ratio = self.main.vtk.devicePixelRatioF()
        if picker.Pick(mid.x()*ratio,(self.main.vtk.height()-mid.y())*ratio,0,self.main.renderer):
            point = np.asarray(picker.GetPickPosition())
            center += outward*np.dot(point-center,outward)
        direction = right*delta.x()-up*delta.y()
        direction /= np.linalg.norm(direction)
        if self.original_section is None:
            self.original_section = self.section_frame()
        self.center = center
        self.section_bounds = None
        self.up = outward
        self.normal = np.cross(direction,self.up)
        self.normal /= np.linalg.norm(self.normal)
        self.offset = 0.
        self.section_pan[:] = 0
        self.pending.clear()
        self.set_draw_mode(False)
        self.custom_section_changed.emit(True)
        self.update_section(reset=True)
        self.message.emit('已按拖线及观察方向生成剖面；可测量、滚动浏览，清除后恢复原截面。')

    def clear_custom_section(self):
        self.set_draw_mode(False)
        saved = self.original_section
        if saved is not None:
            self.center = None if saved['center'] is None else np.asarray(saved['center'],float)
            self.normal, self.up = np.asarray(saved['normal']), np.asarray(saved['up'])
            self.offset, self.section_pan = saved['offset'], np.asarray(saved['pan'],float)
            self.section_bounds = saved.get('bounds')
            self.original_section = None
            self.pending.clear()
            self.update_section()
            self.section.renderer.GetActiveCamera().SetParallelScale(saved['scale'])
            self.render()
        self.set_measure_mode('none')
        self.custom_section_changed.emit(False)
        self.message.emit('已恢复原始截面浏览。')

    def load(self, arrays, settings, center=None, preview=False, paths=None, pair=None):
        self.empty_hint.hide()
        self.arrays, self.settings = arrays, settings
        self.preview = preview
        self.original_section = None
        self.section_bounds = None
        self.set_draw_mode(False)
        self.custom_section_changed.emit(False)
        keys = [k[:-9] for k in arrays if k.endswith('_vertices')]
        paths = paths or {k:k for k in keys}
        old_paths = self.model_paths
        self.model_opacities = {k:self.model_opacities.get(k, getattr(settings,k+'_opacity',.45))
                               if old_paths.get(k)==v else getattr(settings,k+'_opacity',.45)
                               for k,v in paths.items() if k in keys}
        self.model_paths = {k:v for k,v in paths.items() if k in keys}
        self.reference_key, self.comparison_key = pair or ('target','current')
        self.pair_data = pair_arrays(arrays,*pair) if pair else arrays
        self.probe = DeviationProbe(self.pair_data) if not preview and arrays['valid'].any() else None
        self.target_values, self.target_valid = (target_color_values(self.pair_data)
            if 'target_vertices' in self.pair_data else (np.zeros(0),np.zeros(0,bool)))
        self.probes, self.measurements, self.pending = [], [], []
        self.annotation_actors = {'main':[], 'section':[]}
        self.section_pan = np.zeros(2)
        self.main.renderer.RemoveAllViewProps()
        self.section.renderer.RemoveAllViewProps()
        self.actors, self.data, self.cutters, self.section_actors = {}, {}, {}, {}
        palette = {'initial': (.55,.60,.68), 'target': (.12,.48,.7), 'current': (.62,.85,.64)}
        self.clippers = {}
        self.normals = {}
        for i,key in enumerate(self.model_paths):
            if key+'_vertices' not in arrays:
                continue
            data = polydata(arrays[key+'_vertices'], arrays[key+'_triangles'])
            self.data[key] = data
            mapper = vtkPolyDataMapper()
            normals = vtkPolyDataNormals()
            normals.SetInputData(data)
            normals.SplittingOff()
            normals.ConsistencyOn()
            mapper.SetInputConnection(normals.GetOutputPort())
            self.normals[key] = normals
            mapper.ScalarVisibilityOff()
            actor = vtkActor()
            actor.SetMapper(mapper)
            shade = palette.get(key,[(.67,.48,.78),(.88,.65,.38),(.35,.72,.72)][i%3])
            actor.GetProperty().SetColor(*shade)
            actor.GetProperty().SetSpecular(.16)
            actor.GetProperty().SetSpecularPower(25)
            self.main.renderer.AddActor(actor)
            self.actors[key] = actor
            cutter = vtkPlaneCutter()
            cutter.SetInputData(data)
            cutter.SetPlane(self.plane)
            clip = vtkClipPolyData()
            clip.SetInputConnection(cutter.GetOutputPort())
            clip.SetClipFunction(self.crop)
            clip.InsideOutOn()
            contour_mapper = vtkPolyDataMapper()
            contour_mapper.SetInputConnection(clip.GetOutputPort())
            contour_mapper.ScalarVisibilityOff()
            contour = vtkActor()
            contour.SetMapper(contour_mapper)
            contour.GetProperty().SetColor(*shade)
            contour.GetProperty().SetLineWidth(2.5 if key != 'initial' else 1.)
            contour.GetProperty().LightingOff()
            self.section.renderer.AddActor(contour)
            self.cutters[key], self.section_actors[key], self.clippers[key] = cutter, contour, clip
        pick_key = self.comparison_key if self.comparison_key in self.actors else next(iter(self.actors))
        self.main.pick_actor = self.actors[pick_key]
        self.normals[pick_key].Update()
        self.main.pick_locator = vtkStaticCellLocator()
        self.main.pick_locator.SetDataSet(self.normals[pick_key].GetOutput())
        self.main.pick_locator.BuildLocator()
        self.main.renderer.AddActor(self.plane_actor)
        self.main.renderer.AddActor2D(self.plane_outline)
        self.main.renderer.AddActor(self.marker_actor)
        self.main.renderer.AddActor2D(self.scalar)
        self.main.renderer.ResetCamera()
        self.main.renderer.GetActiveCamera().SetViewUp(0,1,0)
        self.normal, self.up, self.offset = np.array([0.,0.,1.]), np.array([0.,1.,0.]), 0.
        self.center = None if center is None else np.asarray(center, float)
        self.apply_settings(settings)
        self.update_section(reset=True)
        self.rebuild_opacity_panel()

    def set_center(self, center):
        self.center = np.asarray(center, float)
        self.offset = 0.
        self.section_pan[:] = 0
        self.pending.clear()
        self.apply_settings(self.settings)
        self.update_section(reset=True)
        self.region_changed.emit(self.center.tolist())

    def apply_settings(self, settings):
        self.settings = settings
        if self.arrays is None:
            return
        for key in self.actors:
            opacity = self.model_opacities.get(key, getattr(settings,key+'_opacity',.45))
            self.actors[key].GetProperty().SetOpacity(opacity)
            self.actors[key].SetVisibility(opacity > 0)
            self.section_actors[key].SetVisibility(opacity > 0)
        self.plane_actor.GetProperty().SetOpacity(settings.section_opacity)
        if self.reference_key in self.data:
            rgb = colors(self.target_values, self.target_valid, self.arrays[self.reference_key+'_vertices'], None, settings)
            self.data[self.reference_key].GetPointData().SetScalars(numpy_to_vtk(rgb, deep=True))
            self.actors[self.reference_key].GetMapper().SetScalarVisibility(not self.preview and settings.color)
            self.actors[self.reference_key].GetMapper().SetColorModeToDirectScalars()
        values = np.linspace(settings.lower, settings.upper,256)
        palette_settings = replace(settings,reverse=False,color=True,radius=1e9)
        rgb_scale = colors(values,np.ones(256,bool),np.zeros((256,3)),None,palette_settings)
        self.lut = vtkLookupTable()
        self.lut.SetNumberOfTableValues(256)
        self.lut.SetRange(settings.lower,settings.upper)
        for i,rgb in enumerate(rgb_scale):
            self.lut.SetTableValue(i,*[float(c)/255 for c in rgb],1.)
        self.lut.Build()
        self.scalar.SetLookupTable(self.lut)
        self.update_legend_ticks()
        self.scalar.SetVisibility(not self.preview and settings.color and bool(self.arrays['valid'].any()))
        self.draw_annotations()
        self.update_section()

    def update_legend_ticks(self):
        settings = self.settings
        small = self.main.width()<600
        ticks = [settings.lower,0,settings.upper] if small else [settings.lower,
            (settings.lower-settings.tolerance)/2,-settings.tolerance,0,
            settings.tolerance,(settings.upper+settings.tolerance)/2,settings.upper]
        self.scalar.SetWidth(.24 if small else .11)
        self.scalar.GetLabelTextProperty().SetFontSize(12 if small else 14)
        self.scalar.SetCustomLabels(numpy_to_vtk(np.unique(ticks),deep=True))
        self.scalar.SetUseCustomLabels(True)

    def section_from_camera(self):
        camera = self.main.renderer.GetActiveCamera()
        self.normal = -np.asarray(camera.GetDirectionOfProjection())
        self.up = np.asarray(camera.GetViewUp())
        self.up -= self.normal*np.dot(self.up,self.normal)
        self.up /= np.linalg.norm(self.up)
        self.offset = 0.
        self.section_pan[:] = 0
        self.pending.clear()
        self.update_section(reset=True)

    def scroll(self, steps):
        if self.center is None:
            return
        self.offset = float(np.clip(self.offset+steps*self.settings.step, -self.settings.radius, self.settings.radius))
        self.pending.clear()
        self.update_section()

    def update_section(self, reset=False):
        has_center = self.center is not None
        self.plane_actor.SetVisibility(has_center)
        self.marker_actor.VisibilityOff()
        if not has_center or self.settings is None:
            for actor in self.section_actors.values():
                actor.SetVisibility(False)
            self.render()
            return
        for key, actor in self.section_actors.items():
            actor.SetVisibility(self.model_opacities.get(key,.45) > 0)
        origin = self.center + self.normal*self.offset
        radius = self.settings.radius
        self.plane.SetOrigin(*origin)
        self.plane.SetNormal(*self.normal)
        right = np.cross(self.up, self.normal)
        left,right_bound,bottom,top = self.bounds()
        corners = self.plane_corners()
        self.plane_source.SetOrigin(*corners[0])
        self.plane_source.SetPoint1(*corners[1])
        self.plane_source.SetPoint2(*corners[3])
        points = vtkPoints()
        for point in (origin+left*right,origin+right_bound*right,origin+bottom*self.up,origin+top*self.up):
            points.InsertNextPoint(*point)
        self.crop.SetPoints(points)
        self.crop.SetNormals(numpy_to_vtk(np.array([-right,right,-self.up,self.up]),deep=True))
        self.marker_source.SetCenter(*self.center)
        camera = self.section.renderer.GetActiveCamera()
        focal = origin+right*self.section_pan[0]+self.up*self.section_pan[1]
        camera.SetFocalPoint(*focal)
        camera.SetPosition(*(focal+self.normal*100))
        camera.SetViewUp(*self.up)
        if reset:
            camera.SetParallelScale(max(right_bound-left,top-bottom)*.6)
        self.section.label.setText(f'{"自定义剖面" if self.original_section is not None else "截面"} · {self.offset:+.2f} mm · 左拖旋转 / 中拖平移')
        self.draw_annotations()
        self.render()

    def focus(self):
        if self.center is None:
            self.main.renderer.ResetCamera()
        else:
            camera = self.main.renderer.GetActiveCamera()
            direction = -np.asarray(camera.GetDirectionOfProjection())
            camera.SetFocalPoint(*self.center)
            camera.SetPosition(*(self.center+direction*100))
            camera.SetParallelScale(self.settings.radius*1.7)
        self.main.render()

    def view_state(self):
        camera = self.main.renderer.GetActiveCamera()
        return {'center': None if self.center is None else self.center.tolist(), 'normal': self.normal.tolist(),
                'up': self.up.tolist(), 'offset': self.offset, 'swapped': self.swapped,
                'section_pan': self.section_pan.tolist(), 'probes':self.probes,'measurements':self.measurements,
                'model_opacities':dict(self.model_opacities), 'original_section':self.original_section,
                'section_bounds':self.section_bounds,
                'camera_position': list(camera.GetPosition()), 'camera_focal': list(camera.GetFocalPoint()),
                'camera_up': list(camera.GetViewUp()), 'camera_scale': camera.GetParallelScale(),
                'section_scale': self.section.renderer.GetActiveCamera().GetParallelScale()}

    def restore_view(self, state):
        if not state:
            return
        self.center = None if state.get('center') is None else np.asarray(state['center'], float)
        normal = np.asarray(state.get('normal', [0,0,1]),float)
        up = np.asarray(state.get('up', [0,1,0]),float)
        if np.isfinite(normal).all() and np.linalg.norm(normal) > 1e-6:
            self.normal = normal/np.linalg.norm(normal)
        up -= self.normal*np.dot(up,self.normal)
        if np.linalg.norm(up)>1e-6:
            self.up = up/np.linalg.norm(up)
        self.offset = float(state.get('offset',0))
        self.section_pan = np.asarray(state.get('section_pan',[0,0]),float)
        self.original_section = state.get('original_section')
        self.section_bounds = state.get('section_bounds')
        self.custom_section_changed.emit(self.original_section is not None)
        for key,value in state.get('model_opacities',{}).items():
            if key in self.actors and np.isfinite(value) and 0 <= value <= 1:
                self.model_opacities[key] = value
                if key in self.opacity_sliders:
                    self.opacity_sliders[key].setValue(round(value*100))
        self.probes = list(state.get('probes',[])) if not self.preview else []
        self.measurements = list(state.get('measurements',[]))
        for record in self.measurements:
            if record['mode']=='angle':
                record['value'],record['supplementary'] = line_angle(record['points'],record['normal'])
        self.swapped = bool(state.get('swapped',False))
        camera = self.main.renderer.GetActiveCamera()
        for field, setter in [('camera_position',camera.SetPosition),('camera_focal',camera.SetFocalPoint),('camera_up',camera.SetViewUp)]:
            if field in state:
                setter(*state[field])
        if 'camera_scale' in state:
            camera.SetParallelScale(state['camera_scale'])
        self.apply_settings(self.settings)
        self.update_section(reset=True)
        if 'section_scale' in state:
            self.section.renderer.GetActiveCamera().SetParallelScale(state['section_scale'])
        self.layout_views()
        self.render()
        self.records_changed.emit()

    def drag(self, section, button, dx, dy):
        pane = self.section if section else self.main
        camera = pane.renderer.GetActiveCamera()
        camera.ParallelProjectionOn()
        if button == Qt.MouseButton.RightButton:
            camera.SetParallelScale(max(.05,camera.GetParallelScale()*float(np.exp(np.clip(dy*.01,-.5,.5)))))
        elif button == Qt.MouseButton.MiddleButton:
            scale = 2*camera.GetParallelScale()/max(pane.vtk.height(),1)
            if section:
                self.section_pan += [-dx*scale,dy*scale]
            else:
                up = np.asarray(camera.GetViewUp())
                right = np.cross(up,-np.asarray(camera.GetDirectionOfProjection()))
                shift = (-dx*right+dy*up)*scale
                camera.SetPosition(*(np.asarray(camera.GetPosition())+shift))
                camera.SetFocalPoint(*(np.asarray(camera.GetFocalPoint())+shift))
        elif button == Qt.MouseButton.LeftButton:
            if section:
                def rotate(v,axis,angle):
                    angle = np.deg2rad(angle)
                    return v*np.cos(angle)+np.cross(axis,v)*np.sin(angle)+axis*np.dot(axis,v)*(1-np.cos(angle))
                self.normal = rotate(self.normal,self.up,-dx*.4)
                right = np.cross(self.up,self.normal)
                self.normal = rotate(self.normal,right,-dy*.4)
                self.up = rotate(self.up,right,-dy*.4)
                self.normal /= np.linalg.norm(self.normal)
                self.up -= self.normal*np.dot(self.up,self.normal)
                self.up /= np.linalg.norm(self.up)
                self.pending.clear()
            else:
                camera.Azimuth(-dx*.4)
                camera.Elevation(dy*.4)
                camera.OrthogonalizeViewUp()
        if section:
            self.update_section()
        else:
            pane.render()

    def click_main(self,position):
        if not self.main.pick_mode and not self.measure_enabled:
            return
        point = self.main.pick_surface(position)
        if point is None:
            self.message.emit('请点击可见的比较模型表面；可用右上角滑块调整不透明度。')
            return
        if self.main.pick_mode:
            self.set_center(point)
        elif self.measure_enabled:
            if self.preview or not self.arrays['valid'].any():
                self.message.emit('配准通过后才能测量模型偏差。')
                return
            try:
                if self.probe is None:
                    self.probe = DeviationProbe(self.pair_data)
                record = self.probe.measure(point,self.probe_radius,self.main.pick_face_id)
                record['id'] = f'P{len(self.probes)+1}'
                self.probes.append(record)
                sign = -1 if self.settings.reverse else 1
                self.message.emit(f'{record["id"]} 平均偏差 {record["mean_mm"]*sign:+.3f} mm（半径 {self.probe_radius:.2f} mm）')
                self.draw_annotations()
                self.main.render()
                self.records_changed.emit()
            except ValueError as error:
                self.message.emit(str(error))

    def set_measure_mode(self,mode):
        self.measure_mode = mode
        self.pending.clear()
        self.draw_annotations()
        self.render()

    def section_segments(self):
        pieces = []
        for key,clip in self.clippers.items():
            if not self.section_actors[key].GetVisibility():
                continue
            clip.Update()
            data = clip.GetOutput()
            if not data.GetNumberOfLines():
                continue
            pts = vtk_to_numpy(data.GetPoints().GetData())
            ids = vtk_to_numpy(data.GetLines().GetConnectivityArray())
            offsets = vtk_to_numpy(data.GetLines().GetOffsetsArray())
            for a,b in zip(offsets[:-1],offsets[1:]):
                poly = pts[ids[a:b]]
                if len(poly)>1:
                    pieces.append(np.stack((poly[:-1],poly[1:]),axis=1))
        return np.concatenate(pieces) if pieces else np.empty((0,2,3))

    def click_section(self,position):
        if self.measure_mode=='none' or self.center is None:
            return
        point = snap_to_segments([position.x(),position.y()],self.section_segments(),
            self.section.renderer.GetActiveCamera(),self.section.vtk.width(),self.section.vtk.height())
        if point is None:
            self.message.emit('请靠近可见截线点击（8 像素吸附），空白处不会添加测量点。')
            return
        self.add_section_point(point)

    def add_section_point(self,point):
        self.pending.append(np.asarray(point,float).tolist())
        count = 2 if self.measure_mode=='distance' else 4
        if len(self.pending)==count:
            points = np.asarray(self.pending)
            try:
                if count==2:
                    value = float(np.linalg.norm(points[1]-points[0]))
                    if value<1e-6:
                        raise ValueError('两个测量点不能重合')
                    label = f'{value:.3f} mm'
                    supplementary = None
                else:
                    value,supplementary = line_angle(points,self.normal)
                    label = f'{value:+.2f}°'
                record = {'id':f'M{len(self.measurements)+1}','mode':self.measure_mode,'points':self.pending.copy(),
                          'value':value,'supplementary':supplementary,'center':self.center.tolist(),
                          'normal':self.normal.tolist(),'up':self.up.tolist(),'offset':self.offset}
                self.measurements.append(record)
                self.message.emit(record['id']+' '+label)
                self.records_changed.emit()
            except ValueError as error:
                self.message.emit(str(error))
            self.pending.clear()
        else:
            self.message.emit(f'已取 {len(self.pending)}/{count} 点；'+('继续选终点。' if count==2 else '前两点定义直线一，后两点定义直线二。'))
        self.draw_annotations()
        self.section.render()

    def clear_measurements(self,kind='all'):
        if kind in ('all','probes'):
            self.probes.clear()
        if kind in ('all','section'):
            self.measurements.clear()
        self.pending.clear()
        self.draw_annotations()
        self.records_changed.emit()
        self.render()

    def restore_measurement(self,index):
        if not 0 <= index < len(self.measurements):
            return
        record = self.measurements[index]
        self.center,self.normal,self.up = [np.asarray(record[k],float) for k in ('center','normal','up')]
        self.offset = record['offset']
        self.section_pan[:] = 0
        self.pending.clear()
        self.apply_settings(self.settings)
        self.update_section(reset=True)

    def draw_annotations(self):
        for key,pane in [('main',self.main),('section',self.section)]:
            for actor in self.annotation_actors[key]:
                pane.renderer.RemoveActor(actor)
            self.annotation_actors[key] = []
        def add(key,actor):
            actor.PickableOff()
            pane = self.main if key=='main' else self.section
            pane.renderer.AddActor(actor)
            self.annotation_actors[key].append(actor)
        def marker(key,point):
            sphere = vtkSphereSource()
            sphere.SetCenter(*point)
            pane = self.main if key=='main' else self.section
            sphere.SetRadius(max(.025,pane.renderer.GetActiveCamera().GetParallelScale()*.006))
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(.7,.08,.28)
            add(key,actor)
        def line(key,a,b):
            source = vtkLineSource()
            source.SetPoint1(*a)
            source.SetPoint2(*b)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(source.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(.7,.08,.28)
            actor.GetProperty().SetLineWidth(2)
            add(key,actor)
        def label(key,point,text):
            # A 2D overlay with a world-coordinate anchor follows the camera
            # without participating in the mesh depth buffer.
            actor = vtkTextActor()
            actor.GetPositionCoordinate().SetCoordinateSystemToWorld()
            actor.GetPositionCoordinate().SetValue(*point)
            actor.SetInput(text)
            actor.GetTextProperty().SetColor(.5,.04,.19)
            actor.GetTextProperty().SetFontSize(15)
            actor.GetTextProperty().SetBackgroundColor(1,1,1)
            actor.GetTextProperty().SetBackgroundOpacity(.85)
            pane = self.main if key=='main' else self.section
            actor.PickableOff()
            pane.renderer.AddActor2D(actor)
            self.annotation_actors[key].append(actor)
        for record in self.probes:
            marker('main',record['anchor'])
            sign = -1 if self.settings.reverse else 1
            label('main',record['anchor'],f'{record["mean_mm"]*sign:+.3f} mm')
        for record in self.measurements:
            if self.center is None or not np.allclose(record['center'],self.center) or not np.allclose(record['normal'],self.normal) or abs(record['offset']-self.offset)>1e-6:
                continue
            for point in record['points']:
                marker('section',point)
            line('section',*record['points'][:2])
            if record['mode']=='angle':
                line('section',*record['points'][2:])
            text = f'{record["value"]:.3f} mm' if record['mode']=='distance' else f'{record["value"]:+.2f}°'
            label('section',np.mean(record['points'],axis=0),text)
        for i,point in enumerate(self.pending):
            marker('section',point)
            label('section',point,str(i+1))

    def render(self):
        self.main.render()
        self.section.render()

    def close(self):
        self.main.closed = self.section.closed = True
        self.main.vtk.Finalize()
        self.section.vtk.Finalize()
        return super().close()
