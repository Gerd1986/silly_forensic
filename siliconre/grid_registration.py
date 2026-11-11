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

def _overlap_score(a, b, dx, dy):
    h, w = a.shape
    x0, y0 = max(0, dx), max(0, dy)
    x1, y1 = min(w, dx+w), min(h, dy+h)
    if x1-x0 < 4 or y1-y0 < 4:
        return -1.0
    step = max(1, int(max(x1-x0, y1-y0) / 384))
    return _ncc(a[y0:y1:step, x0:x1:step],
                b[y0-dy:y1-dy:step, x0-dx:x1-dx:step])

def estimate_translation(first, second, axis, min_score=0.72):
    """Return B's integer origin relative to A, or reject uncertain matches.

    Search covers 5-50% overlap and up to 8% transverse drift. Periodic
    ambiguous matches are rejected rather than silently choosing one period.
    """
    if axis not in ("horizontal", "vertical"):
        raise ValueError("Unknown neighbor axis")
    with Image.open(first) as image:
        a = np.asarray(image.convert("L"))
    with Image.open(second) as image:
        b = np.asarray(image.convert("L"))
    if a.shape != b.shape or min(a.shape) < 32:
        raise ValueError("Registration requires equal tiles at least 32 pixels wide and high")
    if axis == "vertical":
        a, b = a.T, b.T
    h, w = a.shape
    scale = max(1, int(np.ceil(max(h, w) / 1024)))
    size = (int(round(w/scale)), int(round(h/scale)))
    aa = np.asarray(Image.fromarray(a).resize(size, Image.Resampling.BOX))
    bb = np.asarray(Image.fromarray(b).resize(size, Image.Resampling.BOX))
    hh, ww = aa.shape
    margin = max(2, int(hh * .08))
    strip = max(3, int(ww * .04))
    start = int(ww * .50)
    stop = int(ww * .95)
    scores = _valid_ncc(aa[:, start:min(ww, stop+strip)],
                        bb[margin:hh-margin, :strip])
    # Restrict the origin to the documented overlap range.
    scores = scores[:, :stop-start+1]
    iy, ix = np.unravel_index(np.argmax(scores), scores.shape)
    peak = float(scores[iy, ix])
    other = scores.copy()
    radius = max(3, int(ww * .01))
    other[max(0, iy-radius):iy+radius+1, max(0, ix-radius):ix+radius+1] = -1
    if peak < min_score or peak - float(other.max()) < .035:
        raise ValueError(f"Uncertain/ambiguous {axis} overlap: {Path(first).name} -> "
                         f"{Path(second).name} (NCC {peak:.3f})")
    dx = int(round((start+ix) * w/ww))
    dy = int(round((iy-margin) * h/hh))
    # Refine at original resolution without resampling the source pixels.
    best = (-2.0, dx, dy)
    for yy in range(dy-scale-1, dy+scale+2):
        for xx in range(dx-scale-1, dx+scale+2):
            score = _overlap_score(a, b, xx, yy)
            if score > best[0]:
                best = (score, xx, yy)
    score, dx, dy = best
    if score < min_score:
        raise ValueError(f"Overlap validation failed (NCC {score:.3f})")
    if axis == "vertical":
        dx, dy = dy, dx
    return {"dx": int(dx), "dy": int(dy), "score": round(score, 6),
            "peak_score": round(peak, 6)}

def register_grid(mapping, tile_size):
    """Fit a globally consistent translation graph, requiring connectivity."""
    keys = sorted(mapping)
    if len(keys) < 2:
        raise ValueError("Select at least two neighboring tiles")
    edges = []
    adjacency = {key: set() for key in keys}
    for key in keys:
        for delta, axis in (((0, 1), "horizontal"), ((1, 0), "vertical")):
            neighbor = (key[0]+delta[0], key[1]+delta[1])
            if neighbor not in mapping:
                continue
            match = estimate_translation(mapping[key], mapping[neighbor], axis)
            edges.append({"first": list(key), "second": list(neighbor), **match})
            adjacency[key].add(neighbor)
            adjacency[neighbor].add(key)
    seen, pending = set(), [keys[0]]
    while pending:
        key = pending.pop()
        if key not in seen:
            seen.add(key)
            pending.extend(adjacency[key] - seen)
    if len(seen) != len(keys):
        raise ValueError("Tile grid is disconnected: no reliable position for every tile")
    index = {key: i for i, key in enumerate(keys)}
    matrix = np.zeros((len(edges)+1, len(keys)))
    offsets = np.zeros((len(edges)+1, 2))
    matrix[0, 0] = 1
    for i, edge in enumerate(edges, 1):
        matrix[i, index[tuple(edge["first"])]] = -1
        matrix[i, index[tuple(edge["second"])]] = 1
        offsets[i] = (edge["dx"], edge["dy"])
    positions = np.linalg.lstsq(matrix, offsets, rcond=None)[0]
    residual = np.linalg.norm(matrix @ positions - offsets, axis=1)
    if float(residual.max()) > 3:
        raise ValueError("Inconsistent overlap matches: grid residual exceeds 3 pixels")
    positions = np.rint(positions - positions.min(axis=0)).astype(int)
    return {key: tuple(map(int, positions[index[key]])) for key in keys}, edges
