"""Geometry-only measurement helpers, in millimetres and degrees."""
import numpy as np
from scipy.spatial import cKDTree
from auto_alignment.result_viewer import calculate_general_annotation


def line_angle(points, normal=None):
    points = np.asarray(points, dtype=float)
    if points.shape != (4,3) or not np.isfinite(points).all():
        raise ValueError('角度测量需要四个有效点')
    a, b = points[1]-points[0], points[3]-points[2]
    if min(np.linalg.norm(a),np.linalg.norm(b)) < 1e-6:
        raise ValueError('同一直线的两个点不能重合，请重新选点')
    if normal is not None:
        n = np.asarray(normal,dtype=float)
        if not np.isfinite(n).all() or np.linalg.norm(n)<1e-6:
            raise ValueError('截面方向无效')
        n = n/np.linalg.norm(n)
        a,b = a-n*np.dot(a,n), b-n*np.dot(b,n)
        if min(np.linalg.norm(a),np.linalg.norm(b))<1e-6:
            raise ValueError('直线在截面内的投影长度不足')
        angle = float(np.degrees(np.arctan2(np.dot(n,np.cross(a,b)),np.dot(a,b))))
        return angle,None
    cosine = float(np.clip(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)), -1,1))
    acute = float(np.degrees(np.arccos(abs(cosine))))
    return acute, 180-acute


def snap_to_segments(position, segments, camera, width, height, tolerance=8):
    """Pixel tolerance independent of zoom, snapping to visible section line segments."""
    segments = np.asarray(segments, float).reshape(-1,2,3)
    if not len(segments):
        return None
    normal = -np.asarray(camera.GetDirectionOfProjection())
    up = np.asarray(camera.GetViewUp())
    right = np.cross(up,normal)
    relative = segments-np.asarray(camera.GetFocalPoint())
    scale = max(height,1)/(2*camera.GetParallelScale())
    xy = np.stack((width/2+relative@right*scale, height/2-relative@up*scale),axis=-1)
    edge = xy[:,1]-xy[:,0]
    denom = np.sum(edge*edge,axis=1)
    t = np.clip(np.divide(np.sum((np.asarray(position)-xy[:,0])*edge,axis=1),denom,
                          out=np.zeros(len(edge)),where=denom>1e-12),0,1)
    distances = np.linalg.norm(xy[:,0]+t[:,None]*edge-position,axis=1)
    index = int(np.argmin(distances))
    if distances[index] > tolerance:
        return None
    return segments[index,0]+t[index]*(segments[index,1]-segments[index,0])


class DeviationProbe:
    def __init__(self, arrays):
        self.arrays = arrays
        vertices,faces = arrays['current_vertices'],arrays['current_triangles']
        points = vertices[faces]
        cross = np.cross(points[:,1]-points[:,0],points[:,2]-points[:,0])
        double_area = np.linalg.norm(cross,axis=1)
        self.normals = cross/np.maximum(double_area[:,None],1e-20)
        weights = np.maximum(np.bincount(faces.ravel(),weights=np.repeat(double_area/6,3),minlength=len(vertices)),1e-12)
        self.ids = np.flatnonzero(arrays['valid'] & np.isfinite(arrays['values']))
        self.points = vertices[self.ids]
        self.values = arrays['values'][self.ids]
        self.weights = weights[self.ids]
        self.tree = cKDTree(self.points) if len(self.ids) else None
        incident = np.zeros(len(vertices),dtype=np.int64)
        incident[faces.ravel()] = np.repeat(np.arange(len(faces)),3)
        self.incident = incident[self.ids]

    def measure(self, point, radius=.1, face_id=None):
        """VTK supplies an exact surface hit; query only the enclosing local ball.

        The downstream cylinder and area weighting are the unchanged general-viewer
        formula. Its nearest-sample fallback is preserved when the cylinder is empty.
        """
        if self.tree is None or not self.arrays['valid'].any():
            raise ValueError('当前结果没有可用的偏差数据')
        anchor = np.asarray(point,float)
        _,nearest = self.tree.query(anchor)
        if face_id is None:
            face_id = self.incident[nearest]
        normal = self.normals[int(face_id)]
        height = max(.15,min(radius,.75))
        ids = self.tree.query_ball_point(anchor,np.hypot(radius,height)+1e-9)
        # Include the global nearest point for the original fallback, without scanning the arch.
        ids = np.unique([*ids,int(nearest)])
        annotation = calculate_general_annotation(self.points[ids],self.values[ids],self.weights[ids],
            anchor_mm=anchor,surface_normal=normal,radius_mm=radius,annotation_id='probe')
        return {'anchor':anchor.tolist(),
                'mean_mm':annotation.mean_signed_deviation_mm,'radius_mm':radius,'samples':annotation.sample_count}
