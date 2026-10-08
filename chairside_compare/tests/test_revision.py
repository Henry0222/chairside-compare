from dataclasses import asdict
import numpy as np
import pytest
from chairside_compare import core
from chairside_compare.demo import create_demo
from chairside_compare.measurements import line_angle, DeviationProbe


def test_signed_angles_and_diagnostic_missing_values():
    for degrees in (-3.,3.,-90.,90.):
        theta = np.deg2rad(degrees)
        points = [[0,0,0],[1,0,0],[0,0,0],[np.cos(theta),np.sin(theta),0]]
        assert line_angle(points,[0,0,1])[0]==pytest.approx(degrees)
        assert line_angle(points,[0,0,-1])[0]==pytest.approx(-degrees)
    from chairside_compare.diagnostics import diagnostic_summary
    report = diagnostic_summary({'registrations':{'current':{'status':'success','metrics':{'overlap_ratio':.9,'inlier_rmse_mm':.123}}}})
    assert '90.0%' in report and '0.123 mm' in report and '未记录' in report


def test_target_color_transfer_preserves_sign_interpolation_and_invalid_mask():
    arrays = dict(current_vertices=np.array([[0.,0,1],[2,0,1],[0,2,1]]),
                  current_triangles=np.array([[0,1,2]]),
                  target_vertices=np.array([[.5,.5,0],[0,0,0]]),
                  values=np.array([1.,2,3]),valid=np.ones(3,dtype=bool))
    values,valid = core.target_color_values(arrays)
    np.testing.assert_allclose(values,[1.75,1])
    assert valid.all()  # positive remains positive on target, not inverted
    arrays['valid'][2] = False
    assert not core.target_color_values(arrays)[1].any()
    settings = core.DisplaySettings()
    assert settings.radius==10 and settings.section_opacity==.3
    with pytest.raises(ValueError):
        core.DisplaySettings(section_opacity=1.1).validate()


def test_two_models_use_target_frame_and_no_initial_required(monkeypatch,tmp_path):
    state,_,_ = create_demo(tmp_path/'input')
    paths = {k:state['paths'][k] for k in ('target','current')}
    from auto_alignment.integration.core import RegistrationResult,RegistrationMetrics
    expected = np.linalg.inv(state['registrations']['target']['matrix']) @ state['registrations']['current']['matrix']
    calls = []
    def register(target,source,tf,sf,config,progress,cancel):
        calls.append((tf.path,sf.path))
        return RegistrationResult(expected,'success','test',RegistrationMetrics(1,0,10,1,0,0),(),0.)
    monkeypatch.setattr(core,'register_meshes',register)
    result,arrays,_ = core.run_case(paths,tmp_path/'results')
    assert calls==[(paths['target'],paths['current'])]
    assert result['reference']=='target'
    assert 'initial' not in result['paths'] and 'initial_vertices' not in arrays
    np.testing.assert_allclose(result['registrations']['current']['matrix'],expected)
    preview = core.load_preview(paths)
    assert not preview['valid'].any()
    original,_ = core.load_mesh(paths['current'])
    np.testing.assert_array_equal(preview['current_vertices'],np.asarray(original.vertices))


def test_refinement_diagnostics_are_json_portable(tmp_path):
    data = {'matrix':np.eye(4),'selected':np.bool_(True),'score':np.float64(np.inf),'items':[np.int64(2)]}
    safe = core.diagnostic_json(data)
    core.atomic_json(tmp_path/'diagnostics.json',safe)
    assert safe['selected'] is True and safe['score'] is None
    assert safe['matrix'][0]==[1.,0.,0.,0.]


def test_identical_initial_target_skips_duplicate_registration(monkeypatch,tmp_path):
    state,_,_ = create_demo(tmp_path/'input')
    from auto_alignment.integration.core import RegistrationResult,RegistrationMetrics
    paths = {**state['paths'],'initial':state['paths']['target']}
    calls=[]
    def register(target,source,tf,sf,config,progress,cancel):
        calls.append(sf.path)
        return RegistrationResult(np.eye(4),'success','test',RegistrationMetrics(1,0,10,1,0,0),(),0.)
    monkeypatch.setattr(core,'register_meshes',register)
    result,_,_ = core.run_case(paths,tmp_path/'results')
    assert calls==[paths['current']]
    assert result['registrations']['target']['identical_input']


def test_angles_parallel_perpendicular_oblique_and_degenerate():
    assert line_angle([[0,0,0],[1,0,0],[2,2,0],[4,2,0]])==(0,180)
    assert line_angle([[0,0,0],[1,0,0],[2,2,0],[2,3,0]])==(90,90)
    a,b = line_angle([[0,0,0],[1,0,0],[2,2,0],[3,3,0]])
    assert a==pytest.approx(45) and b==pytest.approx(135)
    with pytest.raises(ValueError):
        line_angle([[0,0,0],[0,0,0],[1,0,0],[2,0,0]])


def test_indexed_probe_matches_full_general_local_mean(tmp_path):
    from auto_alignment.result_viewer import calculate_general_annotation
    _,arrays,_ = create_demo(tmp_path/'input')
    probe = DeviationProbe(arrays)
    index = int(np.argmax(arrays['values']))
    record = probe.measure(arrays['current_vertices'][index],.4)
    assert 'point_mm' not in record
    assert record['mean_mm']>0 and record['samples']>0
    for face in (5,210,11000,17000):
        point = arrays['current_vertices'][arrays['current_triangles'][face]].mean(axis=0)
        for radius in (.1,.4,1.2):
            fast = probe.measure(point,radius,face)
            full = calculate_general_annotation(probe.points,probe.values,probe.weights,
                anchor_mm=point,surface_normal=probe.normals[face],radius_mm=radius,annotation_id='test')
            assert fast['mean_mm']==pytest.approx(full.mean_signed_deviation_mm,abs=1e-12)
            assert fast['samples']==full.sample_count
    arrays['valid'][:]=False
    with pytest.raises(ValueError):
        probe.measure(arrays['current_vertices'][index])


def test_full_arch_color_ignores_selected_tooth_and_preview_accepts_one(tmp_path):
    settings = core.DisplaySettings(radius=2)
    values = np.array([-.2,0,.4])
    vertices = np.array([[0,0,0],[10,0,0],[50,0,0]])
    rgb = core.colors(values,np.ones(3,bool),vertices,[0,0,0],settings)
    np.testing.assert_array_equal(rgb,core.colors(values,np.ones(3,bool),vertices,None,settings))
    state,_,_ = create_demo(tmp_path/'input')
    preview = core.load_preview({'target':state['paths']['target']})
    assert 'target_vertices' in preview and 'current_vertices' not in preview
