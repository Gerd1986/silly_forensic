"""Read only a bounded ROI from a SEM image, including giant uncompressed BigTIFF."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

MAX_ROI_PIXELS=12_000_000

def read_gray_roi(image_path,roi,max_pixels=MAX_ROI_PIXELS):
    x,y,w,h=map(int,roi)
    if x<0 or y<0 or w<1 or h<1 or w*h>max_pixels:
        raise ValueError(f"ROI must be positive and at most {max_pixels} pixels")
    path=str(image_path)
    if Path(path).suffix.lower() in (".tif",".tiff"):
        try:
            from .bigtiff_reader import BigTiffReader
        except ImportError:
            from bigtiff_reader import BigTiffReader
        try:
            reader=BigTiffReader(path)
        except (ValueError,OSError,TypeError):
            reader=None
        if reader is not None:
            try:
                if x+w>reader.width or y+h>reader.height:
                    raise ValueError("ROI exceeds image bounds")
                raw,tw,th,channels=reader.crop(0,x,y,w,h)
                rgb=np.frombuffer(raw,dtype=np.uint8).reshape(th,tw,channels)
                # RGB is created from the original grayscale by BigTiffReader.
                return np.ascontiguousarray(
                    (rgb[:,:,:3].astype(np.uint16) @ np.array([77,150,29],dtype=np.uint16) >> 8)
                    .astype(np.uint8))
            finally:
                reader.close()
    with Image.open(path) as source:
        if x+w>source.width or y+h>source.height:
            raise ValueError("ROI exceeds image bounds")
        return np.asarray(ImageOps.grayscale(source.crop((x,y,x+w,y+h))),
                          dtype=np.uint8).copy()
