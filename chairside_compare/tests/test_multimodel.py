import numpy as np
from chairside_compare import core
from chairside_compare.demo import create_demo


def test_many_models_align_independently_and_cache_by_reference(monkeypatch,tmp_path):
    state,_,_ = create_demo(tmp_path/'input')
    paths = {'round_a':state['paths']['target'], 'round_b':state['paths']['current'],
             'round_c':state['paths']['initial'], 'round_d':state['paths']['target']}
    from auto_alignment.integration.core import RegistrationResult,RegistrationMetrics
    calls = []
    def register(target,source,tf,sf,config,progress,cancel):
        calls.append((tf.path,sf.path))
        return RegistrationResult(np.eye(4),'success','test',RegistrationMetrics(1,0,10,1,0,0),(),0.)
    monkeypatch.setattr(core,'register_meshes',register)
    result,arrays,path = core.run_case(paths,tmp_path/'runs',reference='round_c',comparison='round_b')
    assert list(result['paths'])==list(paths)
    assert len(calls)==3 and all(t==paths['round_c'] for t,s in calls)
    assert result['pair']==['round_c','round_b']
    assert all(k+'_vertices' in arrays for k in paths)
    preview = core.load_preview(paths)
    assert all(k+'_triangles' in preview for k in paths)
    calls.clear()
    core.run_case(paths,tmp_path/'runs',previous=result,reference='round_c',comparison='round_a')
    assert not calls
    # A different registration reference invalidates all previous transforms.
    core.run_case(paths,tmp_path/'runs',previous=result,reference='round_b',comparison='round_c')
    assert len(calls)==3 and all(t==paths['round_b'] for t,s in calls)
    # Save a different displayed pair without changing the immutable geometry.
    expected = core.pair_arrays(arrays,'round_b','round_a',calculate=True)
    result['pair'] = ['round_b','round_a']
    core.atomic_json(path,result)
    restored,loaded,_ = core.load_case(path)
    assert restored['geometry_pair']==['round_c','round_b']
    np.testing.assert_allclose(loaded['values'],expected['values'])
    np.testing.assert_array_equal(loaded['valid'],expected['valid'])


def test_failed_pair_never_becomes_evaluable(tmp_path):
    _,arrays,_ = create_demo(tmp_path/'input')
    pair = core.pair_arrays(arrays,'current','target',calculate=True,failed=True)
    assert not pair['valid'].any()
    assert len(pair['values'])==len(arrays['target_vertices'])


def test_import_snapshot_survives_same_filename_overwrite(tmp_path):
    source = tmp_path/'scan.stl'
    source.write_bytes(b'first round')
    first = core.snapshot_paths({'round_1':str(source)},tmp_path/'imports')
    source.write_bytes(b'second round')
    later = core.snapshot_paths({**first,'round_2':str(source)},tmp_path/'imports')
    from pathlib import Path
    assert first['round_1']==later['round_1']
    assert Path(later['round_1']).read_bytes()==b'first round'
    assert Path(later['round_2']).read_bytes()==b'second round'
    assert Path(later['round_2']).name=='scan.stl'
