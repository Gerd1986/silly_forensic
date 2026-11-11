# silly_forensic / SiliconRE

Windows-first SEM delayering reverse-engineering prototype.

## v0.1
- PySide6 desktop viewer with mouse-wheel zoom, drag pan, and fit-to-view
- Import SEM PNG/JPEG/TIFF images as named layers
- SQLite project metadata and point annotations
- Local read-only MCP server exposing layers and annotations

## Run (Windows, no virtual environment required)

Install Python 3.12+, then run from the repository root:

```powershell
py -m pip install -r requirements.txt
py siliconre/app.py
```

Create a project in an empty folder, import SEM images and select a layer.

## MCP

Set `SILICONRE_PROJECT` to the folder containing `project.sqlite3`. Configure your MCP client to launch `py siliconre/mcp_server.py` over stdio. The server only reads project metadata.

## Current limitations

Prototype, not yet tested on Windows. The viewer renders visible 512px tiles with a bounded cache; Pillow may still decode full compressed TIFF images internally. Multiresolution pyramids remain planned. Images are referenced by absolute paths. Layer alignment, segmentation, via detection, transistor recognition and netlist extraction are not yet implemented.

## Layer overlay and alignment
Select a base layer, click **Overlay layer**, and choose another layer. Adjust transparency with the right-hand slider. Click **Align overlay (dx,dy)** to set and persist a translation in pixel coordinates. This is manual translation only, not automatic feature registration, rotation, or affine correction.

## Experimental auto-registration
Choose a base layer and an overlay, then click **Auto-align overlay**. OpenCV phase correlation estimates a translation only, using previews. The images must have identical dimensions, orientation, and scale. Inspect results manually; repeating chip patterns can yield incorrect offsets. A weak response is rejected.

## TIFF pyramids
**Build TIFF pyramid** writes a tiled, multiresolution TIFF using optional `pyvips` and a separately installed Windows libvips runtime. The source is not overwritten. Pyramid generation currently runs synchronously and may freeze the UI for large files. The viewer now selects a reduced-resolution TIFF frame when Pillow exposes pyramid levels as frames. Some TIFF pyramids store reduced levels in SubIFDs that Pillow does not expose as frames; these still fall back to full-resolution tile crops. For reliable large-file random access, a libvips-backed reader is planned.

Install optional support with `py -m pip install pyvips` and install libvips for Windows.


## Multiresolution viewer (experimental)
The renderer chooses a TIFF frame based on the current display scale and uses a bounded tile cache. Import a generated pyramidal TIFF as a new layer to try it. Zooming selects among available frame resolutions; original image coordinates remain unchanged. This has not yet been validated against representative SEM TIFFs or tested on Windows.

## Windows executable (CI)
GitHub Actions workflow `SiliconRE Windows build` compiles the sources and builds a portable PyInstaller onedir ZIP artifact. Run it via **Actions → SiliconRE Windows build → Run workflow** on the feature branch. The workflow does not publish a GitHub Release automatically. The resulting ZIP is a CI artifact, not yet a tested release.

## Optional libvips reader
TIFFs preferentially use `pyvips` for random-access tile reads when both the Python binding and native libvips are installed; otherwise the Pillow reader is used. Install `pyvips` and a compatible Windows libvips runtime separately. The CI-built EXE currently does **not** bundle the native libvips DLLs, so its TIFF handling falls back to Pillow. Pyramid SubIFD compatibility and 16-bit contrast fidelity need validation with representative SEM images.

## Publishing a prerelease
From **Actions → SiliconRE Windows build and release → Run workflow**, choose `feature/siliconre-v0.1`, check `publish_release`, and enter `v0.2.0`. After a successful build, GitHub CLI creates a prerelease tagged at that exact commit and uploads `SiliconRE-Windows-x64.zip`. Ordinary pushes only build and upload an Actions artifact; they do not publish releases. To publish another version, choose a new unused tag.

## v0.2 experimental trace candidates
The **Detect trace candidates** toolbar action segments the currently visible region using Otsu or a manually selected grayscale threshold, a small morphological opening, and connected-component analysis. Results can be exported to JSON with bounding boxes and centroids. Limitations: foreground polarity is currently bright-on-dark, results are unverified visual candidates (not nets), and the processing path uses Pillow and may decode large TIFFs. Zoom into a region under 12 MP before running. No automatic electrical net tracing or transistor recognition is claimed.
