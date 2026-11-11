"""Deterministic row/column tile-grid assembly without feature matching."""
import json
import re
from pathlib import Path
from PIL import Image
try:
    from .batch_import import EXTENSIONS
except ImportError:
    from batch_import import EXTENSIONS

PATTERN=re.compile(r"^tile_(\d+)-(\d+)(?:[-_.]|$)",re.IGNORECASE)

def parse_tile(path):
    match=PATTERN.match(Path(path).stem)
    return (int(match.group(1)),int(match.group(2))) if match else None

def plan_tile_grid(paths,max_pixels=120_000_000):
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
            raise ValueError(f"Duplicate tile coordinate {position}: {mapping[position].name} / {path.name}")
        with Image.open(path) as im:
            if dimensions is None:
                dimensions=im.size
                mode="L" if im.mode in ("1","L","I;16","I") else "RGB"
            elif im.size!=dimensions:
                raise ValueError(f"Different tile dimensions: {path.name}")
        mapping[position]=path
    if not mapping:
        raise ValueError("No tiles selected")
    rows=[rc[0] for rc in mapping]
    cols=[rc[1] for rc in mapping]
    r0,r1=min(rows),max(rows)
    c0,c1=min(cols),max(cols)
    width=(c1-c0+1)*dimensions[0]
    height=(r1-r0+1)*dimensions[1]
    if width*height>max_pixels:
        raise ValueError(f"Grid {width}x{height} exceeds {max_pixels} pixels")
    missing=[(r,c) for r in range(r0,r1+1) for c in range(c0,c1+1)
             if (r,c) not in mapping]
    return {"tiles":mapping,"tile_size":dimensions,"mode":mode,
            "bounds":[r0,r1,c0,c1],"size":[width,height],"missing":missing}

def stitch_tile_grid(paths,output,max_pixels=120_000_000):
    plan=plan_tile_grid(paths,max_pixels)
    r0,r1,c0,c1=plan["bounds"]
    tw,th=plan["tile_size"]
    # Grey missing cells make holes visible; no shifting of valid coordinates.
    canvas=Image.new(plan["mode"],tuple(plan["size"]),
                     127 if plan["mode"]=="L" else (127,127,127))
    for (row,col),path in sorted(plan["tiles"].items()):
        with Image.open(path) as im:
            tile=im.convert(plan["mode"])
            canvas.paste(tile,((col-c0)*tw,(row-r0)*th))
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.stem+".partial"+target.suffix)
    try:
        canvas.save(temp,format="TIFF",compression="tiff_lzw")
        temp.replace(target)
        result={"method":"filename_tile_grid","row_direction":"increasing_down",
                "column_direction":"increasing_right","bounds":plan["bounds"],
                "tile_size":list(plan["tile_size"]),"size":plan["size"],
                "missing_tiles":[list(rc) for rc in plan["missing"]],
                "sources":[{"row":r,"column":c,"path":str(path)}
                           for (r,c),path in sorted(plan["tiles"].items())],
                "output":str(target)}
        target.with_suffix(".sources.json").write_text(
            json.dumps(result,indent=2),encoding="utf-8")
        return result
    finally:
        canvas.close()
        if temp.exists():
            temp.unlink()
