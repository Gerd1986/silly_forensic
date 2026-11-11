"""Bounded, lazy display of uncompressed BigTIFF SEM mosaics.

Uses memory mapping and virtual powers-of-two pyramid levels. Each request
materializes at most one viewport tile; no full-image Pillow decode.
"""
import numpy as np
import tifffile

class BigTiffReader:
    def __init__(self,filename):
        self.data=tifffile.memmap(str(filename),mode="r")
        if self.data.ndim not in (2,3) or (self.data.ndim==3 and self.data.shape[2] not in (3,4)):
            self.close()
            raise ValueError("Expected grayscale or RGB/RGBA BigTIFF")
        if self.data.dtype not in (np.uint8,np.uint16):
            self.close()
            raise ValueError("Unsupported BigTIFF pixel type")
        self.height,self.width=self.data.shape[:2]
        self.levels=[]
        scale=1
        while True:
            w=(self.width+scale-1)//scale
            h=(self.height+scale-1)//scale
            self.levels.append((scale,w,h))
            if w<=512 and h<=512:
                break
            scale*=2

    def crop(self,level,x,y,width,height):
        factor,w,h=self.levels[level]
        if x<0 or y<0 or x>=w or y>=h:
            raise ValueError("Tile outside image")
        width=min(width,w-x)
        height=min(height,h-y)
        tile=np.asarray(self.data[y*factor:(y+height)*factor:factor,
                                  x*factor:(x+width)*factor])
        if tile.dtype==np.uint16:
            tile=(tile>>8).astype(np.uint8)
        elif not tile.flags.c_contiguous:
            tile=np.ascontiguousarray(tile)
        if tile.ndim==2:
            tile=np.repeat(tile[:,:,None],3,axis=2)
        return tile.tobytes(),width,height,tile.shape[2]

    def close(self):
        data=getattr(self,"data",None)
        if data is not None:
            mmap=getattr(data,"_mmap",None)
            if mmap is not None:
                mmap.close()
            self.data=None
        self.levels=[]
