# Textures and UVs: formats, swizzle, CLUT, mipmaps, the texture cache, the u16 UV window

Author textures offline (a Python script is fine) into the GE's native layout; do as little as possible at run time.
Sampling arithmetic (1/16-texel positions, 4-bit bilinear weights, LOD per 4-pixel span) is in
[ge-arithmetic.md](ge-arithmetic.md).

## Formats

- Texture formats: 5650, 5551, 4444, 8888, CLUT4 (T4), CLUT8 (T8), CLUT16/CLUT32 (an 8-bit lookup out of a wider
  texel via shift + mask), DXT1/3/5 `[public: pspsdk src/gu/pspgu.h]` `[public: PPSSPP GE docs, image formats]`.
- **Default for 3D: T8 (or T4) + an 8888 CLUT.** Half or a quarter of the bytes of a 16-bit texture, and the GE's
  cost is texels fetched (below). Quantise to a palette offline. The CLUT holds at most 256 entries; load it with
  `sceGuClutMode(GU_PSM_8888, 0, 0xff, 0)` + `sceGuClutLoad(256 / 8, clut)` (blocks of 8 entries, 16-byte aligned)
  `[public: pspsdk src/gu/pspgu.h]` `[public: PPSSPP GE docs, image formats]`.
- **DXT is slow on the PSP**: the texture cache stores DXT decoded as 8888, so a DXT1 texture takes 8x its size in
  the cache and DXT3/5 4x; the block data order is also reversed from the PC one `[public: PPSSPP GE docs, texture
  cache + image formats]`. Prefer T4/T8.
- Size limits: width and height powers of two, 1..512; buffer width (TBW) up to 1024; base address 16-byte aligned
  `[public: pspsdk src/gu/sceGuTexImage.c]`.
- The GE textures from main RAM as well as VRAM, at a slightly reduced rate; you do not have to stream textures into
  VRAM `[public: PPSSPP GE docs, overview]`.

## Swizzle

- The GE reads textures in blocks of **16 bytes x 8 rows**. A swizzled texture stores each block contiguously, so a
  cache line is one 128-byte read instead of eight 16-byte reads: that is why swizzling matters for speed
  `[public: PPSSPP GE docs, texture cache]`. Upload with `sceGuTexMode(psm, maxmips, 0, GU_TRUE)`.
- Block width in texels: 32-bit 4, 16-bit 8, T8 16, T4 32 `[public: PPSSPP GE docs, texture cache]`. The row
  stride in bytes must be a multiple of 16 or the image shears diagonally `[measured: PSP-3000, large 3D game
  port]`; the height should be a multiple of 8 `[derived]`. Power-of-two sizes from the block width up satisfy this.
- A reference swizzler is in pspsdk's samples (`swizzle_fast` in `samples/gu/blit/blit.c`)
  `[public: pspsdk src/samples/gu/blit/blit.c]`. It works in BYTES: pass width x bytes-per-texel.

## The 8 KB texture cache

`[public: PPSSPP GE docs, texture cache]`, measured on a PSP by the PPSSPP team:
- 64 lines of 16 bytes x 8 rows (= one swizzle block), 16 sets x 4 ways, least recently used first. A set's lines
  repeat every 64 x 32 texels for T8, 64 x 64 for T4, 32 x 32 for 16-bit, 32 x 16 for 32-bit.
- Lines are tagged by position (column, band) and mip level only, not by the texture's address or format. It is
  coherent with nothing: only TEXFLUSH empties it. pspsdk's `sceGuTexImage` emits a TEXFLUSH every time
  `[public: pspsdk src/gu/sceGuTexImage.c]`; if you write texture registers yourself, flush after changing textures
  or you sample the previous texture's bytes.
- The CLUT is separate; loading one does not touch the texture cache.

What it means `[derived]`: a T8 64x64 texture is 4 KB (half the cache), a T8 128x128 is 16 KB (twice the cache), so
a big minified texture misses within a single triangle. Mip levels share the cache with level 0 (lines are tagged by
level), so a trilinear blend fetches lines of two levels.

## Texture fetch traffic is a frame cost

On the console the GE's texture reads compete with a memory-bound CPU for the bus. Measured on one recompiled 3D game
`[measured: PSP-3000, recompiled game]`:
- half-resolution 3D textures (128 px and up only, never HUD or fonts): frame time -3.1 ms (95% CI -5.0..-2.0), real
  GE time 12.7 -> 8.7 ms, texture working set 1,122 -> 484 KB per frame. Halving again gained nothing.
- full resolution + generated mipmaps: the same speed as half resolution (+0.08 ms, CI -0.49..+1.20) and sharper:
  the choice that shipped.
So cut texels *fetched*: mipmaps on anything that gets minified, sensible sizes, CLUT formats.

## Mipmaps

- Power-of-two levels, each uploaded with its own `sceGuTexImage(level, w, h, tbw, ptr)`; `sceGuTexMode(psm,
  maxmips, mc, swizzle)` takes the index of the last level (0..7), not the count `[public: pspsdk src/gu/pspgu.h]`. Set a
  mip filter (`GU_LINEAR_MIPMAP_LINEAR` or `GU_LINEAR_MIPMAP_NEAREST`) and `sceGuTexLevelMode(GU_TEXTURE_AUTO, bias)`.
- **Swizzle floor**: stop the chain at the swizzle block width: T8 16 px, T4 32 px wide
  `[measured: PSP-3000, recompiled game]`.
- Cheap generation that keeps palette indices: 2:1 point decimation (even texels of even rows) of the previous
  level, no requantising. Cost: texture bytes +31% at full resolution, conversion +28..57% per texture
  `[measured: PSP-3000, recompiled game]` (the bytes are exact counts, the conversion cost was measured in PPSSPP).
- An unsampled level is never read, so its pointer need not be valid `[public: PPSSPP GE docs, raster pipeline]`.

## No anisotropic filtering

The GE picks one LOD from the largest UV gradient, per 4-pixel span `[public: PPSSPP GE docs, raster pipeline]`: it
is isotropic. Tiled ground at a grazing angle aliases into streaks whatever you do. Mitigations `[practice]`:
- the mip chain;
- a positive bias on opaque surfaces (`sceGuTexLevelMode(GU_TEXTURE_AUTO, ~1.0..1.3f)`, bias applies in every mode
  `[public: PPSSPP GE docs, raster pipeline]`); keep cutout/alpha-test textures at neutral bias or foliage erodes;
- not over-tiling (content-aware cap below).
It never fully disappears: set expectations.

## GE texture coordinates are UNSIGNED: the u16 window and the stripes trap

The GE has no signed texture coordinates: 8-bit and 16-bit UVs are unsigned and divided by 128 and 32768, then
scale + offset are applied `[public: PPSSPP GE docs, overview + vertex pipeline]`. So a u16 UV is u1.15: 0..1.99997.
With `sceGuTexScale(8, 8)` (u16 storing u * 4096) the addressable window is **[0, 16) tiles**, wrapping mod 16
`[derived]`. A "-0.5" packs as a huge positive value near the top of the window.

- Consequence: a pack-time rebase that **zero-centres** UVs (subtract the midpoint) puts about half of every tiled
  surface's vertices "below zero", i.e. across the wrap. A triangle straddling it interpolates the LONG way round
  (almost 16 tiles across one triangle): dense regular stripes / moire on roads and walls. In a port of a large 3D
  game 31-41% of world triangles straddled `[measured: PSP-3000, large 3D game port]`. It looks like a filtering or
  swizzle bug; it is a UV-domain bug.
- **Fix at pack time, per submesh: min-floor, not centre.** `u -= floor(min(u))` so the span lands in [0, 16). Pick
  the rebase per axis; fract-wrap stray triangles. Verify after packing: every u >= 0, span <= 15.75 tiles, and no
  triangle's packed span exceeds its source span (catches seam straddles). Data-only fix `[practice]`.
- Diagnostic: stripes whose frequency follows the tiling factor on SOME triangles of a surface = seam straddle; a
  chevron pattern over the whole surface = range overflow `[measured: PSP-3000, large 3D game port]`.
- **Tiling range**: a submesh can span at most ~15.75 repeats. For ground tiling 10-24x:
  - cap the tiling content-aware at pack time: measure the texture's luminance standard deviation; flat textures
    (stddev below ~35 of 255, asphalt, ground) -> cap to ~1 tile (looks the same, kills aliasing); detailed ones
    (facades) keep their tiling `[measured: PSP-3000, large 3D game port]`;
  - or split triangles at tile boundaries when the art must tile wide `[practice]`.
- **Precision**: per triangle the GE keeps s = u*q, t and q as 15-bit values scaled to the largest of the three
  vertices `[measured by the PPSSPP team on a PSP]`. Big |UV| or a big w ratio inside one triangle costs whole texels
  at the far end. Keep UVs small; on REPEAT textures do not add the integer tile back through `sceGuTexOffset`.
- Float UVs are allowed (each component 4-byte aligned in the vertex) but cost 8 bytes per vertex and are truncated
  to float24 on the way in anyway `[public: PPSSPP GE docs, overview + arithmetic]`.

### Alternative: 8-bit UVs + per-submesh scale/offset

Store one unsigned byte per axis and let TexScale/TexOffset denormalise per submesh `[derived]` from the u8 = /128
rule:

```c
float s = (128.0f / 255.0f) * uv_range;    /* byte 255 -> uv_range                */
sceGuTexScale(s, s);
sceGuTexOffset(min_u, min_v);              /* byte B -> B * uv_range / 255 + min  */
```

Unbounded tiling and 2 bytes per vertex less than u16, but the step is `uv_range / 255`: up to R/510 of a tile of
error on a submesh tiling R times. Invisible under NEAREST, smeared under LINEAR on detailed surfaces tiling 10x+.
Use separate ranges per axis (one shared range lets the high-tiling axis ruin the other) `[practice]`.

## Through-mode UVs are raw texels

In `GU_TRANSFORM_2D`, UVs are texel coordinates and `sceGuTexScale` / `sceGuTexOffset` are ignored
`[public: pspsdk src/gu/pspgu.h, sceGuTexScale]` `[public: PPSSPP GE docs, raster pipeline]`. UVs of 0..1 sample a
single corner texel: a 2D sprite then renders as one flat (often transparent) colour and looks like "the draw is
invisible" `[measured: PSP-3000, large 3D game port]`. Write UVs as 0..w, 0..h of the bound texture.

## Textures larger than 512

The GE samples at most 512 x 512 per texture, but the buffer can be wider (TBW up to 1024)
`[public: pspsdk src/gu/sceGuTexImage.c]`. Draw a big image as 512 x 512 windows: point the base at the window's
first texel, keep TBW = the full stride, size = pow2 of the window, UVs relative to the window, one sprite per
window. Keep such textures unswizzled (linear): a swizzled layout is built for one buffer width `[practice]`.

## Binding, caching, alpha class

- `sceGuTexImage` + `sceGuClutLoad` per submesh is not free: remember the last bound texture and skip rebinding
  `[practice]`.
- Key any texture cache by a globally unique identity or content hash, never by a per-chunk index or an object
  address: index 5 in chunk A is not index 5 in chunk B, and a reused address returns the old texture
  `[measured: PSP-3000, recompiled game]`.
- **Carry the alpha class (opaque / cutout / translucent) with the texture** through every processing pass. Content
  matchers must be alpha-aware (hash RGBA over a neutral background; RGB under alpha 0 is codec garbage), and a
  match must never change the class: cutout wires re-classed opaque render as solid sheets
  `[measured: PSP-3000, large 3D game port]`. Verify a repair by re-reading the deployed data, not the tool's
  success message.

## Ingesting PS2-era texture data (porting)

PS2 GS textures usually arrive as the GS transfer stream, already GS-swizzled: 8-bit images uploaded as 32-bit at
half width and height, 4-bit as 16-bit; 8-bit CLUTs in CSM1 order (index bits 3 and 4 swapped within 32-entry
groups); alpha 0..128. Convert with one fused permutation GS-swizzle -> GE-swizzle (~2.3 instructions per texel),
reorder the CLUT, alpha = min(255, 2a) and double the alpha-test reference `[measured: PSP-3000, recompiled game]`.
Converting at load time cost 0.27-0.6 ms per frame (install-time pre-converted files would have saved under 1%)
`[measured: PSP-3000, recompiled game]`. A partly wrong deswizzle gives plausible low-frequency colours, not noise:
validate a decoder against a known-good reference image before trusting it at scale `[practice]`.

## Sources

- pspsdk: https://github.com/pspdev/pspsdk (`src/gu/pspgu.h`, `src/gu/sceGuTexImage.c`,
  `src/samples/gu/blit/blit.c`)
- PPSSPP GE documentation: https://www.ppsspp.org/docs/psp-hardware/gpu/ (ge-overview, ge-vertex-pipeline,
  raster-pipeline, texture-cache, image-formats, arithmetic)
