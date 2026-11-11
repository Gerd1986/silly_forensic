"""Translation-only registration for neighboring SEM tiles; no image warping."""
from pathlib import Path
import numpy as np
from PIL import Image

def _ncc(a, b):
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    a = a - a.mean()
    b = b - b.mean()
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / norm) if norm > 1e-6 else -1.0

def _valid_ncc(search, template):
    """FFT correlation normalized by local window variance."""
    search = np.asarray(search, dtype=np.float64)
    template = np.asarray(template, dtype=np.float64)
    h, w = template.shape
    sh, sw = search.shape
    template = template - template.mean()
    energy = float(np.sum(template * template))
    if energy < 1e-8:
        raise ValueError("Tile overlap has insufficient texture")
    shape = (sh + h - 1, sw + w - 1)
    corr = np.fft.irfft2(np.fft.rfft2(search, s=shape) *
                         np.fft.rfft2(template[::-1, ::-1], s=shape), s=shape)
    corr = corr[h-1:sh, w-1:sw]
    def sums(values):
        integral = np.pad(values, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]
    variance = np.maximum(sums(search * search) - sums(search)**2 / (h*w), 0)
    denom = np.sqrt(variance * energy)
    return np.divide(corr, denom, out=np.full_like(corr, -1), where=denom > 1e-8)

def _overlap_score(a, b, dx, dy, max_side=384):
    h, w = a.shape
    x0, y0 = max(4, dx+4), max(4, dy+4)
    x1, y1 = min(w-4, dx+w-4), min(h-4, dy+h-4)
    if x1-x0 < 4 or y1-y0 < 4:
        return -1.0
    step = max(1, int(max(x1-x0, y1-y0) / max_side))
    return _ncc(a[y0:y1:step, x0:x1:step],
                b[y0-dy:y1-dy:step, x0-dx:x1-dx:step])

def _candidate_peaks(scores, radius, min_score):
    """Distinct maxima; candidate count alone is never an alignment failure."""
    padded = np.pad(scores, 1, constant_values=-np.inf)
    maxima = np.ones(scores.shape, dtype=bool)
    for yy in range(3):
        for xx in range(3):
            if (yy, xx) != (1, 1):
                maxima &= scores >= padded[yy:yy+scores.shape[0], xx:xx+scores.shape[1]]
    remaining = np.where(maxima & (scores >= min_score), scores, -np.inf)
    candidates = []
    while np.isfinite(remaining).any():
        iy, ix = np.unravel_index(np.argmax(remaining), remaining.shape)
        candidates.append((int(iy), int(ix), float(scores[iy, ix])))
        remaining[max(0, iy-radius):iy+radius+1,
                  max(0, ix-radius):ix+radius+1] = -np.inf
    return candidates

def _whole_overlap_scores(a, b, dxs, dys):
    """Normalized correlation of the complete shared area at every lag."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = a-a.mean()
    b = b-b.mean()
    h,w=a.shape
    shape=(2*h-1,2*w-1)
    cross=np.fft.irfft2(np.fft.rfft2(a,s=shape) *
                        np.conj(np.fft.rfft2(b,s=shape)),s=shape)
    dx=np.asarray(dxs)[None,:]
    dy=np.asarray(dys)[:,None]
    ay0=np.maximum(dy,0)
    ay1=h+np.minimum(dy,0)
    by0=np.maximum(-dy,0)
    by1=h-np.maximum(dy,0)
    count=(w-dx)*(h-np.abs(dy))
    def rect(values,y0,y1,x0,x1):
        integral=np.pad(values,((1,0),(1,0))).cumsum(0).cumsum(1)
        return integral[y1,x1]-integral[y0,x1]-integral[y1,x0]+integral[y0,x0]
    sa=rect(a,ay0,ay1,dx,w)
    sb=rect(b,by0,by1,0,w-dx)
    va=np.maximum(rect(a*a,ay0,ay1,dx,w)-sa*sa/count,0)
    vb=np.maximum(rect(b*b,by0,by1,0,w-dx)-sb*sb/count,0)
    numerator=cross[np.mod(dy,shape[0]),dx]-sa*sb/count
    denominator=np.sqrt(va*vb)
    return np.divide(numerator,denominator,out=np.full_like(numerator,-1),
                     where=denominator>1e-8)

def _detail(image):
    # Remove broad illumination gradients while retaining silicon features.
    from PIL import ImageFilter
    return image.astype(np.float32)-np.asarray(
        Image.fromarray(image).filter(ImageFilter.BoxBlur(3)),dtype=np.float32)

def _position_hint(first,second,axis):
    """Use only calibrated standard TIFF positions, never guessed vendor units."""
    records=[]
    for path in (first,second):
        with Image.open(path) as im:
            tags=getattr(im,"tag_v2",{})
            if not all(key in tags for key in (286,287,282,283,296)):
                return None
            records.append(tuple(float(tags[key]) for key in (286,287,282,283,296)))
    a,b=records
    if a[4] not in (2,3) or b[4]!=a[4] or not np.allclose(a[2:4],b[2:4]):
        return None
    dx,dy=(b[0]-a[0])*a[2],(b[1]-a[1])*a[3]
    principal=dx if axis=="horizontal" else dy
    if not np.isfinite([dx,dy]).all() or principal<=0:
        return None
    return (dx,dy)

def estimate_translation(first,second,axis,min_score=.72,hint=None,return_candidates=False):
    """Whole-overlap translation search with optional calibrated origin hint."""
    if axis not in ("horizontal","vertical"):
        raise ValueError("Unknown neighbor axis")
    with Image.open(first) as image:
        a=np.asarray(image.convert("L"))
    with Image.open(second) as image:
        b=np.asarray(image.convert("L"))
    if a.shape!=b.shape or min(a.shape)<32:
        raise ValueError("Registration requires equal tiles at least 32 pixels wide and high")
    hint_source="acquisition_overlap" if hint is not None else None
    if hint is None:
        hint=_position_hint(first,second,axis)
        if hint is not None:
            hint_source="standard_tiff_position"
    if axis=="vertical":
        a,b=a.T,b.T
        if hint is not None:
            hint=(hint[1],hint[0])
    h,w=a.shape
    scale=max(1,int(np.ceil(max(h,w)/768)))
    size=(int(round(w/scale)),int(round(h/scale)))
    aa=np.asarray(Image.fromarray(a).resize(size,Image.Resampling.BOX))
    bb=np.asarray(Image.fromarray(b).resize(size,Image.Resampling.BOX))
    hh,ww=aa.shape
    dxs=np.arange(max(1,int(ww*.50)),min(ww,int(ww*.95)+1))
    dys=np.arange(-max(2,int(hh*.08)),max(2,int(hh*.08))+1)
    if hint is not None:
        hx,hy=hint
        if not (0<hx<w and abs(hy)<h):
            raise ValueError("Acquisition position does not imply a valid neighbor overlap")
        tolerance=max(2,scale)
        dxs=np.arange(max(1,int(np.floor((hx-tolerance)*ww/w))),
                      min(ww,int(np.ceil((hx+tolerance)*ww/w))+1))
        dys=np.arange(max(1-hh,int(np.floor((hy-tolerance)*hh/h))),
                      min(hh-1,int(np.ceil((hy+tolerance)*hh/h)))+1)
    raw=_whole_overlap_scores(aa,bb,dxs,dys)
    da,db=_detail(aa),_detail(bb)
    scores=.35*raw+.65*_whole_overlap_scores(da,db,dxs,dys)
    if np.max(scores)<min_score:
        raise ValueError(f"Weak {axis} overlap: {Path(first).name} -> {Path(second).name} "
                         f"(whole-area feature NCC {float(scores.max()):.3f})")
    peaks=_candidate_peaks(scores,max(2,int(ww*.003)),min_score)
    # The entire overlap has already been scored for every coarse lag. Only
    # leading distinct maxima need the expensive original-resolution refinement.
    detail_a,detail_b=_detail(a),_detail(b)
    validated=[]
    for iy,ix,coarse_score in peaks[:32]:
        dx=int(round(dxs[ix]*w/ww))
        dy=int(round(dys[iy]*h/hh))
        best=(-2.,dx,dy)
        for yy in range(dy-scale-1,dy+scale+2):
            for xx in range(dx-scale-1,dx+scale+2):
                if hint is not None and max(abs(xx-hint[0]),abs(yy-hint[1]))>max(2,scale):
                    continue
                score=.35*_overlap_score(a,b,xx,yy,max_side=96)+.65*_overlap_score(detail_a,detail_b,xx,yy,max_side=96)
                if score>best[0]:
                    best=(score,xx,yy)
        _,bx,by=best
        best=(-2.,bx,by)
        for yy in range(by-1,by+2):
            for xx in range(bx-1,bx+2):
                if hint is not None and max(abs(xx-hint[0]),abs(yy-hint[1]))>max(2,scale):
                    continue
                score=.35*_overlap_score(a,b,xx,yy)+.65*_overlap_score(detail_a,detail_b,xx,yy)
                if score>best[0]:
                    best=(score,xx,yy)
        score,dx,dy=best
        if score<min_score:
            continue
        if axis=="vertical":
            dx,dy=dy,dx
        if any(max(abs(dx-item["dx"]),abs(dy-item["dy"]))<=scale+1 for item in validated):
            continue
        validated.append({"dx":dx,"dy":dy,"score":round(score,6),
                          "peak_score":round(coarse_score,6),
                          "candidate_count":len(peaks),"refined_candidate_count":min(32,len(peaks)),
                          "position_hint":hint_source})
    validated.sort(key=lambda item:item["score"],reverse=True)
    if not validated:
        raise ValueError("Whole-overlap validation failed; acquisition coordinates may be incorrect")
    if return_candidates:
        return validated
    return _choose_pair(validated,axis)

def _choose_pair(candidates,axis):
    best=dict(candidates[0])
    runner=candidates[1]["score"] if len(candidates)>1 else -1.
    if runner>=.72 and best["score"]-runner<.035:
        raise ValueError(f"Ambiguous {axis} acquisition step: best NCC {best['score']:.3f}, "
                         f"second {runner:.3f}. Use more neighboring tiles or "
                         "Tile grid (known acquisition overlap).")
    best["runner_up_score"]=runner
    return best

def _resolve_axis(edge_options,axis,tolerance):
    """Use repeated neighbor step sizes to resolve pair-wise aliases."""
    if len(edge_options)<2:
        return [_choose_pair(options,axis) for options in edge_options]
    models=[]
    for options in edge_options:
        for seed in options:
            chosen=[]
            for group in edge_options:
                nearby=[item for item in group
                        if max(abs(item["dx"]-seed["dx"]),abs(item["dy"]-seed["dy"]))<=tolerance]
                if not nearby:
                    break
                chosen.append(max(nearby,key=lambda item:item["score"]))
            if len(chosen)==len(edge_options):
                center=np.mean([(item["dx"],item["dy"]) for item in chosen],axis=0)
                if not any(np.max(np.abs(center-old[1]))<=tolerance for old in models):
                    models.append((sum(item["score"] for item in chosen)/len(chosen),center,chosen))
    models.sort(key=lambda item:item[0],reverse=True)
    if models and (len(models)==1 or models[0][0]-models[1][0]>=.035):
        return [dict(item,selection="shared_grid_step") for item in models[0][2]]
    # Preserve individually unique matches even when the scan step varies.
    return [_choose_pair(options,axis) for options in edge_options]

def register_grid(mapping,tile_size,overlap=None):
    """Find shared acquisition steps, then fit consistent global positions."""
    keys=sorted(mapping)
    if len(keys)<2:
        raise ValueError("Select at least two neighboring tiles")
    grouped={"horizontal":[],"vertical":[]}
    adjacency={key:set() for key in keys}
    if overlap is not None and (len(overlap)!=2 or any(not 0<v<100 for v in overlap)):
        raise ValueError("Known horizontal/vertical overlap must be between 0 and 100 percent")
    for key in keys:
        for delta,axis in (((0,1),"horizontal"),((1,0),"vertical")):
            neighbor=(key[0]+delta[0],key[1]+delta[1])
            if neighbor not in mapping:
                continue
            hint=None
            if overlap is not None:
                hint=(tile_size[0]*(1-overlap[0]/100),0) if axis=="horizontal" else (
                    0,tile_size[1]*(1-overlap[1]/100))
            options=estimate_translation(mapping[key],mapping[neighbor],axis,
                                         hint=hint,return_candidates=True)
            grouped[axis].append((key,neighbor,options))
            adjacency[key].add(neighbor)
            adjacency[neighbor].add(key)
    edges=[]
    for axis,records in grouped.items():
        if not records:
            continue
        selected=_resolve_axis([record[2] for record in records],axis,
                               max(2,min(tile_size)*.002))
        for (key,neighbor,_),match in zip(records,selected):
            edges.append({"first":list(key),"second":list(neighbor),**match})
    seen,pending=set(),[keys[0]]
    while pending:
        key=pending.pop()
        if key not in seen:
            seen.add(key)
            pending.extend(adjacency[key]-seen)
    if len(seen)!=len(keys):
        raise ValueError("Tile grid is disconnected: no reliable position for every tile")
    index={key:i for i,key in enumerate(keys)}
    matrix=np.zeros((len(edges)+1,len(keys)))
    offsets=np.zeros((len(edges)+1,2))
    matrix[0,0]=1
    for i,edge in enumerate(edges,1):
        matrix[i,index[tuple(edge["first"])]]=-1
        matrix[i,index[tuple(edge["second"])]]=1
        offsets[i]=(edge["dx"],edge["dy"])
    positions=np.linalg.lstsq(matrix,offsets,rcond=None)[0]
    residual=np.linalg.norm(matrix@positions-offsets,axis=1)
    if float(residual.max())>3:
        raise ValueError("Inconsistent overlap matches: grid residual exceeds 3 pixels")
    positions=np.rint(positions-positions.min(axis=0)).astype(int)
    return {key:tuple(map(int,positions[index[key]])) for key in keys},edges
