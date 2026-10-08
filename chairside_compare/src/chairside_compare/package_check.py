"""Offline package check writing only into an explicit output directory."""
from pathlib import Path
import json
import traceback

def main(folder):
    output=Path(folder);output.mkdir(parents=True,exist_ok=True)
    try:
        import numpy as np
        import open3d as o3d
        from PySide6.QtWidgets import QApplication
        from .core import DisplaySettings,comparison_values,run_case
        from .app import MainWindow,STYLE
        from . import app as app_module
        from .general_review import viewer_data
        app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
        app_module.ROOT=output;app_module.PREFS=output/'preferences.json'
        target=o3d.geometry.TriangleMesh.create_sphere(radius=4,resolution=16)
        current=o3d.geometry.TriangleMesh.create_sphere(radius=4.3,resolution=16)
        target.compute_vertex_normals();current.compute_vertex_normals()
        values,valid=comparison_values(current,target)
        assert np.median(values)>.29 and valid.all()
        arrays={'target_vertices':np.asarray(target.vertices),'target_triangles':np.asarray(target.triangles),
                'current_vertices':np.asarray(current.vertices),'current_triangles':np.asarray(current.triangles),'values':values,'valid':valid}
        window=MainWindow();window.show();window.viewer.load(arrays,DisplaySettings(),[0,0,3])
        app.processEvents();window.viewer.render()
        window.viewer.probe.measure(np.asarray(current.vertices)[10],.1)
        from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
        from vtkmodules.vtkIOImage import vtkPNGWriter
        image=vtkWindowToImageFilter();image.SetInput(window.viewer.main.vtk.GetRenderWindow());image.ReadFrontBufferOff();image.Update()
        writer=vtkPNGWriter();writer.SetFileName(str(output/'render.png'));writer.SetInputConnection(image.GetOutputPort());writer.Write()
        target_path=output/'target.stl';current_path=output/'current.stl'
        current.translate([1.,2.,3.]);current.compute_triangle_normals();target.compute_triangle_normals()
        o3d.io.write_triangle_mesh(str(target_path),target);o3d.io.write_triangle_mesh(str(current_path),current)
        state,_,_=run_case({'target':str(target_path),'current':str(current_path)},output/'cases')
        report={'ok':True,'render':True,'measurement':True,'registration_status':state['registrations']['current']['status'],'open3d':o3d.__version__,'numpy':np.__version__}
        window.close();app.processEvents()
    except Exception:
        report={'ok':False,'error':traceback.format_exc()}
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
