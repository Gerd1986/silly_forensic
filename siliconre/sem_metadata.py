"""Calibrated FEI/Thermo Fisher SEM acquisition positions (TIFF XML tag 34683)."""
import xml.etree.ElementTree as ET
from PIL import Image
import numpy as np

def read_fei_position(path):
    try:
        with Image.open(path) as image:
            raw=getattr(image,"tag_v2",{}).get(34683)
            size=image.size
    except OSError:
        return None
    if not raw:
        return None
    if isinstance(raw,bytes):
        raw=raw.decode("utf-8",errors="replace")
    try:
        root=ET.fromstring(raw)
        def number(path):
            element=root.find(path)
            return float(element.text)
        position=(number("StageSettings/StagePosition/X"),
                  number("StageSettings/StagePosition/Y"))
        pixel=(number("BinaryResult/PixelSize/X"),number("BinaryResult/PixelSize/Y"))
        rotation=number("ScanSettings/ScanRotation")
        stage_rotation=number("StageSettings/StagePosition/Rotation")
        beam=(number("Optics/BeamShift/X"),number("Optics/BeamShift/Y"))
        for axis in ("X","Y"):
            if root.find("BinaryResult/PixelSize/"+axis).get("unit")!="m":
                return None
        if not np.isfinite([*position,*pixel,rotation,stage_rotation,*beam]).all() or min(pixel)<=0:
            return None
        if abs(rotation)>1e-8:
            return None  # Rotated scan coordinates require a calibrated transform.
        return {"stage":position,"pixel_size_m":pixel,"scan_rotation":rotation,
                "beam_shift":beam,"stage_rotation":stage_rotation,"size":size,"source":"fei_xml_34683"}
    except (ET.ParseError,AttributeError,TypeError,ValueError):
        return None

def fei_grid_positions(mapping,tile_size):
    records={key:read_fei_position(path) for key,path in mapping.items()}
    if not all(records.values()):
        return None
    first=records[sorted(records)[0]]
    for record in records.values():
        if abs(record["stage_rotation"]-first["stage_rotation"])>1e-5:
            return None
        if record["size"]!=tuple(tile_size):
            return None
        if not np.allclose(record["pixel_size_m"],first["pixel_size_m"],rtol=1e-5,atol=0):
            return None
        if not np.allclose(record["beam_shift"],first["beam_shift"],rtol=0,atol=1e-12):
            return None
    px,py=first["pixel_size_m"]
    x0,y0=first["stage"]
    positions={key:((record["stage"][0]-x0)/px,
                    -(record["stage"][1]-y0)/py) for key,record in records.items()}
    edges=[]
    for key in sorted(mapping):
        for delta,axis in (((0,1),"horizontal"),((1,0),"vertical")):
            other=(key[0]+delta[0],key[1]+delta[1])
            if other not in mapping:
                continue
            dx=positions[other][0]-positions[key][0]
            dy=positions[other][1]-positions[key][1]
            principal=dx if axis=="horizontal" else dy
            transverse=dy if axis=="horizontal" else dx
            side=tile_size[0] if axis=="horizontal" else tile_size[1]
            if not (.2*side<principal<side and abs(transverse)<.08*side):
                return None
            edges.append((key,other,axis,(dx,dy)))
    if not edges:
        return None
    return positions,edges

def fit_fei_positions(initial,observations,tile_size):
    """Soft stage priors, reliable image edges, robust handling of drift loops."""
    keys=sorted(initial)
    index={key:i for i,key in enumerate(keys)}
    n=len(keys)
    rows=[]
    values=[]
    weights=[]
    for key in keys:
        row=np.zeros(n);row[index[key]]=1
        rows.append(row);values.append(initial[key]);weights.append(1/80)
    row=np.zeros(n);row[0]=1
    rows.append(row);values.append(initial[keys[0]]);weights.append(1)
    for edge in observations:
        if edge["selection"]!="image_refined":
            continue
        row=np.zeros(n)
        row[index[tuple(edge["first"])]]=-1
        row[index[tuple(edge["second"])]]=1
        rows.append(row);values.append((edge["dx"],edge["dy"]));weights.append(1)
    matrix=np.array(rows)
    offsets=np.array(values)
    base=np.array(weights)
    weight=base.copy()
    for _ in range(4):
        fitted=np.linalg.lstsq(matrix*weight[:,None],offsets*weight[:,None],rcond=None)[0]
        residual=np.linalg.norm(matrix@fitted-offsets,axis=1)
        weight=base.copy()
        weight[n+1:]*=np.sqrt(np.minimum(1,8/np.maximum(residual[n+1:],1e-9)))
    # A questionable loop cannot pull a tile arbitrarily far from its measured stage origin.
    displacement=np.linalg.norm(fitted-np.array([initial[key] for key in keys]),axis=1)
    field_span=float(np.ptp(np.array(list(initial.values())),axis=0).max())
    if float(displacement.max())>min(tile_size)*.08+field_span*.04:
        fitted=np.array([initial[key] for key in keys])
        for edge in observations:
            edge["selection"]="metadata_only_global_drift_limit"
    fitted-=fitted.min(axis=0)
    rounded=np.rint(fitted).astype(int)
    result={key:tuple(map(int,rounded[index[key]])) for key in keys}
    for edge in observations:
        actual=rounded[index[tuple(edge["second"])]]-rounded[index[tuple(edge["first"])]]
        edge["fitted_displacement"]=actual.tolist()
        edge["fit_residual_pixels"]=round(float(np.linalg.norm(actual-[edge["dx"],edge["dy"]])),3)
        edge["review_required"]=edge["selection"]!="image_refined" or edge["fit_residual_pixels"]>8
    return result,observations