"""Bounded, native-pixel SEM centerlines, not bounding-box connectivity.

Threshold segmentation has no closing/gap bridging. Skeleton topology is returned
as maximal chains split at branches. Paths remain unverified image evidence.
"""
import math
import cv2
import numpy as np
try:
    from .roi_reader import read_gray_roi
    from . import analysis_store as store
except ImportError:
    from roi_reader import read_gray_roi
    import analysis_store as store

MAX_PIXELS = 2_000_000
MAX_VERTICES = 20_000


def thin_mask(mask, max_iterations=512):
    """Zhang-Suen thinning with explicit zero padding; reject incomplete thinning."""
    b=np.pad(mask.astype(np.uint8),1)
    for iteration in range(max_iterations):
        changed=False
        for phase in (0,1):
            c=b[1:-1,1:-1]
            p=[b[:-2,1:-1],b[:-2,2:],b[1:-1,2:],b[2:,2:],
               b[2:,1:-1],b[2:,:-2],b[1:-1,:-2],b[:-2,:-2]]
            neighbors=sum(p);transitions=sum(((p[i]==0)&(p[(i+1)%8]>0)).astype(np.uint8) for i in range(8))
            remove=(c>0)&(neighbors>=2)&(neighbors<=6)&(transitions==1)
            if phase==0:remove&=(p[0]*p[2]*p[4]==0)&(p[2]*p[4]*p[6]==0)
            else:remove&=(p[0]*p[2]*p[6]==0)&(p[0]*p[4]*p[6]==0)
            if remove.any():c[remove]=0;changed=True
        if not changed:return b[1:-1,1:-1].astype(bool)
    raise ValueError('Thinning did not converge; reduce ROI or isolate thin conductors')


def skeleton_chains(skeleton):
    """Every undirected skeleton edge is visited once, including isolated cycles.
    Suppress redundant diagonal shortcuts when an axial route already exists.
    """
    yy,xx=np.where(skeleton);pixels=set(zip(xx.tolist(),yy.tolist()))
    if len(pixels)>100_000:raise ValueError('Skeleton too dense; reduce the tracing ROI')
    adjacent={}
    for x,y in pixels:
        ns=[]
        for dx,dy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
            q=(x+dx,y+dy)
            if q not in pixels:continue
            if dx and dy and ((x+dx,y) in pixels or (x,y+dy) in pixels):continue
            ns.append(q)
        adjacent[(x,y)]=sorted(ns)
    visited=set();paths=[]
    def edge(a,b):return (a,b) if a<b else (b,a)
    def walk(start,following):
        chain=[start];current=following;previous=start;visited.add(edge(start,following))
        while True:
            chain.append(current)
            if current==start or len(adjacent[current])!=2:break
            nxt=next(q for q in adjacent[current] if q!=previous)
            if edge(current,nxt) in visited:break
            visited.add(edge(current,nxt));previous,current=current,nxt
        return chain
    for p in sorted(pixels):
        if len(adjacent[p])==2:continue
        for q in adjacent[p]:
            if edge(p,q) not in visited:paths.append(walk(p,q))
    for p in sorted(pixels):
        for q in adjacent[p]:
            if edge(p,q) not in visited:paths.append(walk(p,q))
    return paths,adjacent


def extract_paths(image_path, roi, threshold=0, min_area=20, invert=False):
    if len(roi)!=4 or any(not isinstance(v,int) or isinstance(v,bool) for v in roi):
        raise ValueError('ROI requires four integer source pixel coordinates/dimensions')
    x,y,w,h=roi
    if x<0 or y<0 or w<1 or h<1 or w*h>MAX_PIXELS:
        raise ValueError(f'Tracing ROI must be positive and at most {MAX_PIXELS} native pixels')
    if not isinstance(threshold,int) or not 0<=threshold<=255 or not isinstance(min_area,int) or min_area<1:
        raise ValueError('Threshold must be 0..255 and min_area positive')
    gray=read_gray_roi(image_path,roi,max_pixels=MAX_PIXELS)
    mode=cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    used,mask=cv2.threshold(gray,threshold,255,mode|(cv2.THRESH_OTSU if threshold==0 else 0))
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
    keep=np.zeros(count,bool);keep[1:]=stats[1:,cv2.CC_STAT_AREA]>=min_area
    skeleton=thin_mask(keep[labels]);chains,graph=skeleton_chains(skeleton)
    if sum(len(c) for c in chains)>MAX_VERTICES or len(chains)>1000:
        raise ValueError('Too many trace vertices/branches; reduce the ROI or increase min_area')
    result=[]
    def kind(p):
        return 'junction' if len(graph[p])>2 else 'endpoint' if len(graph[p])==1 else 'loop'
    for chain in chains:
        if len(chain)<2:continue
        px,py=chain[0];component=int(labels[py,px]);bx,by,bw,bh,area=map(int,stats[component])
        clipped=bx==0 or by==0 or bx+bw==w or by+bh==h
        points=[[x+px+.5,y+py+.5] for px,py in chain]
        result.append(dict(points=points,closed=chain[0]==chain[-1],component=component,
                           start_kind=kind(chain[0]),end_kind=kind(chain[-1]),
                           component_touches_roi_border=clipped,
                           length_px=sum(math.hypot(a[0]-b[0],a[1]-b[1]) for a,b in zip(points,points[1:]))))
    return dict(roi=list(roi),threshold=float(used),min_area=min_area,invert=bool(invert),
                coordinate_convention='original source pixel centers; no resampling',
                method='threshold / small-component filter / Zhang-Suen / maximal skeleton chains',
                gap_bridging=False,electrically_verified=False,paths=result,
                junctions=[[x+px+.5,y+py+.5] for (px,py),ns in sorted(graph.items()) if len(ns)>2],
                isolated_pixels=sum(not ns for ns in graph.values()),
                note='A threshold bridge, crossing or branch remains ambiguous. ROI-clipped components do not establish physical wire endpoints.')


def save_paths(db, layer, extraction, actor='MCP'):
    """One atomic batch; each saved path remains individually reviewable/undoable."""
    db.execute('SAVEPOINT trace_path_batch')
    try:
        records=[]
        for i,path in enumerate(extraction['paths']):
            provenance={k:v for k,v in extraction.items() if k not in ('paths','junctions')}
            provenance.update({k:v for k,v in path.items() if k!='points'})
            r=store.save_evidence(db,'trace',[dict(layer=layer,points=path['points'])],
                label=f'Centerline {i+1}',note='Automatic image centerline; inspect segmentation and junctions before review.',
                actor=actor,provenance=provenance)
            records.append(dict(id=r['id'],revision=r['revision'],vertices=len(path['points'])))
        db.execute('RELEASE SAVEPOINT trace_path_batch')
        return records
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT trace_path_batch');db.execute('RELEASE SAVEPOINT trace_path_batch')
        raise
