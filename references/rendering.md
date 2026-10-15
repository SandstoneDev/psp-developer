# Rendering: sceGu / sceGum, framebuffer, depth, display lists, big worlds

The GE is driven through pspsdk's `sceGu*` (state + display lists) and `sceGum*` (a matrix stack on the VFPU). You
build a display list per frame and the GE consumes it asynchronously. Bit-level rules of the GE (float24, planes,
clip/cull, sampling) are in [ge-arithmetic.md](ge-arithmetic.md); textures and UVs in
[textures-uv.md](textures-uv.md); measuring the GE in [optimization-profiling.md](optimization-profiling.md).

Golden rule: PPSSPP's hardware backends answer booleans (does it draw, does it hang), never pixels or timings. Most
traps below were invisible in the emulator and cost a hardware run each.

## Init essentials

A minimal 3D init, adapted from pspsdk's `samples/gu/cube` (which uses an 8888 target)
`[public: pspsdk src/samples/gu/cube/cube.c]`:

```c
sceGuInit();
sceGuStart(GU_DIRECT, list);
sceGuDrawBuffer(GU_PSM_5650, fbp0, 512);          /* draw buffer, stride 512 px */
sceGuDispBuffer(480, 272, fbp1, 512);             /* display buffer             */
sceGuDepthBuffer(zbp, 512);                       /* 16-bit depth               */
sceGuOffset(2048 - 480/2, 2048 - 272/2);
sceGuViewport(2048, 2048, 480, 272);
sceGuDepthRange(65535, 0);
sceGuScissor(0, 0, 480, 272);                     /* x, y, WIDTH, HEIGHT        */
sceGuEnable(GU_SCISSOR_TEST);
sceGuDepthFunc(GU_GEQUAL);
sceGuEnable(GU_DEPTH_TEST);
sceGuFrontFace(GU_CW);
sceGuEnable(GU_CULL_FACE);
sceGuEnable(GU_CLIP_PLANES);
sceGuFinish();
sceGuSync(GU_SYNC_FINISH, GU_SYNC_WHAT_DONE);
sceDisplayWaitVblankStart();
sceGuDisplay(GU_TRUE);
```

- **Framebuffer format**: the GE draws 5650, 5551, 4444 or 8888; depth is always 16-bit `[public: PPSSPP GE docs,
  overview]`. A 16-bit colour target halves frame-buffer traffic versus 8888 and the GE is usually bandwidth/fill
  bound `[practice]`. Stencil lives in the colour alpha bits: 1 bit in 5551, 4 in 4444, none in 5650
  `[public: PPSSPP GE docs, overview]`.
- **Double buffering**: draw into one buffer, display the other, `sceGuSwapBuffers()` after the frame's
  `sceGuFinish` + `sceGuSync`. Two 16-bit 512x272 colour buffers + one depth buffer = ~816 KB of VRAM `[derived]`.
- **Back-face culling** on with a consistent winding: it roughly halves fill on closed meshes `[practice]`. Which
  winding is "front" depends on your projection and handedness; check it once with a mesh whose orientation you
  know (the ground must not vanish), then keep a run-time flag to flip it. Turn culling off only per submesh for
  two-sided cutouts / glass.
- **Scissor** every render target to its own size: a pixel past the stride wraps into the next row instead of being
  clipped `[measured by the PPSSPP team on a PSP]`.

## Depth: 16-bit integer, the near plane is the lever

pspsdk's default (`sceGuDepthRange(65535, 0)` + `GU_GEQUAL`, near maps to 65535) is fine, but do not expect it to
"reclaim far precision" the way reversed-Z does with a float depth buffer. The PSP depth buffer is a 16-bit
*integer* z/w through the viewport, so reversing the range changes no code density; it keeps at most ~1 code of
truncation at the far end `[derived]`.

- One code is ~Z^2 / (65536 * near) world units once far >> near `[derived]`: near 0.1 -> 0.06 units at 20, 1.5 at
  100, 6 at 200; near 0.5 is 5x finer everywhere. The far plane barely matters. The GE's float24 arithmetic adds
  ~+-1-3 codes `[derived]` (model in [ge-arithmetic.md](ge-arithmetic.md)).
- **Lever 1: the near plane.** Make it as large as the scene allows, ideally adaptive (e.g. half the distance of the
  nearest thing drawn last frame, clamped). The same factor also improves perspective-UV precision.
- **Lever 2: split ranges.** Far geometry with its own projection into Z 0..32767 (`sceGuDepthRange(32767, 0)`),
  near geometry into 32768..65535 (`sceGuDepthRange(65535, 32768)`). `sceGuDepthRange` also sets MinZ/MaxZ
  `[public: pspsdk src/gu/sceGuDepthRange.c]` and the GE rejects pixels outside them per pixel, clips only at each
  projection's near plane and never at the far plane `[measured by the PPSSPP team on a PSP]`. One depth clear serves
  both; a mesh straddling the split is drawn in both passes with slightly overlapping ranges `[derived]`.
- **Keep `GU_CLIP_PLANES` on** for transform-mode draws: with (65535, 0) the far plane maps to screen Z = -1, which
  is clamped with clipping on but discards the primitive with clipping off `[derived]`.
- Decals (shadows, skid marks) on coplanar surfaces: bias the decal draw by a few depth codes with
  `sceGuDepthOffset` rather than offsetting geometry in world units `[practice]`.
- Equal depth with GEQUAL: the last drawn wins. Draw a coarse backdrop (far LOD) first so detail overwrites it
  `[practice]`.
- Depth writes need the depth test on: for "write Z but always pass", use test ALWAYS, not test disabled
  `[practice]`.

## Opaque and alpha passes

Draw opaque first (depth write on), then alpha (depth write off) so transparency composites over finished depth.
Decide each submesh's class offline from its texture's alpha `[practice]`:

- opaque: no blend, no alpha test, cull on;
- cutout (foliage, fences, hard 0/1 alpha): `GU_ALPHA_TEST` (e.g. GREATER 0x40), no blend, cull off;
- translucent (glass): `GU_BLEND` SRC_ALPHA / ONE_MINUS_SRC_ALPHA, no alpha test, cull off, drawn LAST with
  `sceGuDepthMask(GU_TRUE)`. Glass drawn in material order writes depth before the interior behind it, which then
  fails the depth test: you see the background through the window `[measured: PSP-3000, large 3D game port]`.
- soft-edged alpha that must still sort itself: two passes over the same mesh, alpha >= 0x80 with depth write, then
  0 < alpha < 0x80 without. Fragment alpha is deterministic, so the two passes are exactly complementary
  `[measured by the PPSSPP team on a PSP]` `[measured: PSP-3000, recompiled game]`.

`GU_TFX_MODULATE` with `GU_TCC_RGBA` multiplies texture x vertex colour: keeps baked lighting / AO; REPLACE looks
flat `[practice]`.

## Vertex formats

- Interleaved, fixed component order (weights, UV, colour, normal, position), each component aligned to its own
  size `[public: PPSSPP GE docs, overview]` `[public: pspsdk src/gu/pspgu.h]`. A compact world format:
  `GU_TEXTURE_16BIT | GU_COLOR_8888 (or 5551) | GU_VERTEX_16BIT | GU_TRANSFORM_3D`.
- **s8 / s16 positions are normalised**: the GE uses value/128, value/32768 `[public: PPSSPP GE docs, vertex
  pipeline]`. Raw world-unit s16 vertices without a scale matrix collapse the scene into a speck inside the near
  plane: blank screen from every angle, both windings, every backend `[measured: PSP-3000, streaming map engine]`.
  Quantise about the mesh centre and put `scale * 32768` (and the centre) into the model matrix:
  `sceGumTranslate(&centre); sceGumScale(&(ScePspFVector3){ s * 32768, s * 32768, s * 32768 });`.
- A scale matrix scales normals too. Lighting and shade mapping divide by |N|, so a UNIFORM scale is harmless there
  `[measured by the PPSSPP team on a PSP]`; texture-matrix mapping from `GU_NORMAL` (un-normalised) is scaled, use
  `GU_NORMALIZED_NORMAL` `[public: pspsdk src/gu/pspgu.h]` `[derived]`. Non-uniform scale or shear in WORLD
  distorts lighting.
- **Vertex colour is 0xAABBGGRR**: R in the LOW byte, i.e. `r | g<<8 | b<<16 | a<<24`, which is what pspsdk's
  `GU_ABGR` / `GU_RGBA` macros build `[public: pspsdk src/gu/pspgu.h]` `[public: PPSSPP GE docs, image formats]`. A
  baker packing `(r<<24)|(g<<16)|(b<<8)|a` is mirrored, and the symptom is not a hue shift: red is read from the
  alpha byte (dark models) and alpha from the red byte (alpha-test stripes and holes)
  `[measured: PSP-3000, large 3D game port]`. It hid for a long time because every model checked was all-white,
  and **white is a palindrome**. Any format check against default data (white colours, an identity matrix, a zero
  offset, a one-element array) checks nothing: test with asymmetric content.
- A prelit colour's 4th byte imported from another platform is not necessarily coverage; force alpha opaque
  instead of feeding it to the alpha test unless you know what it means `[practice]`.
- Skin weights must sum exactly to 128 (u8) or 32768 (u16): the GE does not normalise `[measured by the PPSSPP team
  on a PSP]`.

## Display lists: lifetime, cache, kicks, guards

The GE runs your list later, on its own, reading memory behind the CPU's data cache. On the console the CPU runs a
whole frame ahead; PPSSPP executes nearly synchronously, so every rule here is hardware-only.

- **Anything referenced by pointer must stay untouched until the GE has run it.** `sceGuDrawArray` stores only the
  vertex/index address in the list `[public: pspsdk src/gu/sceGuDrawArray.c]`. A CPU-skinning path that writes every
  character into one shared static buffer draws them all with the LAST pose, on hardware only
  `[measured: PSP-3000, large 3D game port]`. Use per-instance (or per-frame double-buffered) vertex memory, or the
  GE's hardware skinning: bone matrices travel by value in the list and cannot alias.
- **Data cache**: `sceKernelDcacheWritebackRange(p, n)` on vertices / indices / textures / CLUTs written through
  the cache before the list that uses them runs `[public: pspsdk src/user/psputils.h]`. A loader thread publishes a
  pointer only after writeback + a barrier (`__sync_synchronize()`). Never mix cached and uncached writes in one
  64-byte line `[measured: PSP-3000, recompiled game]`.
- **VRAM-as-texture-cache**: overwriting a VRAM slot the GE may still be sampling for the previous frame is a
  read/write race; double-buffer the slot or sync with the GE first `[measured: PSP-3000, large 3D game port]`.
- **Static geometry as sub-lists**: build each static mesh once into its own list (`sceGuStart(GU_CALL, buf)`, draws,
  `sceGuFinish()`) and `sceGuCallList()` it per frame `[public: pspsdk src/gu/pspgu.h]`. CALL nests at most two
  levels; a deeper CALL is silently skipped `[public: PPSSPP GE docs, display list control]`. Key such caches by
  content (sizes, counts, a hash), not by an object's address: a reused address otherwise draws stale geometry
  `[measured: PSP-3000, recompiled game]`.
- **Kick in batches**: in `GU_DIRECT` mode every `sceGuDrawArray` / `sceGuCallList` updates the stall address
  `[public: pspsdk src/gu/sceGuDrawArray.c]`, a system call. Writing CALL words into the open list yourself and
  updating the stall address every 16 meshes cut kicks from ~151,000 to ~27,000 per run
  `[measured: PSP-3000, recompiled game]` (counted in PPSSPP).
- **Reset state at each list start**: GE state persists across lists. Any pass that changes cull, depth, blend,
  texture scale etc. must restore it, or the next pass inherits it `[measured: PSP-3000, recompiled game]`.
- **Guards**: a malformed list hangs a retail console with no error screen. Range-check primitive counts and every
  address, keep CALL depth <= 2, and keep a run-time switch to fall back to a simpler path. If you emit raw GE
  words yourself, compare them at start-up against what pspsdk's `sceGu*` functions emit for the same state and
  disable your encoder on a mismatch `[measured: PSP-3000, recompiled game]`.
- Measured cost of a good path: ~1.4 ms of GE time per frame for ~10,000 triangles `[measured: PSP-3000, recompiled
  game]`. CPU per mesh was 55 us on the console against 13.5 us in PPSSPP (the converter evicted the caches)
  `[measured: PSP-3000, recompiled game]`.

## Culling and the guard band (no X/Y clipping)

- The GE clips only at the near plane. A primitive with any vertex outside the 0..4096 screen window is discarded
  WHOLE `[measured by the PPSSPP team on a PSP]`. With the viewport centred at 2048 you have ~2048 px of guard band
  each side; a triangle taller than ~2730 px also misrenders (extra pixels) `[measured by the PPSSPP team on a PSP]`.
- So big triangles near the camera vanish. Fix by tessellation (cap edge length) plus CPU clipping only for what
  still straddles the guard band `[practice]`.
- Derive the cap `[derived]`: with f = (screen_h / 2) / tan(fovy / 2) px, an edge at depth d spans ~edge * f / d px;
  the safe threshold edge/d is 2048 / f (pessimistic) up to 4096 / f. fovy 43 deg: f = 345, 5.9..11.9; fovy 70 deg:
  f = 194, 10.5..21.
  - The `near` in `cap = near x threshold` is the FLOOR of an adaptive near, not the nominal one: tessellation and
    the near plane are two halves of one setting. A port of a large 3D game ended at `cap = near x 7` (fovy 70)
    after hardware calibration `[measured: PSP-3000, large 3D game port]`.
- A cheap CPU pre-cull per instance (cone / box test + distance band) and per grid cell before iterating its
  instances; collect, sort near-first, draw `[practice]`. A box classifier: every corner outside one plane -> skip;
  every corner inside the guard band and in front of the near plane -> GE; else CPU path
  `[measured: PSP-3000, recompiled game]`.
- Make the CPU clip path watertight: emit clipped triangles in the mesh's model space through the same matrix the
  GE path uses, and compute each edge intersection in a canonical vertex order. Otherwise a vertex shared by a
  CPU-clipped and a GE-drawn triangle goes through different arithmetic and dotted cracks appear `[derived]`.

## Big worlds: camera-relative rendering (the "shaking" bug)

On a large map (coordinates in the thousands) everything trembles while the camera moves. Cause: matrices reach the
GE as float24 (15 fraction bits) and the whole transform runs in float24 `[public: PPSSPP GE docs, arithmetic]`. At
|T| ~ 3000 one ulp is 2048 * 2^-15 = 0.0625 units `[derived]`: the view translation re-rounds onto a different
1/16-unit grid point every frame and every vertex swims with it. Worse far from the origin, only while the camera
translates. PPSSPP's hardware backends transform in full float and hide it.

**Fix: park the world origin at the camera.**
- View matrix = rotation only.
- Each model matrix subtracts the camera position on the CPU in float32 before `sceGumLoadMatrix`
  (`model.w -= cam`).
- Coverage must be TOTAL: every path that submits world-space geometry (world chunks, characters, vehicles, props,
  sky, markers). A path left on absolute coordinates renders offset by the camera position. Keep a switch that zeroes
  the rebase for A/B `[measured: PSP-3000, large 3D game port]`.
- **Paths with world-space vertices** (decals, blob shadows, particles, water): an identity-with-(-camera)
  translation matrix still multiplies big vertices by a big translation, so near-camera decals keep jittering while
  the world is stable. Subtract the camera from the VERTICES on the CPU at emit time and load a true identity.
  Finding the stragglers: grep for `sceGumLoadIdentity` on the model matrix `[measured: PSP-3000, large 3D game
  port]`. Do not convert something already camera-relative (a billboard built as camera + offset): that shifts it
  twice.
- **Camera-relative is now a coordinate space.** Feeding an absolute target into anything computed in it (IK, attach
  points) throws the result thousands of units away; on screen an IK arm then looks like it is STRETCHING, which
  reads as a length bug. "Wrong length" and "wrong place" look identical: measure the bone lengths offline before
  tuning a solver (a rotation-only solver cannot stretch) `[measured: PSP-3000, large 3D game port]`. Rebase on the
  way in, un-rebase on the way out, and say so at the function.
- Even camera-relative, the error scales with the largest term of each matrix row, so meshes spanning hundreds of
  units can shimmer by a fraction of a pixel near the camera; re-centre such meshes if it shows `[derived]`.

## Seams between separately drawn meshes: a binary defect

Thin flickering gaps along boundaries between adjacent tiles/models, worse on hardware, never inside one model.
Inside a model two triangles share one vertex (same bytes, same matrix), so their edges are bit-identical. Two
models each have their own copy of the boundary vertex and their own matrix, truncated differently.

- Screen positions are 12.4 fixed point `[public: PPSSPP GE docs, vertex pipeline]`, so a disagreement either lands
  both copies in the same 1/16-px cell or not: a small error gives a RARE gap (a dotted line along a long edge), not
  a small one. Ask "is the error non-zero", never "is it big enough to see" `[derived]`.
- The GE does have a top-left fill rule `[measured by the PPSSPP team on a PSP]`, but it only arbitrates edges that
  are identical after the viewport; two nearly coincident edges are not adjudicated.
- What a port of a large 3D game measured `[measured: PSP-3000, large 3D game port]`:
  - requantising all world vertices onto one shared lattice did NOT close the seams (the boundary data was already
    clean; test the premise before a re-bake);
  - one shared matrix per tile would cover under half the instances (most are rotated) and coarsen quantisation;
  - widening polygons to overlap in plane is worse (z-fighting);
  - what works: a **skirt strip**, a narrow strip dropped down/behind each boundary edge: not coplanar, invisible from the
    front, hides the gap.
- A shared per-frame origin (the camera) is necessary (each tile rebasing to its own origin guarantees different
  arithmetic) but not sufficient. Keep vertex fog/lighting on the same origin, or the seam shows as a colour line
  `[practice]`.

## Sprites, points, 2D

- **GU_SPRITES** draws an axis-aligned screen rectangle from 2 vertices `[public: pspsdk src/gu/pspgu.h]`. Colour
  and depth come from the SECOND vertex (flat, no Gouraud); UVs map corner to corner and are rotated if the
  vertices go bottom-left -> top-right `[public: PPSSPP GE docs, raster pipeline]` `[public: PPSSPP source,
  GPU/Software/RasterizerRectangle.cpp]`. Works in 3D: the rectangle is formed after projection, so a camera-facing
  particle/glow is one sprite instead of two triangles (1/3 of the vertices) `[derived]`. Never near-clipped: a
  sprite with a vertex behind the camera is dropped whole `[measured by the PPSSPP team on a PSP]`. Fine for world
  particles, wrong for screen-covering quads and ground decals (not screen-aligned: use triangles).
- `GU_POINTS` lights exactly one pixel, no size `[public: PPSSPP blog, ge-bit-exact]`: useless for glows.
- **Through mode (`GU_TRANSFORM_2D`)**: no transform, lighting, fog or clipping; coordinates are screen pixels
  `[public: PPSSPP GE docs, vertex pipeline]`. The depth test and depth write still work
  `[public: PPSSPP GE docs, raster pipeline]`, so depth-gated fullscreen passes (e.g. multiply only where world was
  drawn) are legal. **Back-face culling still applies**: a fullscreen post-FX quad late in the frame inherited a
  front-face setting from skinned characters and was culled silently (drawn per the log, zero pixels changed); the
  same quad earlier in the frame worked by luck `[measured: PSP-3000, large 3D game port]`. Every 2D pass:
  `sceGuDisable(GU_CULL_FACE)` (and restore).
- `sceGuScissor(x, y, w, h)` takes WIDTH and HEIGHT, not x1/y1 `[public: pspsdk src/gu/sceGuScissor.c]`. Passing a
  bottom edge as the height silently enlarges the clip window and content "leaks" past its band; it reads as a
  rendering bug `[measured: PSP-3000, large 3D game port]`.
- 2D blits: draw wide images as vertical strips of 32-64 px; pspsdk's `samples/gu/speed` and `samples/gu/blit`
  show the technique `[public: PPSSPP GE docs, overview]` `[public: pspsdk src/samples/gu/speed/speed.c]`. Use
  NEAREST for 1:1 copies (see [ge-arithmetic.md](ge-arithmetic.md), sampling).

## Post-processing and frame copies

- **Never sample the buffer you are drawing to.** The texture cache is not coherent with GE writes and lines load
  lazily during a primitive `[measured by the PPSSPP team on a PSP]`. Copy it first (`sceGuCopyImage` into scratch
  VRAM + `sceGuTexSync()` before sampling) `[public: pspsdk src/gu/pspgu.h]`.
- **A 5551 frame copy carries a garbage alpha bit** (usually 0). A pass that re-textures the captured frame with
  `GU_TCC_RGBA` gets fragment alpha 0 and an alpha blend then draws nothing, invisibly; REPLACE passes work, which
  hides the bug in sibling passes. Use `GU_TCC_RGB` for frame-capture redraws `[measured: PSP-3000, large 3D game
  port]`.
- **Dither speckles on additive glows**: on a 16-bit target a low-alpha additive gradient dithers into dark speckles
  on the console, invisible in PPSSPP's hardware backends. Disable `GU_DITHER` around additive glow/flare passes:
  banding inside an additive gradient is imperceptible, the speckles are not `[measured: PSP-3000, large 3D game
  port]`. (PPSSPP's software renderer models dithering exactly `[measured by the PPSSPP team on a PSP]`.)
- Glow/flare textures whose shape is stored in RGB with a fully opaque alpha wash out additively and show black
  squares when alpha-blended: rebake alpha = luminance, RGB = white, tint with vertex colour `[practice]`.
- Measured: a set of post-processing passes on the GE cost 1.27 ms per frame `[measured: PSP-3000, recompiled game]`.

## Baked vertex lighting and day/night

- Baked vertex colour (no GE light) is the cheapest world lighting; moving a baked world onto `sceGuLight` adds
  per-vertex, per-light work where the GE is already loaded `[practice]`. GE lighting rules: see
  [ge-arithmetic.md](ge-arithmetic.md), lighting.
- With baked colour, day -> night means rewriting vertex colours on the CPU (budget a fraction of resident meshes
  per frame, then dcache writeback) `[practice]`.
- A fullscreen multiply (DST_COLOR blend) is a cheap global darken but caps every pixel at the tint: baked emissive
  (lit windows, neon) can never outshine the night `[derived]`. Fade the multiply out at night and move the darkness
  into the vertex rewrite `[practice]`.
- Ship night colour as a sparse overlay: full second colour buffers were ~35% of the world's vertex bytes, while
  runs of only the emissive vertices are a few KB per tile `[measured: PSP-3000, large 3D game port]`.
- Objects lit by GE ambient do not follow a world darkened through vertex colours: drive their ambient from the
  same time-of-day curve or they glow at night `[measured: PSP-3000, large 3D game port]`.
- Seam-split vertices (duplicated at UV/colour seams) need normals welded by position before an env-map pass;
  diffuse hides the split, an env-map turns every seam into a hard line `[measured: PSP-3000, large 3D game port]`.

## Measuring the GE and bus contention

- Polling `sceGeDrawSync(1)` reports "drawing" whenever a `GU_DIRECT` list is open, i.e. during the CPU's whole
  render phase: it said 15-35 ms of GE time where serial replays of the same frames measured 6.4-12.7 ms
  `[measured: PSP-3000, recompiled game]`. Measure GE time by building one frame with `sceGuStart(GU_SEND, list)`,
  then `sceGuSendList` + `sceGuSync` with the CPU idle, cut by draw category. Details:
  [optimization-profiling.md](optimization-profiling.md).
- Texture fetches share the memory bus with a memory-bound CPU: halving 3D texture resolution cut real GE time from
  12.7 to 8.7 ms and the frame by 3.1 ms (95% CI -5.0..-2.0) `[measured: PSP-3000, recompiled game]`. See
  [textures-uv.md](textures-uv.md), "Texture fetch traffic".
- Moving CPU work to the Media Engine shortened the main core's render phase but the main core's other code got
  slower by about the same amount; shared-bus contention is the leading suspect, unproven
  `[measured: PSP-3000, recompiled game]`. Measure the whole frame, not the moved phase.

## Small traps

- `pspDebugScreenInitEx` clears its whole target buffer on every call: call it once, then per frame only
  `pspDebugScreenSetBase(front_buffer)` + `pspDebugScreenSetXY` `[measured: PSP-3000, large 3D game port]`.
- PPSSPP shows nothing for your homebrew? On some installs its D3D11 backend did not draw a homebrew GE stream
  (and did not show CPU writes to VRAM) while the software renderer did: switch the renderer before suspecting your
  list `[practice]`.
- Mirror / reflection passes reuse the camera code: include the view matrix in any cache keyed by camera state
  `[measured: PSP-3000, recompiled game]`.

## Sources

- pspsdk: https://github.com/pspdev/pspsdk (`src/gu/pspgu.h`, `src/gu/sceGuDrawArray.c`, `src/gu/sceGuScissor.c`,
  `src/gu/sceGuDepthRange.c`, `src/gu/sceGuTexImage.c`, `src/user/psputils.h`, `src/samples/gu/cube`,
  `src/samples/gu/speed`, `src/samples/gu/blit`)
- PPSSPP GE documentation: https://www.ppsspp.org/docs/psp-hardware/gpu/ (ge-overview, ge-vertex-pipeline,
  raster-pipeline, arithmetic, texture-cache, image-formats, display-lists)
- PPSSPP blog "Cracking the PSP GPU's transform and raster": https://www.ppsspp.org/news/ge-bit-exact/
- PPSSPP source: https://github.com/hrydgard/ppsspp (`GPU/Software/RasterizerRectangle.cpp`)
