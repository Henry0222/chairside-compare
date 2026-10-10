"""Case storage and registration in a shared, explicitly chosen reference frame."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import uuid

import numpy as np
import open3d as o3d

from auto_alignment.integration.core import AlignmentConfig, load_mesh, register_meshes
from auto_alignment.comparison import signed_point_to_mesh_distances
from auto_alignment.deviation_scale import DeviationScale
from auto_alignment.integration import GENERAL_MODEL_REGISTRATION_VERSION
from auto_alignment.refinement_modes import check_cancelled


@dataclass
class DisplaySettings:
    lower: float = -1.0
    upper: float = 1.0
    tolerance: float = 0.10
    target_opacity: float = 1.0
    current_opacity: float = 0.28
    initial_opacity: float = 0.0
    section_opacity: float = 0.3
    radius: float = 10.0
    tooth: str = '未指定'
    reverse: bool = False
    color: bool = True
    step: float = 0.05

    @classmethod
    def from_dict(cls, values):
        result = cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})
        result.validate()
        return result

    def validate(self):
        numbers = [self.lower, self.upper, self.tolerance, self.radius, self.step,
                   self.target_opacity, self.current_opacity, self.initial_opacity, self.section_opacity]
        if not np.isfinite(numbers).all():
            raise ValueError('显示参数必须为有限数值')
        if not self.lower < -self.tolerance <= 0 <= self.tolerance < self.upper:
            raise ValueError('需要：冷色下限 < -容许值，暖色上限 > 容许值')
        if self.radius <= 0 or self.step <= 0:
            raise ValueError('范围和截面步长必须大于零')
        if any(not 0 <= x <= 1 for x in [self.target_opacity, self.current_opacity, self.initial_opacity, self.section_opacity]):
            raise ValueError('透明度参数超出范围')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def diagnostic_json(value):
    """The 3.0 refinement diagnostics include NumPy arrays/scalars and unavailable scores."""
    if isinstance(value,np.ndarray):
        return diagnostic_json(value.tolist())
    if isinstance(value,np.generic):
        return diagnostic_json(value.item())
    if isinstance(value,dict):
        return {str(k):diagnostic_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [diagnostic_json(v) for v in value]
    if isinstance(value,float) and not np.isfinite(value):
        return None  # unavailable diagnostic, never interpreted as zero
    return value


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def mesh_arrays(mesh):
    return np.asarray(mesh.vertices).copy(), np.asarray(mesh.triangles).copy()


def pack_mesh(mesh):
    v, f = mesh_arrays(mesh)
    return {'vertices': v, 'triangles': f}


def unpack_mesh(data):
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(data['vertices']),
                                    o3d.utility.Vector3iVector(data['triangles']))
    mesh.compute_vertex_normals()
    mesh.compute_triangle_normals()
    return mesh


def comparison_values(current, target):
    """Mask open-boundary correspondences; never turn missing target surfaces green."""
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(target))
    points = np.asarray(current.vertices)
    hits = scene.compute_closest_points(o3d.core.Tensor(points.astype(np.float32)))
    triangles = np.asarray(target.triangles)
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_vertices = np.unique(unique[counts != 2])
    boundary_faces = np.any(np.isin(triangles, boundary_vertices), axis=1)
    valid = ~boundary_faces[hits['primitive_ids'].numpy().astype(int)]
    values = signed_point_to_mesh_distances(points, target)
    valid &= np.isfinite(values)
    return values, valid


def target_color_values(arrays):
    """Transfer current-to-target deviation onto target; retain its sign convention.

    Interpolate at the closest current triangle. Do not reverse the surface
    comparison, which would invert the meaning of excess preparation material.
    Invalid source triangles remain unevaluable on the target.
    """
    target = arrays['target_vertices']
    if not arrays['valid'].any():
        return np.zeros(len(target)), np.zeros(len(target), dtype=bool)
    current = unpack_mesh({'vertices':arrays['current_vertices'], 'triangles':arrays['current_triangles']})
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(current))
    hits = scene.compute_closest_points(o3d.core.Tensor(np.asarray(target,dtype=np.float32)))
    ids = arrays['current_triangles'][hits['primitive_ids'].numpy().astype(int)]
    uv = hits['primitive_uvs'].numpy().astype(float)
    weights = np.column_stack((1-uv.sum(axis=1),uv))
    values = np.sum(arrays['values'][ids]*weights,axis=1)
    valid = np.all(arrays['valid'][ids],axis=1) & np.isfinite(values)
    return values,valid


def colors(values, valid, vertices, center, settings):
    settings.validate()
    values = np.asarray(values) * (-1 if settings.reverse else 1)
    scale = DeviationScale(settings.lower,-settings.tolerance,settings.tolerance,
                           settings.upper,-settings.tolerance,settings.tolerance)
    rgb = scale.map_colors(values)*255
    if not settings.color:
        rgb[:] = [221, 228, 237]
    mask = np.asarray(valid).copy()
    # Tooth selection controls the section only; deviation colors cover the whole arch.
    rgb[~mask] = [142, 153, 171]
    return rgb.astype(np.uint8)


def suggest_region(initial, target):
    """Suggest changed geometry, not a semantic tooth segmentation or FDI label."""
    vertices = np.asarray(target.vertices)
    take = np.linspace(0, len(vertices)-1, min(len(vertices), 18000), dtype=int)
    values = signed_point_to_mesh_distances(vertices[take], initial)
    changed = vertices[take][np.abs(values) > .25]
    if len(changed) < 12:
        return None
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(changed))
    labels = np.asarray(cloud.cluster_dbscan(eps=1.8, min_points=8, print_progress=False))
    if not np.any(labels >= 0):
        return None
    label = np.bincount(labels[labels >= 0]).argmax()
    group = changed[labels == label]
    center = np.median(group, axis=0)
    radius = float(np.clip(np.percentile(np.linalg.norm(group-center, axis=1), 95)+1.5, 3, 12))
    return {'center': center.tolist(), 'radius': radius}


def normalize_paths(paths):
    paths = {k: str(v) for k, v in paths.items() if v}
    if len(paths) < 2:
        raise ValueError('请至少导入两个模型')
    return paths


def load_preview(paths, progress=lambda f,m: None):
    paths = {k:v for k,v in paths.items() if v}
    if not paths:
        raise ValueError('请拖入至少一个模型')
    arrays = {}
    for i,(key,path) in enumerate(paths.items()):
        progress(i/len(paths), '加载原始模型预览…')
        mesh,_ = load_mesh(path)
        arrays[key+'_vertices'], arrays[key+'_triangles'] = mesh_arrays(mesh)
    count = len(arrays.get('current_vertices',[]))
    arrays.update(values=np.zeros(count),valid=np.zeros(count,dtype=bool))
    return arrays


def snapshot_paths(paths,destination):
    """Keep each imported round independent of later overwrites by the scanner."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    result = {}
    for key,path in paths.items():
        source = Path(path).resolve()
        if source.is_relative_to(destination):
            result[key] = str(source)
            continue
        folder = destination/uuid.uuid4().hex
        folder.mkdir()
        target = folder/source.name
        before = file_hash(source)
        shutil.copyfile(source,target)
        if before != file_hash(source) or before != file_hash(target):
            raise ValueError(f'{source.name} 正在写入，请等待口扫导出完成后重新导入。')
        result[key] = str(target)
    return result


def run_case(paths, destination, progress=lambda f, m: None, cancel=lambda: False, previous=None,
             reference=None, comparison=None):
    """Global 3.0 auto route; every model independently aligns to one reference."""
    if GENERAL_MODEL_REGISTRATION_VERSION != '3.0.0':
        raise RuntimeError('需要配准核心 3.0.0')
    paths = normalize_paths(paths)
    reference = reference or ('initial' if 'initial' in paths else next(iter(paths)))
    if reference not in paths:
        raise ValueError('参考模型不在导入列表中')
    color_reference = 'target' if comparison is None and 'target' in paths else reference
    comparison = comparison or ('current' if 'current' in paths else next(k for k in paths if k != reference))
    if comparison not in paths or comparison == color_reference:
        raise ValueError('请选择两个不同的模型进行比较')
    config = AlignmentConfig(refinement_mode='auto')
    meshes, facts, hashes = {}, {}, {}
    for key in paths:
        check_cancelled(cancel)
        progress(0, f'读取 {key} 模型…')
        before = file_hash(paths[key])
        meshes[key], facts[key] = load_mesh(paths[key])
        hashes[key] = file_hash(paths[key])
        if before != hashes[key]:
            raise ValueError('读取期间模型仍在写入，请等待导出完成后再试')
    aligned = {reference: meshes[reference]}
    registrations = {}
    keys = [key for key in paths if key != reference]
    for index, key in enumerate(keys):
        cached = (previous and previous.get('reference') == reference and
                  previous.get('hashes', {}).get(reference) == hashes[reference] and
                  previous.get('hashes', {}).get(key) == hashes[key] and
                  previous.get('core_version') == GENERAL_MODEL_REGISTRATION_VERSION and
                  previous.get('registrations', {}).get(key, {}).get('status') in ('success', 'warning'))
        if hashes[key] == hashes[reference]:
            record = {'matrix': np.eye(4).tolist(), 'status': 'success', 'confidence': '相同模型',
                      'warnings': [], 'metrics': {}, 'seconds': 0., 'identical_input': True}
        elif cached:
            record = copy.deepcopy(previous['registrations'][key])
            record['cache_reused'] = True
        else:
            result = register_meshes(meshes[reference], meshes[key], facts[reference], facts[key], config,
                lambda f, m, i=index, k=key: progress((i+f)/len(keys), f'{Path(paths[k]).name} · {m}'), cancel=cancel)
            record = {'matrix': result.transformation.tolist(), 'status': result.status,
                      'confidence': result.confidence, 'warnings': list(result.warnings),
                      'metrics': diagnostic_json(result.metrics.as_dict()), 'seconds': result.elapsed_seconds}
        registrations[key] = record
        aligned[key] = copy.deepcopy(meshes[key]).transform(np.asarray(record['matrix']))
    failed = any(x['status'] == 'failed' for x in registrations.values())
    check_cancelled(cancel)
    progress(.97, '计算偏差与变化区…')
    values, valid = comparison_values(aligned[comparison], aligned[color_reference])
    if failed:
        valid[:] = False
    region = None if failed else (suggest_region(aligned['initial'], aligned['target']) if 'initial' in aligned and 'target' in aligned else None)
    state = {'schema': 1, 'core_version': GENERAL_MODEL_REGISTRATION_VERSION,
             'reference': reference,
             'pair': [color_reference, comparison],
             'geometry_pair': [color_reference, comparison],
             'created': datetime.now().astimezone().isoformat(), 'paths': {k: str(Path(v).resolve()) for k,v in paths.items()},
             'hashes': hashes, 'registrations': registrations, 'failed': failed,
             'region_suggestion': region, 'mesh_warnings': {k: list(v.warnings) for k,v in facts.items()}}
    arrays = {}
    for key, mesh in aligned.items():
        v, f = mesh_arrays(mesh)
        arrays[key+'_vertices'] = v
        arrays[key+'_triangles'] = f
    arrays.update(values=values, valid=valid)
    check_cancelled(cancel)
    # Immutable runs retain every scan; a failed/cancelled job never replaces a saved case.
    folder = Path(destination) / ('scan_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(folder/'geometry.npz', **arrays)
    atomic_json(folder/'case.json', state)
    progress(1, '完成')
    return state, arrays, folder/'case.json'


def pair_arrays(arrays, reference, comparison, *, calculate=False, failed=False):
    """Canonical aliases are local to the chosen pair; model IDs stay stable."""
    pair = {role+'_'+field: arrays[key+'_'+field]
            for role,key in [('target',reference),('current',comparison)]
            for field in ('vertices','triangles')}
    if calculate:
        pair['values'], pair['valid'] = comparison_values(
            unpack_mesh({'vertices':pair['current_vertices'],'triangles':pair['current_triangles']}),
            unpack_mesh({'vertices':pair['target_vertices'],'triangles':pair['target_triangles']}))
    else:
        pair['values'], pair['valid'] = arrays['values'], arrays['valid']
    if failed:
        pair['valid'] = np.zeros(len(pair['values']), dtype=bool)
    return pair


def load_case(path):
    path = Path(path)
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('schema') != 1:
        raise ValueError('不支持的病例版本')
    with np.load(path.with_name('geometry.npz'), allow_pickle=False) as archive:
        arrays = {k: archive[k].copy() for k in archive.files}
    pair = state.get('pair', ['target','current'])
    if pair != state.get('geometry_pair', ['target','current']):
        data = pair_arrays(arrays,*pair,calculate=True,failed=state.get('failed',False))
        arrays.update(values=data['values'],valid=data['valid'])
    return state, arrays, path
