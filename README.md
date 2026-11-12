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
GitHub Actions workflow `SiliconRE Windows build` compiles the sources and builds a portable PyInstaller onedir ZIP artifact. Run it via **Actions  SiliconRE Windows build  Run workflow** on the feature branch. The workflow does not publish a GitHub Release automatically. The resulting ZIP is a CI artifact, not yet a tested release.

## Optional libvips reader
TIFFs preferentially use `pyvips` for random-access tile reads when both the Python binding and native libvips are installed; otherwise the Pillow reader is used. Install `pyvips` and a compatible Windows libvips runtime separately. The CI-built EXE currently does **not** bundle the native libvips DLLs, so its TIFF handling falls back to Pillow. Pyramid SubIFD compatibility and 16-bit contrast fidelity need validation with representative SEM images.

## Publishing a prerelease
From **Actions  SiliconRE Windows build and release  Run workflow**, choose `feature/siliconre-v0.1`, check `publish_release`, and enter `v0.2.0`. After a successful build, GitHub CLI creates a prerelease tagged at that exact commit and uploads `SiliconRE-Windows-x64.zip`. Ordinary pushes only build and upload an Actions artifact; they do not publish releases. To publish another version, choose a new unused tag.

## v0.2 experimental trace candidates
The **Detect trace candidates** toolbar action segments the currently visible region using Otsu or a manually selected grayscale threshold, a small morphological opening, and connected-component analysis. Results can be exported to JSON with bounding boxes and centroids. Limitations: foreground polarity is currently bright-on-dark, results are unverified visual candidates (not nets), and the processing path uses Pillow and may decode large TIFFs. Zoom into a region under 12 MP before running. No automatic electrical net tracing or transistor recognition is claimed.

## v0.2 candidate review
After **Detect trace candidates**, bounding boxes appear directly over the SEM layer: cyan = candidate, green = accepted, red = rejected. Select one or more boxes and use **Accept selected**, **Reject selected**, or **Delete selected**. Drag a box to correct its position; its position and review state are saved to SQLite and restored on reopening the layer. This is bounding-box editing only; resizing, contour editing, verified electrical connectivity, and automated net tracing remain future work.

## v0.2 release
In GitHub Actions run **SiliconRE Windows build and release** on `feature/siliconre-v0.1` with `publish_release=true` and `version=v0.2.0`. The workflow publishes a prerelease only if the Windows build succeeds. Confirm the build result and ZIP before relying on the release.

## v0.3 candidate editing and automatic release
Select one rectangle and choose **Resize selected** to enter width and height. **Export candidates CSV** writes all candidate boxes and review statuses for the current layer with UTF-8 BOM for Excel compatibility.

On a successful push build of the feature branch, the workflow attempts to create prerelease `v0.3.0` with the Windows ZIP. If the tag/release already exists, the workflow skips publishing rather than overwriting it. Future versions should update the release tag in the workflow or use the manual dispatch input. GitHub Actions results and the resulting release must still be verified.

## Standalone Windows MCP server (v0.3.1)
The Windows CI builds `SiliconRE-MCP-Windows-x64.zip` alongside the GUI ZIP. Unpack the MCP ZIP and configure your MCP client to launch `SiliconRE-MCP.exe` from its unpacked directory with the environment variable `SILICONRE_PROJECT` set to the absolute path of an existing SiliconRE project folder containing `project.sqlite3`. The server uses stdio; it does not expose a network port.

Read-only MCP tools: `list_layers()`, `list_annotations(layer)`, `list_trace_candidates(layer, status="")`. The status filter accepts `candidate`, `accepted`, `rejected`, or an empty string for all. Native Windows packaging must be verified from the GitHub Actions run before use.

## v0.4 manual connectivity graph
Accept at least two candidates in a layer, Ctrl+click to select both and choose **Connect selected**. A magenta line represents an explicitly asserted visual connection, not verified electrical continuity. **Remove selected links** deletes the selected pair's link. Links are stored in SQLite and restored when switching layers. **Export manual nets JSON** exports links and transitive connected groups of accepted candidate IDs. Deleting a candidate removes its links. This version does not automatically detect vias, join connections across layers, trace actual contours, or infer circuit nets.

MCP adds `list_manual_links(layer)` and `list_manual_net_groups(layer)` as read-only tools. The Windows build runs SQLite graph regression tests before creating the GUI and MCP executables. The feature branch push attempts an automatic `v0.4.0` prerelease, subject to successful CI and GitHub permissions.

## v0.5 via-like feature proposals
With a layer open, zoom into a region below 12 megapixels and choose **Detect via candidates**. OpenCV's Hough circle detector proposes round features. Yellow rings are unreviewed, green rings accepted, red rings rejected. Use **Review nearest via** (a candidate selection dialog; not spatial nearest-neighbor selection) to change the review status, and **Export vias JSON** to save positions and radii. The proposals persist in SQLite. MCP adds `list_via_candidates(layer, status="")`.

This is experimental 2D circular feature detection, **not** confirmed via recognition, multilayer electrical connectivity, or transistor identification. Repeated scans may create duplicate proposals; image contrast and radius settings strongly affect results. v0.5.0 release is attempted by GitHub Actions on branch push after tests/build; inspect CI and Releases for confirmation.

## v0.5 completed: multilayer net reconstruction and structural HDL
The GUI now supports **Associate via to trace** (accepted objects in the same layer), **Connect vias across layers** (two accepted vias on distinct layers), and **Suggest aligned via pairs** (geometric proposals using saved translation alignment and a pixel tolerance, with explicit human confirmation). Only confirmed manual links become graph edges. **Export reconstructed nets** supports JSON (`.json`), structural Verilog (`.v`) and structural VHDL (`.vhd`). The HDL defines one signal per connected group, with **no inferred gates, transistors, ports, logic equations or electrical verification**. It is an intermediate connectivity representation, not a functional reverse-engineered IC design. Existing v0.4 manual trace links contribute to the same graph. Unaccepted/rejected elements do not participate.

MCP adds `list_reconstructed_nets()`, `get_structural_hdl(language="verilog")` and `suggest_cross_layer_vias(base_layer,overlay_layer,tolerance_px=10.0)`. The SQLite schema is extended non-destructively with via-to-trace and cross-layer via link tables. Regression tests cover cross-layer grouping, invalid links, alignment geometry and HDL output.

**Known limitations:** Via detection is based on circles and may miss noncircular contacts. Alignment currently handles translation only, not rotation, distortion or scale. Proximity is only a suggestion, never electrical evidence. No gate/transistor identification or transistor-level Verilog/VHDL reconstruction is implemented.

## v1.0 milestone: manual MOS-level circuit reconstruction
The v1.0 GUI adds **Add MOS transistor**, **Delete MOS transistor**, and **Export transistor netlist**. Select a layer and assign a unique transistor name, NMOS/PMOS type, and accepted trace IDs for gate, drain, source, and optional bulk. The device annotation is placed at the current viewport center; automatic transistor recognition is **not implemented**. A device can be removed and recreated to correct terminals.

The shared reconstructed connectivity graph resolves trace IDs to net names. **Export reconstructed nets** now includes manually annotated MOS devices in JSON, Verilog primitive instances (`nmos`/`pmos`, with gate/drain/source and no bulk modeling) and VHDL component instances (black boxes requiring an external implementation). **Export transistor netlist** writes a SPICE `.cir` file with MOS terminal order drain, gate, source, bulk; devices lacking bulk mapping are commented out, and transistor models must be supplied externally. The MCP server exposes `list_annotated_transistors()` and `get_spice_netlist()`.

**Evidence limitations:** Device type, location and terminal associations are user annotations, not SEM-derived identifications. The software cannot yet automatically recognize individual transistors, extract channel geometry, infer functional logic, provide PDK models, or prove electrical continuity. VHDL black boxes are not simulation-ready without implementation. This v1.0 milestone is a preview until Windows CI and representative real SEM datasets have been validated.

## v1.1 experimental automatic MOS crossing candidates
Choose an imported POLY mask layer, zoom into an ROI under 12 MP, then click **Detect MOS candidates** and select the ACTIVE mask layer. Both masks must already be aligned, in the same pixel coordinate system and at the same scale. Select bright or dark foreground polarity for each mask. Otsu thresholding produces binary masks; connected components of their intersection become orange, unverified MOS crossing candidates. **Review MOS candidate** changes the review state (green accepted, red rejected); **Export MOS candidates JSON** saves their geometry. The SQLite records are not automatically converted into MOS devices and cannot silently alter a reconstructed netlist. MCP adds `list_mos_crossing_candidates(poly_layer="",status="")`.

**Important:** Real SEM photos are not equivalent to extracted POLY and ACTIVE masks. This first detector requires clean masks and cannot infer which delayered image corresponds to which material. It does not yet identify source/drain, well doping, NMOS/PMOS type, transistor width/length, or circuit logic. Mask registration, false positives and non-Manhattan layouts require review. The Windows CI includes synthetic mask regression tests; release success requires GitHub Actions verification.

## v1.2 debug port evidence analyzer
**Analyze debug port clues** examines user-created marker labels for JTAG (IEEE 1149.1), SWD, compact JTAG (IEEE 1149.7), UART, SPI, I2C, SWIM, BDM and Nexus (IEEE-ISTO 5001). For each protocol, the analyzer lists matched/missing required signal names and candidate net associations where a marker falls inside an accepted trace box. It can export an evidence JSON report; MCP exposes `list_debug_port_hypotheses()`.

This is **label-based hypothesis generation**, not automatic TAP controller recognition or a confirmed electrical pinout. SPI, I2C and UART signals are not necessarily debug interfaces. The tool does not claim debug access is enabled or accessible, and performs no probing, bypass, or unlocking. The next research stage requires gate/flip-flop reconstruction and characteristic TAP finite-state-machine verification from an independently validated netlist.

## v1.3 sequential structure analyzer
**Annotate logic cell** records manually identified DFF, MUX2, AND2, OR2, XOR2, NOT, NAND2 and NOR2 gates and their accepted trace IDs. **Analyze sequential logic** groups DFFs by resolved clock net, searches direct Q-to-D chains as possible shift registers, and flags exactly four DFFs sharing a clock as possible state-register candidates. Exported JSON includes supporting cell names and nets; MCP provides `analyze_sequential_logic()`.

These are graph-level *structural hypotheses*, not an automatically recognized JTAG TAP or debug port. Four DFFs do not prove 16 reachable states, nor the IEEE 1149.1 transition function. Full TAP verification needs gate-level Boolean evaluation, TMS branching and IR/DR paths. Current detection requires human-identified cells and trace mappings; it does not extract flipflops from SEM images automatically.

## Batch SEM image import
Use **Add multiple SEM images** to select many files at once, or **Import image folder** to load all supported images in a directory. Folder import optionally includes subfolders. Supported extensions: PNG, JPEG, TIFF, BMP. The application derives layer names from filenames and automatically adds numeric suffixes to avoid overwriting existing layer names (case-insensitive). A confirmation dialog shows the planned image count. Image files are referenced by their original paths, not copied into the project; keep source files accessible.

## v1.4 automatic SEM mosaics (replaces individual image import)
Use **Stitch selected SEM images** or **Stitch SEM image folder**. Each batch produces exactly one stitched TIFF and one named layer. Files must depict overlapping areas of the **same physical delayering level** and be sufficiently textured for OpenCV's SCANS stitcher to register them. Output is saved under `project/stitched_layers/`; a `.sources.json` sidecar records original paths. Original files are not changed. Failed alignment stops the import without creating a layer; no blind tiling or fabricated positions. Image stitching is memory-intensive and currently capped at 24 megapixels per source and 120 megapixels per mosaic. The GUI operation is synchronous and may temporarily block interaction. Existing imported layers remain untouched. To combine existing layers, re-import their source images together into a new mosaic layer. Stitching is not suitable for distinct POLY/METAL/VIA levels: those should be separate layers aligned via overlay registration.

## v1.5 deterministic tile-grid stitching
When filenames follow `Tile_013-017-000000_0-000.s0001_e00.tif`, the first number is the row and the second the column. Increasing rows run down; increasing columns run right. Select **Tile grid (edge-to-edge, no overlap)** only for images that have already been cropped to meet exactly. Duplicate coordinates and different tile dimensions are rejected. Missing cells are reported and require confirmation. See the overlap-aware mode below for uncut SEM acquisitions.

## v1.6 large tile grids
Tile-grid mode now uses a disk-backed, uncompressed BigTIFF via tifffile.memmap rather than allocating the complete mosaic in RAM. The previous 120 MP guard is replaced with a 25-billion-pixel ceiling and a free-disk-space check. A 77,824 x 53,248 8-bit grayscale mosaic needs approximately 4.14 GB of disk, RGB approximately 12.43 GB. The GUI operation is synchronous and may take significant time. Very large BigTIFF viewing depends on the TIFF reader; use the optional libvips reader for efficient display if Pillow cannot handle the file. Output is currently 8-bit, so quantitative 16-bit SEM data should not be processed with this mode until native-depth support is implemented.

## v1.8 overlap-aware SEM tile stitching

Use **Stitch selected SEM images** or **Stitch SEM image folder**, then choose
**Tile grid (automatic overlap alignment)**. This is preselected for
`Tile_ROW-COL...` filenames. Import uncut neighboring images from the same
physical layer, then give the assembled layer a name.

The filename provides topology, not the final pixel distance. Normalized
cross-correlation finds each horizontal and vertical neighbor's displacement,
refines it at original resolution, and fits a consistent global position graph.
The supported search range is **5-50% overlap**, with transverse drift up to
approximately **8%**. Images must have equal dimensions and at least 32 pixels
per side. Acquisition orientation is increasing rows downward and columns rightward.
Rotation, scale differences, reversed scan directions, and different delayering
levels require another registration workflow.

Flat areas, ambiguous repeating patterns, unrelated images, disconnected grids,
and inconsistent loops are rejected with an explanation. No guessed placement
is used after an alignment failure. Missing cells are allowed only if the
remaining neighboring tiles form a connected graph.

One uncompressed, disk-backed BigTIFF is written for the new layer. Source
images need no manual cropping. Original pixels are translated to integer
coordinates without geometric warping or averaging; overlaps use the later
tile in sorted row/column order. This can retain a brightness seam if exposures
differ. The sources remain unchanged. The output remains 8-bit, as in the
existing grid writer.

The `.sources.json` records each source's actual mosaic position, neighbor
displacements, correlation scores, missing cells, and the overlap policy.
These are image-registration results and do not verify electrical continuity.
The edge-to-edge mode remains available explicitly for already-cropped tiles.
Large output limits and disk-space checks from v1.6 still apply. Import remains
synchronous and may temporarily block the GUI.

Validation includes synthetic horizontal/vertical overlaps, transverse drift,
brightness offsets, coarse-to-full-resolution refinement, a consistent 2�2
grid, periodic ambiguity, textureless tiles, disconnected and inconsistent
graphs, and BigTIFF pixel/source-preservation checks. Original-acquisition validation is described in the v1.10 section below.

## v1.8.1 overlap ambiguity correction

Overlap matching now compares distinct local correlation maxima, rather than
treating the flank of one broad maximum as a second match. Plausible candidates
are refined and ranked using the entire shared image area. This also helps when
a narrow edge strip repeats but the rest of the overlap distinguishes positions.
Genuinely indistinguishable positions remain rejected. Error messages include
the best and runner-up full-overlap scores; successful registration records the
candidate count and runner-up score. The window title now displays 1.8.1.

## v1.9 scan acquisition geometry

Automatic Tile Grid mode now correlates the **complete shared area for every
coarse candidate translation**, using intensity and local detail to reduce
the influence of illumination gradients. It no longer rejects an image merely
because more than 16 correlation maxima were found. Up to 32 leading maxima
are refined at original pixel coordinates using samples distributed across
the overlap; the sources are not resampled for output.

Horizontal and vertical neighbor candidates are grouped separately. A unique
shared scan-step model can resolve individually ambiguous pairs. Multiple
equally plausible scan-step models still require acquisition information.
The final position graph must remain connected and consistent.

Standard TIFF **XPosition (286), YPosition (287), XResolution (282),
YResolution (283), and ResolutionUnit (296)** can constrain the displacement
when both images use compatible physical units and resolution. Zero/absent
position deltas are not interpreted as measured overlap. Vendor-specific
stage metadata is not guessed or automatically converted.

If you know the overlap configured in the SEM scan software, select
**Tile grid (known acquisition overlap)**. Enter the horizontal and vertical
percentages from that acquisition. These constrain the expected origins,
including a near-zero transverse step, and are checked against image contents.
The dialog defaults are examples, not detected values. Filenames contain row
and column indices but do not themselves specify pixel pitch or overlap.
Exact zero-overlap acquisitions should use the existing edge-to-edge mode.

The source sidecar records requested overlap, positional hint source, candidate
counts, scores, and selection by shared grid step. Purely periodic images with
no unique features, no usable positions, and no known scan pitch can have
several mathematically equivalent alignments. The program reports that
ambiguity instead of inventing a scan step.

Synthetic regressions cover whole-area FFT normalization, known acquisition
steps and calibrated TIFF positions on periodic textures, shared grid
disambiguation, genuinely ambiguous shared steps, and more than 16 maxima.
Original-acquisition validation and FEI metadata support are described below.

### v1.9.1 large-tile coarse-search correction

The coarse-search threshold accounts for sampling-phase differences introduced
when large tiles are reduced for FFT matching. Every accepted displacement must
still pass the original-resolution feature-correlation threshold. A regression
uses 4096-pixel-wide sources shifted by 3001 pixels horizontally and 3 pixels
vertically, recovering the exact original-resolution displacement.

## v1.10 FEI/Apreo acquisition support and SEM noise handling

For unrotated FEI/Thermo Fisher SEM TIFFs with calibrated acquisition XML in
tag **34683**, automatic Tile Grid mode now reads stage X/Y, pixel size in
metres, scan rotation and beam shift. Image Y runs opposite to stage Y for this
supported acquisition orientation. The parser validates common pixel scale,
stage rotation, constant beam shift and plausible neighbor spacing. Unsupported
metadata does not silently receive a guessed calibration.

This provides the measured mosaic geometry before image matching. Gaussian
noise suppression followed by a wider bandpass prevents high-frequency SEM
shot noise from dominating the detail correlation. Image alignment searches
within a bounded neighborhood of the measured stage displacement. Reliable
image matches refine the tile origins using a robust global fit with soft
stage-position priors. Stage positioning can differ from actual image alignment
by several dozen pixels, so metadata does not impose an exact two-pixel search.

Textureless or genuinely ambiguous image neighbors keep their measured FEI
layout and are explicitly marked **metadata_only** for review. Inconsistent
image constraints are downweighted; fitted seam residuals over eight pixels
are marked for review. A maximum field-relative drift limit prevents runaway
image fits. The sources sidecar records expected stage displacement, measured
image alternatives, selection method, fitted displacement, residual and review
state. This is image assembly; it does not establish electrical continuity.

Use **Tile grid (automatic overlap alignment)** with images from one physical
layer. No overlap percentage or manual cropping is required for a supported
FEI acquisition. The import shows alignment and writing progress and can be
cancelled; partial TIFF output is removed before a layer is added. The completion
status reports the number of neighbor connections marked for review.

Validation on the supplied local acquisition identifies an approximately
**10% nominal overlap** (3686.4-pixel median step for 4096-pixel tiles), with
slightly different boundary steps. The original v1.9.1 rejection was reproduced
on the first M1 pair. Real 2�2 samples from M1, M2, M3 and Poly assemble using
the corrected metadata and image matching. Synthetic regressions cover
independent SEM-like noise, stage-position error, unit/rotation validation,
metadata-only layout, bounded drift, BigTIFF provenance and cancellation cleanup.

The Windows workflow now builds pushes to main and the development branch.
Automatic release publication runs from main; development pushes build artifacts
without publishing a release.
Full local M1 validation assembled all **247 source tiles** and **462 neighbor
connections** into a **69,982 × 47,912** BigTIFF (3,353,043,120 bytes).
443 neighbor connections were image-refined; 19 required measured-geometry
fallback. The final fit marked 21 connections for review, including two
image-refined connections with larger loop residuals. Nine source-center pixel
samples matched the written mosaic, and the full-chip overview was inspected.
These review flags remain material when tracing conductors across tile seams.

## v1.10.1 BigTIFF overview display correction

The virtual TIFF pyramid now samples **both X and Y** by the selected level
factor. Previously only Y was subsampled while the display buffer advertised
a reduced X dimension; Qt interpreted these overlong rows with the wrong
stride, producing horizontal streaks and a distorted overview.

The renderer checks tile shape and byte count before creating a Qt image.
Fractional minification uses smooth display interpolation; magnified pixel
views keep nearest-pixel rendering. Regression coverage compares complete
tiles with direct two-axis source samples at every pyramid level, including
odd dimensions, edge tiles, RGB/RGBA and 16-bit inputs. The corrected reader
and actual Qt scene rendering were checked against the complete local M1 TIFF.

This changes display only. Existing source TIFFs, stitched mosaics, project
coordinates and feature coordinates need no reconstruction or conversion.
Restart with v1.10.1 and reopen the existing project.

## v1.10.2 bounded previews for automatic overlay alignment

Auto-align no longer opens a complete multi-gigapixel TIFF through Pillow.
Uncompressed TIFF mosaics use the memory-mapped reader and streamed box averaging
into a preview of at most 1536 pixels per side. Temporary reductions use source
row bands; no full-resolution grayscale image is allocated. Up to four small
read-only previews are cached and invalidated by file size/mtime. Pillow's
decompression protection remains enabled.

The two mosaics use **one common source-pixel sampling step**. Different mosaic
widths and heights are padded on a common preview canvas instead of independently
stretched or rejected. Images must still share pixel scale and orientation.
The result remains a translation in original base-image coordinates.

A low phase-correlation response is rejected without changing the stored
alignment. The message now explains that different layer structure, rotation
or scale changes can prevent translation-only matching. This is not a general
affine or nonrigid inter-layer registration method.

Validation covers a 225-megapixel TIFF with Pillow decoding explicitly forbidden,
partial box-average blocks, 16-bit previews, displacement direction, unequal
mosaic dimensions and flat-image rejection. All 71 local regression tests pass.
The existing large M1/M2/M3/POLY project TIFFs were read successfully: M1/POLY
produced a provisional translation candidate, while M2 and M3 had insufficient
phase correlation. Those weak matches must not be applied as verified alignment.
No source TIFF, annotation or existing project alignment was modified by validation.

Restart with v1.10.2 and reopen the existing project; restitching is unnecessary.



## Grouped menus and global annotation clear (v1.10.3)

Commands are grouped under Project, Layers, Annotations, Traces, Vias, Devices,
Analysis, Export and View. The compact quick-access toolbar keeps Open project,
Mark point, Overlay layer and Fit visible.

**Annotations → Clear all annotations...** clears the whole project's markers,
trace/via/MOS candidates (all review states), manual connections, MOS devices
and logic cells/pin mappings across every layer. Confirmation defaults to Cancel.
Layers, source and stitched images, and alignment offsets are retained.
Before deletion a complete SQLite backup is saved in the project's
`annotation_backups` folder. If backup or deletion fails, annotations are retained.
To restore, close SiliconRE, keep a copy of the current database, and replace
`project.sqlite3` with the chosen backup database. Existing exported files are untouched.


## Manual layer correction and MCP image screening (v1.11.0)

Choose a base layer, then **Layers → Overlay layer**, then
**Layers → Manual overlay correction...**. The dialog previews X/Y offset,
clockwise rotation and uniform scale live. Use the spin-box arrows for small
steps (0.01 degree / 0.01 percent), or type a larger value. Save stores the pair;
Cancel restores the previous view. Reset sets (dx,dy,rotation,scale) to (0,0,0,1)
in the preview and still requires Save to persist.

Coordinates use the original image pixels: X right, Y down, origin at the
top-left. Rotation and scaling are around the overlay's (0,0), followed by
translation in base pixels. Adjust angle/scale first and then offsets.
This is a rough registration aid, not a validated automatic registration.
Original image files, annotations and their source-layer coordinates are retained.
Legacy translation-only alignments automatically gain zero rotation and scale 1
when saved/opened by the GUI. Reverse layer pairs use the inverse transform.
Saving one direction removes a stale saved reverse entry.

Translation-only autoalignment will not overwrite an active manual rotation or
scale. Via pairing uses the full saved transform; the POLY/ACTIVE mask detector
requires pixel-aligned masks and does not silently ignore overlay transforms.

The standalone MCP executable still uses local **stdio**, without a network port.
Set **SILICONRE_PROJECT** to the folder containing **project.sqlite3** and launch
the executable from a compatible local MCP client. Replace the complete extracted
MCP distribution when updating; the new server includes image dependencies.

New MCP tools:

| Tool | Purpose |
| --- | --- |
| get_layer_info | Original width/height, coordinate convention and available alignment pairs |
| get_layer_alignment | Saved overlay-to-base matrix, angle, scale and translation |
| get_layer_overview | Anti-aliased grayscale overview with original-coordinate metadata |
| get_layer_region | Detailed region using original source-layer pixel coordinates |
| compare_layer_region | Base, transformed overlay and blend on exactly the same base-coordinate grid |
| plan_layer_scan | Paginated overlapping screening rectangles; no automatic inspection claim |

For example, request an M1 overview, then compare M1 with poly at
x=10000, y=12000, width=4096, height=4096. The comparison requires a saved alignment
and reads your GUI correction. Each request returns PNG image content and JSON
metadata, including source dimensions, requested rectangle, output scale,
transform and coverage. Transparent regions have no image data. Comparisons
do not automatically mark, accept or connect features.

Use plan_layer_scan with layer=M1, tile_width=4096, tile_height=4096,
overlap_px=256, limit=25, then request the returned rectangles and continue from
next_index. This provides reproducible coordinates for a systematic review.

Outputs default to at most 1024 pixels/side (hard maximum 1536). At native detail,
one output pixel represents one original pixel; larger regions are reduced.
For fine contacts, request smaller regions rather than trusting an overview.
Large uncompressed TIFFs are memory-mapped and box-averaged in row bands; they
are not fully decoded into RAM. Other formats retain the 24-million-pixel decode
guard. These are grayscale inspection views, not scientific intensity exports.

The six image/geometry tools open the database read-only, do not migrate its
schema, and do not modify original images or annotations. The existing metadata
tools keep their existing schema behavior. Returned crops are sent to the MCP
client for review; a local connection does not imply local-only AI processing.
No raw SEM sample images are included in this repository.

Installing/connecting the MCP client remains a separate step: adding these tools
does not automatically install a ChatGPT desktop plugin.
