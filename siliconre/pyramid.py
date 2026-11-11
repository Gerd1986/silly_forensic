"""Generate a disk-backed image pyramid with libvips, if installed.

Optional dependency: pyvips and a compatible libvips runtime.
Pyramidal tiled TIFF permits random-access decoding at multiple resolutions.
"""
from pathlib import Path

def build_pyramid(source: str, destination: str):
    try:
        import pyvips
    except ImportError as exc:
        raise RuntimeError("Install pyvips and the libvips runtime for pyramid generation") from exc
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError("Output must differ from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = pyvips.Image.new_from_file(str(source), access="sequential")
    image.tiffsave(
        str(destination), tile=True, pyramid=True, tile_width=256,
        tile_height=256, compression="deflate", bigtiff=True
    )
    return str(destination)
