"""Synthetic full-arch geometry. No patient data; known poses clearly labelled."""
from datetime import datetime
from pathlib import Path
import copy
import numpy as np
import open3d as o3d
from auto_alignment.exporters import _write_triangle_mesh
from .core import atomic_json, file_hash, comparison_values


def demo_meshes():
    meshes = {key:o3d.geometry.TriangleMesh() for key in ('initial','target','current')}
    center = None
    for index,angle in enumerate(np.linspace(.12,np.pi-.12,12)):
        location = np.array([25*np.cos(angle), 30*np.sin(angle), 0.])
        for key,arch in meshes.items():
            mesh = o3d.geometry.TriangleMesh.create_sphere(resolution=26)
            points = np.asarray(mesh.vertices).copy()
            size = np.array([3.6 if index in (0,1,2,9,10,11) else 2.5, 3.4, 4.8])
            # Rounded occlusal table with shallow cusp relief.
            points[:,2] = np.sign(points[:,2])*np.abs(points[:,2])**.55
            points[:,2] += .07*np.sin(points[:,0]*5)*np.cos(points[:,1]*5)*np.maximum(points[:,2],0)
            points *= size
            if index == 2:
                center = location.copy()
                above = np.clip((points[:,2]+2)/4,0,1)
                if key == 'target':
                    points[:,0] *= 1-.25*above
                    points[:,1] *= 1-.25*above
                    points[:,2] -= .9*above
                elif key == 'current':
                    # Most surface still outside target; one side beyond target.
                    factor = .13+.23*(points[:,0]<-.5)
                    points[:,0] *= 1-factor*above
                    points[:,1] *= 1-.15*above
                    points[:,2] -= (.55+.65*(points[:,0]<-1))*above
            rotation = np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
            mesh.vertices = o3d.utility.Vector3dVector(points@rotation.T+location)
            arch += mesh
    for mesh in meshes.values():
        mesh.compute_vertex_normals()
        mesh.compute_triangle_normals()
    return meshes,center


def create_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    meshes,center = demo_meshes()
    paths,hashes,registrations,arrays = {},{},{},{}
    for index,(key,mesh) in enumerate(meshes.items()):
        transform = np.eye(4)
        if key != 'initial':
            angle = .18*index
            transform[:3,:3] = mesh.get_rotation_matrix_from_xyz((.03*index,-.06*index,angle))
            transform[:3,3] = [index*8,-index*4,index*2]
        exported = copy.deepcopy(mesh).transform(transform)
        path = folder/(key+'.stl')
        if not _write_triangle_mesh(path,exported):
            raise RuntimeError('演示模型写入失败')
        paths[key],hashes[key] = str(path.resolve()),file_hash(path)
        if key != 'initial':
            registrations[key] = {'matrix':np.linalg.inv(transform).tolist(),'status':'success',
                                   'confidence':'演示已知变换','warnings':[], 'metrics':{},'seconds':0}
        arrays[key+'_vertices'] = np.asarray(mesh.vertices).copy()
        arrays[key+'_triangles'] = np.asarray(mesh.triangles).copy()
    values,valid = comparison_values(meshes['current'],meshes['target'])
    arrays.update(values=values,valid=valid)
    state = {'schema':1,'core_version':'demo-known-transform','created':datetime.now().astimezone().isoformat(),
             'paths':paths,'hashes':hashes,'registrations':registrations,'failed':False,'demo':True,
             'region_suggestion':{'center':center.tolist(),'radius':6.2}}
    np.savez_compressed(folder/'geometry.npz',**arrays)
    atomic_json(folder/'case.json',state)
    return state,arrays,folder/'case.json'
