# GE arithmetic, raster and sampling rules (hardware-measured)

What the GE actually computes, bit for bit. Everything tagged `[measured by the PPSSPP team on a PSP]` comes from
the PPSSPP project's 2026 hardware study: about 174 hand-built display-list probes run on a real PSP with the colour
and depth buffers read back, then 125 of 132 game frame dumps replayed on a PSP matching PPSSPP's new software
renderer pixel for pixel. The full write-ups are the PPSSPP GE pages (see Sources); this file is the part that
changes how you write PSP code. Lines tagged `[derived]` are our own arithmetic on top of that model, estimates,
not measurements.

Why it matters: none of this is visible in PPSSPP's hardware backends (D3D11 / OpenGL / Vulkan), which transform in
host float and sample with the host GPU. Z-fighting, texture swimming, seams and near-camera shimmer that appear
only on the console usually come from the rules below. See [rendering.md](rendering.md) and
[textures-uv.md](textures-uv.md) for what to do about them.

## Numbers: float24 everywhere

- Display-list commands carry a 24-bit payload, so every float parameter (matrix entries, light positions, viewport
  scale/offset, fog, texture scale/offset) is a float32 with the low 8 mantissa bits dropped: 1 sign, 8 exponent,
  15 fraction bits ("float24") `[public: PPSSPP GE docs, overview + arithmetic]`.
- The T&L unit also *computes* in float24, truncating toward zero (sign-magnitude), denormals as zero. Float vertex
  positions are truncated to float24 on input; s8 / s16 positions are exactly value/128, value/32768
  `[measured by the PPSSPP team on a PSP]`. Consequence: a float vertex format buys no precision over s16 + a
  scale matrix.
- The adder has no guard bits: both operands are first cut to the precision of the larger one, then added exactly.
  Adding a small offset to a big value loses the offset's low bits (viewport centre, texture offset, fog end, the
  near clipper's z + w) `[measured by the PPSSPP team on a PSP]`.
- A matrix row (x*m0 + y*m1 + z*m2 + t) is ONE multi-operand sum: each product exact, every term truncated to the
  lsb of the largest term, summed exactly, truncated once. The GE combines (World*View)*Projection itself this way
  before transforming any vertex; dot products, the texture matrix and the vertex-to-light vector use the same
  unit `[measured by the PPSSPP team on a PSP]`.
  - Consequence `[derived]`: the error scales with the largest *term*, not the result. A vertex 2-5 units from the
    camera on a mesh spanning +-32 units can be off by ~0.1-0.3 px, on a +-128 unit mesh ~0.5-1 px, re-truncated
    every time the camera moves (near-camera shimmer). Camera-relative matrices are necessary; small per-draw model
    coordinates (re-centred meshes) help further.
  - A translation in one matrix and its negation in the next cancel exactly inside the combined matrix
    `[public: PPSSPP GE docs, vertex pipeline]`.
- There is no divider. z/w, perspective UVs, the near clipper's interpolation factor and point-light attenuation use
  a 128-segment linear reciprocal table (not the VFPU's vrcp); normalisation uses a reciprocal square root with the
  same layout; triangle setup uses a third, finer 256-segment table. A zero vector stays zero
  `[measured by the PPSSPP team on a PSP]`.

## Viewport, cull and clip

- Screen X/Y after the viewport are 12.4 fixed point (floor(X*16)), screen Z is floored to 16 bits. The usable
  window is 0..4096 in X and Y; the scene is normally centred at 2048,2048 `[public: PPSSPP GE docs, vertex
  pipeline]` `[public: pspsdk src/gu/pspgu.h, sceGuOffset]`.
- A vertex is "outside" when |X|, |Y| or |Z| > W (compared in float24, no divide). A primitive is culled only when
  all its vertices are outside the SAME plane, any primitive type, clipping on or off. There are no X/Y clip
  planes `[measured by the PPSSPP team on a PSP]`.
- Any vertex with screen X or Y outside 0..4096 discards the whole primitive (vertices removed by the near clip
  do not count) `[measured by the PPSSPP team on a PSP]`. This is the guard band: keep geometry inside it, split
  or CPU-clip the rest.
- The only clip plane is the near plane Z = -W, applied before the viewport and only with clipping enabled
  (`GU_CLIP_PLANES`). A vertex exactly on it is not clipped (its XY must still be inside 0..4096). Sprites
  (RECTANGLES) are never clipped: one with W < 0 is discarded `[measured by the PPSSPP team on a PSP]`.
- Nothing is clipped at the far plane: geometry beyond Z/W = 1 is rasterised and only the per-pixel MinZ/MaxZ test
  removes it `[measured by the PPSSPP team on a PSP]`.
- Clipping ON: each vertex's screen Z is clamped to 0..65535 before interpolation, whatever MinZ/MaxZ are.
  Clipping OFF: a vertex with screen Z < 0 or >= 65536, or (triangles/lines) W <= 0, discards the primitive, and
  there is no near clip `[measured by the PPSSPP team on a PSP]`.
  - Trap `[derived]` from pspsdk's `sceGuDepthRange` source (scale = (near+far)/2 - near, centre = (near+far)/2):
    the common `sceGuDepthRange(65535, 0)` maps z/w = +1 to screen Z = -1. Harmless with clipping on (clamped to
    0); with clipping off everything at the far plane is discarded. Keep `GU_CLIP_PLANES` on for transform-mode
    draws.
- The "tall triangle" quirk, exactly: when a triangle's long edge has 3*dy > 2^17 subpixels (dy >= 43691 subpixels,
  ~2730.7 px), its rows are drawn in 4-pixel spans and the first pixel of each span along that edge lights if any
  pixel of the span is inside: extra-pixel spikes at any slope; the short edges stay exact
  `[measured by the PPSSPP team on a PSP]`. Keep projected Y spans below ~2730 px (a margin of one subpixel is
  enough).

## Depth

- Depth is an affine plane per triangle in screen space, never z/w per pixel: gradients floored to 14 fraction bits
  per subpixel, anchored at one vertex, value floored at the pixel centre, clamped to 0 below
  `[measured by the PPSSPP team on a PSP]`.
- The depth buffer is 16-bit integer only `[public: PPSSPP GE docs, overview]`. One depth code at view distance Z is
  about Z^2 * (f - n) / (65536 * f * n) world units, i.e. ~Z^2 / (65536 * near) once far >> near `[derived]`.
  Examples with near 0.1: 0.06 units at Z = 20, 1.5 at 100, 6 at 200; near 0.5 is 5x finer everywhere. The float24
  arithmetic adds roughly +-1-3 codes on top `[derived]`.
- Reversing the range (65535..0, GEQUAL) does not add code density to an integer z/w buffer; it saves at most about
  one code of truncation at the far end `[derived]`. The lever is the near plane, then split depth ranges
  ([rendering.md](rendering.md), "Depth").

## Gouraud colour, fog, texture coordinates

- Gouraud colour and fog are interpolated screen-linearly (no perspective correction), unlike a PC GPU. Fog is
  computed per vertex, quantised to 8 bits (min(floor(256f), 255)), clamped per vertex, then interpolated: a big
  triangle spanning the fog range gets a ramp, not a plateau. No fog in through mode
  `[measured by the PPSSPP team on a PSP]`.
- Transform-mode texture coordinates: per vertex q = R(w), s = u*q, t = v*q. The three vertices' s, t and q are each
  scaled to the largest exponent of the three and truncated to **15-bit integers**, then interpolated; per pixel
  u = s * R(q) `[measured by the PPSSPP team on a PSP]`. A large |UV| or a large w ratio inside one triangle eats
  those 15 bits.
  - Estimates `[derived]`, error at the far vertex in texels of a 128-texel texture, for |u| = 1 / 4 / 8 tiles:
    w ratio 20 -> 0.03 / 0.12 / 0.23; ratio 100 -> 0.7 / 2.6 / 5.2; a near-clipped ground triangle at near 0.1
    reaching 20 units (ratio 200) -> 0.5 / 2 / 4. That is device-only texture swimming.
  - Levers: a larger near plane, small UVs (do not add an integer tile offset back through TexOffset on REPEAT
    textures), split near-camera triangles with a big w ratio.
- UV generation mode 0: u' = add(u*scale, offset); u8/u16 coordinates are unsigned, divided by 128 / 32768. Mode 1
  is the texture matrix (source: position, UV, normal or normalised normal); mode 2 is shade mapping
  `[public: PPSSPP GE docs, vertex pipeline]`.
- A draw whose vertex format has no UVs (or no normal) uses the last ones the GE read, from any earlier draw, even
  across lists `[public: PPSSPP GE docs, vertex pipeline]`.

## Coverage and sampling

- Pixel centres at +0.5 (8/16); exact edge test; **top-left fill rule**; zero-area triangles light nothing (both
  modes) `[measured by the PPSSPP team on a PSP]`. The fill rule only arbitrates an edge that is bit-identical in both
  primitives; it does not close gaps between edges that merely nearly coincide (see seams in
  [rendering.md](rendering.md)).
- Rows are walked one at a time, never in tiles or quads; sprites top to bottom, left to right
  `[public: PPSSPP GE docs, raster pipeline]`.
- Sprites: two vertices; colour and depth come from the second vertex `[public: PPSSPP GE docs, vertex pipeline]`
  `[public: PPSSPP source, GPU/Software/RasterizerRectangle.cpp]`. UVs come from planes through three corners; a
  sprite whose vertices go bottom-left -> top-right is drawn with its UVs rotated `[public: PPSSPP GE docs, raster
  pipeline]`.
- Every primitive has its own planes: two triangles and one sprite over the same rectangle sample different texel
  rows. A 1:1 bilinear sprite hits texel centres only if its AREA is a power of two, otherwise it blurs. Use
  NEAREST for 1:1 copies `[measured by the PPSSPP team on a PSP]`.
- Texel position is in 1/16 texel, truncated toward zero. Bilinear: 4-bit weights, two horizontal lerps truncated to
  8 bits, then the vertical one. 16-bit texels and CLUT entries are widened to 8 bits per channel first
  `[measured by the PPSSPP team on a PSP]`.
- Mip LOD in 1/16 levels from a float-bits log2; AUTO uses the largest of the s/t plane gradients over q, so the
  selection is isotropic (no anisotropic filtering exists). One q per 4-pixel span (the span's 2nd pixel in walk
  order), not per 2x2 quad. The bias applies in all modes. Mip-linear blends D>>4 and the next level by D&15;
  mip-nearest takes (D+8)>>4. An unsampled level is never read `[public: PPSSPP GE docs, raster pipeline]`.

## Pixel operations

- Blending: each term ((2c+1)(2f+1))>>10, one clamp at the end; a fixed factor of 255 is an exact x1. The doubled
  inverse factors are not clamped, so with alpha >= 128 their term subtracts. Min / max / absdiff use raw colours
  `[measured by the PPSSPP team on a PSP]`.
- Texture function MODULATE: (t*(c+1))>>8; fragment alpha with texture alpha ((a+1)*a_tex)>>8; colour doubling also
  doubles the specular colour `[measured by the PPSSPP team on a PSP]`.
- Dithering, 565/5551/4444 packing, alpha/colour test, logic ops, write masks, clear mode: PPSSPP's existing model
  was confirmed exact; stencil compares (ref & mask) against (stencil & mask) `[measured by the PPSSPP team on a
  PSP]`. Stencil lives in the colour buffer's alpha bits (1 bit in 5551, none in 565) `[public: PPSSPP GE docs,
  overview]`.

## Lighting

- Lighting happens in world space. Normals go through the WORLD matrix itself (no inverse transpose) and are never
  normalised, but N.L, N.H and shade-mapping dots are divided by |N|. So a uniform scale in WORLD is harmless; shear
  or non-uniform scale distorts lighting `[measured by the PPSSPP team on a PSP]`.
- The viewer direction is V = normalize(third column of VIEW): view-space +z must face the viewer, or specular and
  shade mapping come out mirrored `[measured by the PPSSPP team on a PSP]`.
- Specular / spot pow is Mitchell's approximation with the exponent cut to 4 mantissa bits: highlights tighter and
  up to 10-30 steps darker than a true pow `[measured by the PPSSPP team on a PSP]`.
- Light x material and every factor use ((2x+1)(2s+1))>>10 with s = floor(256f), so a white light reproduces a
  colour exactly; attenuation then spot are applied one after the other `[measured by the PPSSPP team on a PSP]`.
- Skinning: weight x bone matrix, accumulated sequentially in float24; weights are NOT normalised (u8: 1.0 = 128;
  u16: 1.0 = 32768). Make weights sum exactly to that `[measured by the PPSSPP team on a PSP]`. pspsdk exposes 8
  bone matrices (`sceGuBoneMatrix` index 0..7) `[public: pspsdk src/gu/pspgu.h]`.

## Memory side

- A pixel past the frame-buffer stride is not clipped; it wraps to the start of the next row. Scissor every render
  target to its own size `[measured by the PPSSPP team on a PSP]`.
- REGION1 translates as well as clips; pspsdk's `sceGuScissor` writes REGION1 = 0, and PPSSPP ignores REGION1
  `[public: PPSSPP GE docs, raster pipeline]` `[public: pspsdk src/gu/sceGuScissor.c]`.
- The 8 KB texture cache is coherent with nothing: only TEXFLUSH empties it (pspsdk's `sceGuTexImage` emits one).
  A texture that fits stays cached across draws until the next flush. After a GE block transfer into a texture,
  TEXSYNC before sampling it `[measured by the PPSSPP team on a PSP]` `[public: pspsdk src/gu/sceGuTexImage.c]`.
- Texturing from the buffer being drawn reads mostly pre-primitive data, except lines loaded lazily during the
  primitive. Copy the buffer to a scratch texture first `[measured by the PPSSPP team on a PSP]`.
- The depth buffer is stored scrambled; the VRAM mirrors at 0x04200000 (16-bit colour target) and 0x04600000
  (32-bit colour target) show it linear, e.g. to texture from depth `[measured by the PPSSPP team on a PSP]`.
- Display lists: CALL nests only two levels; a third-level CALL is silently skipped and the list goes on
  `[measured by the PPSSPP team on a PSP]` `[public: PPSSPP GE docs, display list control]`.

## Which PPSSPP to trust for what (status as of 2026-10-15)

- The bit-exact software renderer is PPSSPP PR hrydgard/ppsspp#22414 ("SoftGPU: Make it mostly bit exact vs PSP
  hardware"), **merged into master on 2026-10-05, milestone v1.21**. The newest release on 2026-10-15 is still
  v1.20.4 (2026-05-16), so you need a development build from master (or v1.21 once released)
  `[public: github.com/hrydgard/ppsspp PR 22414 + releases]`.
- Only the SOFTWARE renderer changed. The hardware backends (D3D11, OpenGL, Vulkan) still use host-float T&L, host
  depth and host filtering: for them, keep treating PPSSPP as a boolean oracle (does it draw, does it crash), never
  as a pixel or depth reference `[public: PR hrydgard/ppsspp#22414 file list]`.
- The 1.20.x software renderer is close but not exact: an indication only.
- Not modelled even by the new renderer: reads through the swizzled depth mirrors, the lazy 8-row self-texture
  loads, REGION1, and anything about time (fill rate, GE cost, CPU-GE races) `[public: PPSSPP blog, ge-bit-exact]`.
  Timing and concurrency stay hardware-only questions.

## Replaying a GE frame on PC and on the console

1. Record a GE frame dump (`.ppdmp`) of the frame in question from PPSSPP's GE debugger.
2. PC: replay it in `PPSSPPHeadless --graphics=software` (a build containing PR 22414) and save the screenshot
   (`--screenshot-save=FILE`; current master can also save the depth buffer, see `headless/Headless.cpp`)
   `[public: PPSSPP source, headless/Headless.cpp]`.
3. Console: pspautotests `utils/ppdmp-playback` runs the same dump on a real PSP over PSPLink (`usbhostfs_pc` +
   `pspsh -e playback.prx host0:/x.ppdmp`) and captures the screen; `run.py --headless` renders both and prints the
   MSE `[public: pspautotests utils/ppdmp-playback/README.md]`.
   - The whole decompressed dump must fit in PSP RAM; the replay is not a speed measurement
     `[public: pspautotests utils/ppdmp-playback/README.md]`.
   - One job at a time: a too-big readback or two jobs at once wedges the PSP until a manual reset
     `[public: PPSSPP blog, ge-bit-exact]`.
4. Regression tests from those probes live in `pspautotests/tests/gpu/exact`.

This gives a device verdict for one frame without deploying your program, and a numeric PC preview of z-fighting,
cracks, UV swimming, fog banding and dithering. It does not measure GE time; for that see
[optimization-profiling.md](optimization-profiling.md).

## Sources

- PPSSPP GE documentation: https://www.ppsspp.org/docs/psp-hardware/gpu/ (pages: ge-overview, arithmetic,
  ge-vertex-pipeline, raster-pipeline, lighting, texture-cache, image-formats, display-lists)
- PPSSPP blog "Cracking the PSP GPU's transform and raster" (2026-10-02): https://www.ppsspp.org/news/ge-bit-exact/
- PPSSPP source and PR 22414: https://github.com/hrydgard/ppsspp (`GPU/Software/GEMath.cpp`,
  `GPU/Software/RasterizerRectangle.cpp`, `headless/Headless.cpp`), https://github.com/hrydgard/ppsspp/pull/22414
- pspautotests: https://github.com/hrydgard/pspautotests (`pspautotests/utils/ppdmp-playback`, `pspautotests/tests/gpu/exact`)
- pspsdk: https://github.com/pspdev/pspsdk (`src/gu/pspgu.h`, `src/gu/sceGuDepthRange.c`,
  `src/gu/sceGuScissor.c`, `src/gu/sceGuTexImage.c`)
