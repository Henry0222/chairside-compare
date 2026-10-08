from pathlib import Path
import sys
from chairside_compare.core import load_case, unpack_mesh, DisplaySettings
from auto_alignment.result_viewer import ViewerData,GeneralResultViewer,configure_open3d_font
from auto_alignment.deviation_scale import DeviationScale
from open3d.visualization import gui


def viewer_data(path):
    state,arrays,path = load_case(path)
    if state['failed']:
        raise ValueError('配准失败的病例不能启动偏差测量')
    settings = DisplaySettings.from_dict(state.get('display',{}))
    meshes = {k:unpack_mesh({'vertices':arrays[k+'_vertices'],'triangles':arrays[k+'_triangles']}) for k in ('target','current')}
    values = arrays['values']*(-1 if settings.reverse else 1)
    scale = DeviationScale(settings.lower,-settings.tolerance,settings.tolerance,settings.upper,-settings.tolerance,settings.tolerance)
    return ViewerData(path,meshes['target'],meshes['current'],values,scale,settings.reverse,
        registration_status=state['registrations']['current']['status'],
        registration_warnings=tuple(state['registrations']['current'].get('warnings',[])),
        annotation_file=path.with_name('general_viewer_annotations.json'))


def main(path):
    data = viewer_data(path)
    app = gui.Application.instance
    app.initialize()
    configure_open3d_font(app)
    viewer = GeneralResultViewer(data)
    viewer.scene_widget.scene.set_background([1.,1.,1.,1.])
    viewer.annotation_toggle.is_on = True
    viewer._on_annotation_enabled(True)
    if '--smoke' in sys.argv:
        import threading,time
        def stop():
            time.sleep(2)
            app.post_to_main_thread(viewer.window,viewer.window.close)
        threading.Thread(target=stop,daemon=True).start()
    app.run()
