from dataclasses import asdict
import numpy as np
import open3d as o3d
import pytest
from chairside_compare import core
from chairside_compare.demo import create_demo


def test_color_sign_tolerance_mask_and_reverse():
    settings = core.DisplaySettings()
    values = np.array([-1,-.05,.05,1,0])
    valid = np.array([True,True,True,True,False])
    rgb = core.colors(values,valid,np.zeros((5,3)),[0,0,0],settings)
    assert rgb[0,2]>rgb[0,0]  # blue is inside target
    assert rgb[3,0]>rgb[3,2]  # red is outside target
    assert np.array_equal(rgb[1],rgb[2])
    assert np.array_equal(rgb[4],[142,153,171])
    settings.reverse = True
    reverse = core.colors(values,valid,np.zeros((5,3)),[0,0,0],settings)
    assert np.array_equal(reverse[0],rgb[3])
    with pytest.raises(ValueError):
        core.DisplaySettings(tolerance=2).validate()


def test_signed_surface_spheres_and_open_boundary():
    target = o3d.geometry.TriangleMesh.create_sphere(radius=4,resolution=30)
    outside = o3d.geometry.TriangleMesh.create_sphere(radius=4.5,resolution=30)
    inside = o3d.geometry.TriangleMesh.create_sphere(radius=3.5,resolution=30)
    assert np.median(core.comparison_values(outside,target)[0])>.49
    assert np.median(core.comparison_values(inside,target)[0])<-.48
    assert core.comparison_values(outside,target)[1].all()
    triangle = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector([[0,0,0],[1,0,0],[0,1,0]]),
                                        o3d.utility.Vector3iVector([[0,1,2]]))
    assert not core.comparison_values(triangle,triangle)[1].any()


def test_demo_has_both_deviations_and_portable_restore(tmp_path):
    state,arrays,path = create_demo(tmp_path/'中文病例')
    assert arrays['values'].max()>.2 and arrays['values'].min()<-.1
    state['display'] = asdict(core.DisplaySettings(lower=-.7,upper=1.2))
    core.atomic_json(path,state)
    restored,data,_ = core.load_case(path)
    assert restored['display']['lower']==-.7
    np.testing.assert_array_equal(data['current_vertices'],arrays['current_vertices'])
    for key in ('target','current'):
        mesh,_ = core.load_mesh(state['paths'][key])
        mesh.transform(np.asarray(state['registrations'][key]['matrix']))
        # STL reload merges/reorders vertices; nearest surface comparison remains invariant.
        ref = core.unpack_mesh({'vertices':arrays[key+'_vertices'],'triangles':arrays[key+'_triangles']})
        assert np.abs(core.comparison_values(mesh,ref)[0]).max()<1e-4


def test_registration_frame_failure_and_cache(monkeypatch,tmp_path):
    state,arrays,_ = create_demo(tmp_path/'input')
    calls = []
    from auto_alignment.integration.core import RegistrationResult,RegistrationMetrics
    def fake(target,source,tf,sf,config,progress,cancel):
        key = next(k for k,p in state['paths'].items() if p==sf.path)
        calls.append(key)
        assert tf.path==state['paths']['initial']
        assert config.refinement_mode=='auto'
        return RegistrationResult(np.asarray(state['registrations'][key]['matrix']),
            'failed' if key=='current' else 'success','test',RegistrationMetrics(1,0,10,1,0,0),(),0)
    monkeypatch.setattr(core,'register_meshes',fake)
    result,data,path = core.run_case(state['paths'],tmp_path/'results')
    assert calls==['target','current']
    assert result['failed'] and not data['valid'].any()
    calls.clear()
    second,_,_ = core.run_case(state['paths'],tmp_path/'results',previous=result)
    assert calls==['current'] and second['registrations']['target']['cache_reused']
    assert path.exists()


def test_change_region_matches_designed_tooth():
    from chairside_compare.demo import demo_meshes
    meshes,center = demo_meshes()
    suggestion = core.suggest_region(meshes['initial'],meshes['target'])
    assert suggestion is not None
    assert np.linalg.norm(np.asarray(suggestion['center'])-center)<5
