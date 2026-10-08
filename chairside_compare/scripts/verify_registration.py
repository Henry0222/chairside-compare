from pathlib import Path
import sys
import json
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run
from chairside_compare.demo import create_demo
from chairside_compare.core import run_case,atomic_json

state,arrays,_ = create_demo(ROOT/'verification'/'input')
last = ['']
def progress(f,m):
    if m!=last[0]:
        print(f'{f:.0%} {m}',flush=True)
        last[0]=m

result,data,path = run_case(state['paths'],ROOT/'verification'/'runs',progress)
errors = {}
for key in ('target','current'):
    actual = np.asarray(result['registrations'][key]['matrix'])
    expected = np.asarray(state['registrations'][key]['matrix'])
    delta = actual@np.linalg.inv(expected)
    angle = np.degrees(np.arccos(np.clip((np.trace(delta[:3,:3])-1)/2,-1,1)))
    points = arrays[key+'_vertices']
    displacement = points@delta[:3,:3].T+delta[:3,3]-points
    errors[key] = {'angle_degrees':float(angle),'maximum_displacement_mm':float(np.linalg.norm(displacement,axis=1).max()),
                   'status':result['registrations'][key]['status']}
atomic_json(ROOT/'verification'/'registration_report.json',{'errors':errors,'case':str(path)})
print(json.dumps(errors,indent=2),flush=True)
assert not result['failed']
assert all(x['maximum_displacement_mm']<.15 for x in errors.values())
print('Real 3.0.0 integration passed.',flush=True)
