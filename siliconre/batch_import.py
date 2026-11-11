"""Non-destructive batch import planning for SEM layers."""
from pathlib import Path

EXTENSIONS={".png",".jpg",".jpeg",".tif",".tiff",".bmp"}

def images_in_folder(folder,recursive=False):
    root=Path(folder)
    if not root.is_dir():
        raise ValueError("Image folder does not exist")
    files=root.rglob("*") if recursive else root.iterdir()
    return sorted((p for p in files if p.is_file() and p.suffix.lower() in EXTENSIONS),
                  key=lambda p:str(p).casefold())

def plan_import(paths,existing_names):
    used={name.casefold() for name in existing_names}
    planned=[]
    seen=set()
    for item in paths:
        path=Path(item).resolve()
        if path.suffix.lower() not in EXTENSIONS or not path.is_file():
            continue
        if str(path).casefold() in seen:
            continue
        seen.add(str(path).casefold())
        base=path.stem.strip() or "layer"
        name=base
        suffix=2
        while name.casefold() in used:
            name=f"{base}_{suffix}"
            suffix+=1
        used.add(name.casefold())
        planned.append((name,str(path)))
    return planned
