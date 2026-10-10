"""Four-model end-to-end registration on synthetic full-arch surfaces."""
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run
from chairside_compare.demo import create_demo
from chairside_compare.core import run_case,load_case,atomic_json,pair_arrays

output = ROOT/'verification'/'registration_v07'
state,_,_ = create_demo(output/'input')
paths = {'baseline':state['paths']['initial'],'design':state['paths']['target'],
         'round_1':state['paths']['current'],'round_2':state['paths']['current']}
result,arrays,path = run_case(paths,output/'runs',reference='baseline',comparison='round_2')
assert list(result['paths'])==list(paths)
assert set(result['registrations'])=={'design','round_1','round_2'}
assert all(np.isfinite(record['matrix']).all() for record in result['registrations'].values())
result['pair'] = ['design','round_1']
atomic_json(path,result)
_,loaded,_ = load_case(path)
expected = pair_arrays(arrays,'design','round_1',calculate=True,failed=result['failed'])
np.testing.assert_allclose(loaded['values'],expected['values'])
atomic_json(output/'result.json',{'completed':True,'models':4,'case':str(path),
            'statuses':{k:v['status'] for k,v in result['registrations'].items()},
            'pair_switch_reload':True,'clinical_accuracy_validated':False})
print('Four-model real registration pipeline completed; see result.json for statuses.')
