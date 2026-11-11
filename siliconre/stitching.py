"""Conservative automatic SEM image stitching into one layer.

OpenCV Stitcher works on overlapping textured frames, not arbitrary delayered
images. On failure, no synthetic mosaic or false alignment is written.
"""
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
try:
    from .batch_import import EXTENSIONS
except ImportError:
    from batch_import import EXTENSIONS

def stitch_images(paths,output,mode="scans",max_input_pixels=24_000_000,
                  max_output_pixels=120_000_000):
    sources=[Path(p).resolve() for p in paths]
    if len(sources)<2:
        raise ValueError("Select at least two overlapping images")
    if len({str(p).casefold() for p in sources})!=len(sources):
        raise ValueError("Duplicate input image")
    frames=[]
    for path in sources:
        if path.suffix.lower() not in EXTENSIONS or not path.is_file():
            raise ValueError(f"Unsupported image: {path}")
        with Image.open(path) as im:
            if im.width*im.height>max_input_pixels:
                raise ValueError(f"Image exceeds stitching limit: {path.name}")
            rgb=np.asarray(im.convert("RGB"))
        frames.append(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
    if mode not in ("scans","panorama"):
        raise ValueError("Unknown stitching mode")
    stitcher=cv2.Stitcher_create(
        cv2.Stitcher_SCANS if mode=="scans" else cv2.Stitcher_PANORAMA)
    status,mosaic=stitcher.stitch(frames)
    if status!=cv2.Stitcher_OK or mosaic is None:
        raise ValueError(f"Automatic alignment failed (OpenCV status {status}). "
                         "Check overlap, texture, scale and scan order.")
    height,width=mosaic.shape[:2]
    if width*height>max_output_pixels:
        raise ValueError("Stitched image exceeds output size limit")
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.stem+".partial"+target.suffix)
    try:
        Image.fromarray(cv2.cvtColor(mosaic,cv2.COLOR_BGR2RGB)).save(temporary,
                                                                     compression="tiff_lzw")
        temporary.replace(target)
        metadata={"method":"opencv_stitcher","mode":mode,
                  "sources":[str(p) for p in sources],
                  "output":str(target),"size":[width,height],
                  "electrically_verified":False}
        target.with_suffix(".sources.json").write_text(
            json.dumps(metadata,indent=2),encoding="utf-8")
    finally:
        if temporary.exists():
            temporary.unlink()
    return metadata
