"""Deterministic disk-backed SEM tile-grid BigTIFF writer."""
import json
import re
import shutil
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image
try:
    from .batch_import import EXTENSIONS
except ImportError:
    from batch_import import EXTENSIONS

PATTERN=re.compile(r"^tile_(\d+)-(\d+)(?:[-_.]|$)",re.IGNORECASE)

def parse_tile(path):
    match=PATTERN.match(Path(path).stem)
    return (int(match.group(1)),int(match.group(2))) if match else None

def plan_tile_grid(paths,max_pixels=25_000_000_000):
    mapping={}
    dimensions=None
    mode=None
    for item in paths:
        path=Path(item).resolve()
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise ValueError(f"Invalid image: {path}")
        position=parse_tile(path)
        if position is None:
            raise ValueError(f"No Tile_ROW-COL coordinate in {path.name}")
        if position in mapping:
            raise ValueError(f"Duplicate tile coordinate {position}")
        with Image.open(path) as im:
            if dimensions is None:
                dimensions=im.size
                mode="L" if im.mode in ("1","L","I;16","I") else "RGB"
            elif im.size!=dimensions:
                raise ValueError(f"Different tile dimensions: {path.name}")
        mapping[position]=path
    if not mapping:
        raise ValueError("No tiles selected")
    rows=[p[0] for p in mapping]
    cols=[p[1] for p in mapping]
    r0,r1=min(rows),max(rows)
    c0,c1=min(cols),max(cols)
    width=(c1-c0+1)*dimensions[0]
    height=(r1-r0+1)*dimensions[1]
    if width*height>max_pixels:
        raise ValueError(f"Grid {width}x{height} exceeds {max_pixels} pixels")
    missing=[(row,col) for row in range(r0,r1+1)
             for col in range(c0,c1+1) if (row,col) not in mapping]
    return {"tiles":mapping,"tile_size":dimensions,"mode":mode,
            "bounds":[r0,r1,c0,c1],"size":[width,height],"missing":missing}

def stitch_tile_grid(paths,output,max_pixels=25_000_000_000):
    plan=plan_tile_grid(paths,max_pixels)
    r0,r1,c0,c1=plan["bounds"]
    tw,th=plan["tile_size"]
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.stem+".partial"+target.suffix)
    channels=1 if plan["mode"]=="L" else 3
    shape=(plan["size"][1],plan["size"][0]) if channels==1 else (
        plan["size"][1],plan["size"][0],3)
    output_bytes=int(np.prod(shape,dtype=np.int64))
    if shutil.disk_usage(target.parent).free < output_bytes+100_000_000:
        raise ValueError(f"Not enough free disk space ({output_bytes/1e9:.2f} GB needed)")
    canvas=None
    try:
        canvas=tifffile.memmap(temp,shape=shape,dtype=np.uint8,bigtiff=True,
                              photometric="minisblack" if channels==1 else "rgb")
        # Fill by stripes to avoid allocating a giant temporary array.
        for y in range(0,shape[0],256):
            canvas[y:min(y+256,shape[0])]=127
        for (row,col),path in sorted(plan["tiles"].items()):
            with Image.open(path) as im:
                tile=np.asarray(im.convert(plan["mode"]))
            y,x=(row-r0)*th,(col-c0)*tw
            canvas[y:y+th,x:x+tw]=tile
        canvas.flush()
        # Explicitly close the underlying Windows mmap before renaming.
        canvas._mmap.close()
        canvas=None
        temp.replace(target)
        result={"method":"filename_tile_grid_bigtiff",
                "row_direction":"increasing_down","column_direction":"increasing_right",
                "bounds":plan["bounds"],"tile_size":list(plan["tile_size"]),
                "size":plan["size"],"missing_tiles":[list(rc) for rc in plan["missing"]],
                "sources":[{"row":row,"column":col,"path":str(path)}
                           for (row,col),path in sorted(plan["tiles"].items())],
                "output":str(target)}
        target.with_suffix(".sources.json").write_text(
            json.dumps(result,indent=2),encoding="utf-8")
        return result
    finally:
        if canvas is not None:
            canvas._mmap.close()
            canvas=None
        if temp.exists():
            temp.unlink()
