from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs, collect_data_files
root = Path(SPECPATH).parent
hidden = collect_submodules('vtkmodules') + ['open3d.cpu.pybind','scipy.spatial.transform._rotation']
data = collect_data_files('open3d', includes=['resources/**'])
a = Analysis([str(root/'run.py')], pathex=[str(root/'src'),str(root.parent/'general_model_registration'/'src')],
    binaries=collect_dynamic_libs('open3d'), datas=data,
    hiddenimports=hidden, excludes=['torch','tensorflow','tensorboard','IPython','jupyter','pandas','matplotlib','open3d._ml3d','PyQt5','PyQt6','PySide2'], noarchive=False)
# Qt uses the Windows ICU API. A Poppler directory on the developer PATH can
# incorrectly supply its incompatible, version-suffixed ICU DLL instead.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='ChairsideCompare',console=False,upx=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='ChairsideCompare')
