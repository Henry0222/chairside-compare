"""Run the provided real-case pair through the two-model route without overwriting its prior result."""
from pathlib import Path
import json
import sys
import copy
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run
from chairside_compare.core import load_case,run_case,atomic_json

source = Path(sys.argv[1])
if source.is_dir():
    source = source/'case.json'
old,_,_ = load_case(source)
paths = {k:old['paths'][k] for k in ('target','current')}
state,arrays,path = run_case(paths,ROOT/'cases',lambda f,m:print(f'{f:.0%} {m}',flush=True))
refinement = state['registrations']['current']['metrics'].get('refinement')
assert state['reference']=='target' and 'initial_vertices' not in arrays
assert not refinement or refinement.get('reason')!='refinement_error',refinement
# The supplied old case used the same target as its initial reference. Preserve
# its chosen viewing region only when that coordinate equivalence is verified.
if (old.get('hashes',{}).get('initial')==state['hashes']['target'] and
        np.allclose(old.get('registrations',{}).get('target',{}).get('matrix',np.eye(4)),np.eye(4))):
    if 'display' in old:
        state['display'] = copy.deepcopy(old['display'])
    if 'view' in old:
        state['view'] = {k:copy.deepcopy(v) for k,v in old['view'].items() if k not in ('probes','measurements')}
    atomic_json(path,state)
report = {'source_case':str(source),'new_case':str(path),'status':state['registrations']['current']['status'],
          'seconds':state['registrations']['current']['seconds'],'refinement':refinement}
atomic_json(ROOT/'verification'/'two_model_report.json',report)
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
