"""Memory-bounded grayscale previews, including multi-gigapixel TIFF mosaics."""
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

MAX_DECODE_PIXELS=24_000_000

def _mapped_reader(path):
    if Path(path).suffix.lower() not in (".tif",".tiff"):
        return None
    try:
        from .bigtiff_reader import BigTiffReader
    except ImportError:
        from bigtiff_reader import BigTiffReader
    try:
        return BigTiffReader(path)
    except (ValueError,OSError,TypeError):
        return None

def image_size(path):
    reader=_mapped_reader(path)
    if reader is not None:
        try:
            return reader.width,reader.height
        finally:
            reader.close()
    try:
        with Image.open(path) as image:
            if image.width*image.height>MAX_DECODE_PIXELS:
                raise ValueError("Large-image alignment requires an uncompressed memory-mappable TIFF")
            return image.size
    except Image.DecompressionBombError as exc:
        raise ValueError("Large-image alignment requires an uncompressed memory-mappable TIFF") from exc

def _mapped_preview(data,step):
    """Box-average in source row bands; never materialize the full image."""
    h,w=data.shape[:2]
    out_h=(h+step-1)//step
    out_w=(w+step-1)//step
    channels=1 if data.ndim==2 else min(3,data.shape[2])
    shape=(out_h,out_w) if channels==1 else (out_h,out_w,channels)
    result=np.empty(shape,dtype=np.float32)
    starts=np.arange(0,w,step)
    widths=np.minimum(step,w-starts)
    for row,y in enumerate(range(0,h,step)):
        band=data[y:min(y+step,h)]
        if channels>1:
            band=band[:,:,:channels]
        sums=np.add.reduceat(band.sum(axis=0,dtype=np.uint64),starts,axis=0)
        counts=widths*min(step,h-y)
        if channels>1:
            counts=counts[:,None]
        result[row]=sums/counts
    if data.dtype==np.uint16:
        result/=256.0
    if channels>1:
        result=result @ np.array([.299,.587,.114],dtype=np.float32)
    return np.ascontiguousarray(result,dtype=np.float32)

@lru_cache(maxsize=4)
def _cached_preview(path,size_bytes,mtime_ns,step,max_side):
    reader=_mapped_reader(path)
    if reader is not None:
        try:
            original=(reader.width,reader.height)
            result=_mapped_preview(reader.data,step)
        finally:
            reader.close()
    else:
        with Image.open(path) as image:
            original=image.size
            if original[0]*original[1]>MAX_DECODE_PIXELS:
                raise ValueError("Large-image alignment requires an uncompressed memory-mappable TIFF")
            gray=ImageOps.grayscale(image)
            shape=((original[0]+step-1)//step,(original[1]+step-1)//step)
            result=np.array(gray.resize(shape,Image.Resampling.BOX),dtype=np.float32)
    if max(result.shape)>max_side:
        raise ValueError("Preview exceeds its allocation limit")
    result.setflags(write=False)
    return result,original

def gray_preview(path,max_side=1536,step=None):
    if max_side<32:
        raise ValueError("Preview size must be at least 32 pixels")
    path=Path(path).resolve()
    if step is None:
        step=max(1,(max(image_size(path))+max_side-1)//max_side)
    if int(step)!=step or step<1:
        raise ValueError("Invalid preview sampling step")
    info=path.stat()
    return _cached_preview(str(path),info.st_size,info.st_mtime_ns,int(step),max_side)