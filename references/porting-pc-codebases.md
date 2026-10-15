# Porting an existing PC C++ codebase to the PSP

An open-source PC C++ client (modern C++, SDL3, ~120k lines) was brought up on a PSP-3000 without rewriting the
game: the upstream code compiles unchanged, and the PSP side is a platform layer plus link-time hooks. What worked,
and what broke in the order it broke. All numbers: [measured: PSP-3000, PC game client port] unless tagged otherwise.

## Keep upstream untouched

- **Link-time wraps** (`-Wl,--wrap=sym`, then define `__wrap_sym` and call `__real_sym`). Used for: the present call
  (per-frame platform duties), window/renderer creation (force the PSP size), the image loader (own texture
  format), the GU swap (on-screen keyboard update), texture create/destroy (texture memory accounting), and
  `operator new` (`_Znwj` on 32-bit; allocation profiler, below). A wrap only catches references **between** object
  files: a call inside the same `.o` (or the same archive member) is resolved by the assembler and not wrapped
  [practice].
- **Shadow headers**: a shim directory placed **first** on the include path replaces an upstream header (a smaller
  class with the same API, an extra overload). Catch: `#include "x.hpp"` with quotes searches the includer's own
  directory first, so an upstream header next to the includer wins; shadow the includer too. `#include_next` lets a
  shim wrap the real header and add to it [practice].
- **Replaced `.cpp` files** for whole-file swaps (data-archive readers, texture database loaders) [practice].
- **Weak hooks** for the few places that must call into the platform. Upstream gets
  `extern "C" void plat_hook(int) __attribute__((weak));` and `if (plat_hook) plat_hook(x);`, marked with a comment;
  other builds link without the symbol and skip the call. Cheaper to merge than a replaced file [practice].
- Rename upstream `main` (`-Dmain=upstream_main`) and call it after the platform is up [practice].

## What broke, with the fix

- **Open-file limit.** The console allowed only about 9 more `sceIoOpen` calls (then `0x80020320`, SCE_KERNEL_ERROR_MFILE [public: pspsdk src/user/pspkerror.h]); PPSSPP allowed about 60. A PC design that opens one file per asset database died at the 10th. Fix: one pack
  file, one descriptor per part, an LRU of open handles. See [file-io-memory-stick.md](file-io-memory-stick.md).
- **Whole-data-in-RAM designs.** An index table of 380k entries (~20 MB); maps decoded whole (41.6 MB for one map);
  every UI label owning its own tinyxml2 document (tinyxml2 allocates a 4 KB pool block per node type per document:
  ~7 MB in total). Fixes: a compact pack index loaded lazily; maps as a tile dictionary + u16 indices (1.9 MB);
  tinyxml2 rebuilt with 512-byte pool blocks (a generated header placed first on every include path).
- **Caches counted in entries, not bytes** (8192 map textures, 1024 of each sprite set). Give entries a weight in KB
  and each set a KB budget; a single item heavier than its budget must be allowed, not assert.
- **Fetch before cull.** The draw loop fetched textures for cells 25 rows above and below the screen (tall objects)
  before culling: 500 texture reloads per frame once the caches were bounded. Remember each texture's size on first
  load and cull before fetching.
- **Eager UI.** A settings window built one row per item (thousands) at world entry. Build on first draw.
- **32-bit vs 64-bit wire format.** A portable binary serializer (cereal) writes `size_t` with its declared size, 4
  bytes on the PSP and 8 on a 64-bit server. Audit by printing `sizeof` of every message struct with both compilers.
  Fix with a PSP-only overload (on the PSP toolchain `size_t` is `unsigned int` while `uint32_t` is `unsigned long`,
  so they are distinct types) plus a boot self-test.
- **Library bugs show up only on the new path.** An SDL3_gfx release returned failure from `roundedRectangleRGBA` for
  every radius > 1 (the code starts from `result = 0` and then ANDs every step into it); upstream treated the failure as fatal.
- **SDL3 on the PSP** (packaged by pspdev in psp-packages: `sdl3`, `sdl3-gfx`, `sdl3-image`, `sdl3-mixer`, `sdl3-ttf`):
  - the PSP video driver keeps the window at the requested size: 800x600 renders 1:1 and you see the top-left 480x272.
    Create a 480x272 window and use SDL logical presentation;
  - textures wider than 512 needed a renderer patch: split into 512-wide tiles, not swizzled (the fast path needs
    texture width == buffer width);
  - linear filtering samples the power-of-two zero padding and draws dark seams between tiles; use nearest.
- **Emulator blind spots.** PPSSPP's hardware backends do not write frames back to emulated VRAM, so a VRAM screenshot
  shows a stale frame; scripted input through its debugger sometimes stopped after a restart. Take only yes/no answers
  from the emulator; numbers and pictures from the console (the port saved a BMP on L+R+START).
- **Free heap differs**: ~47.9 MB in PPSSPP vs ~42.8 MB on the console for the same large-memory EBOOT. Log
  `mallinfo()` and a largest-block probe at each step of a heavy transition, install `std::set_new_handler` to log
  before dying, and in the emulator allocate a ballast block at boot so its heap matches the console.

Toolchain flags for such a port (no exceptions/RTTI where upstream allows, large-memory flag, PRX build):
[build-toolchain.md](build-toolchain.md); memory partitions: [memory-map.md](memory-map.md).

## Diagnostics that paid off

- **`operator new` wrap**: log every request >= 4 MB with its caller plus every stack word that points into `.text`
  (Allegrex code has no frame pointers), then `psp-addr2line -f -C -e <unstripped elf>`. On failure log `mallinfo()`
  and an allocation profile per caller (top 30 sites). It found the 41.6 MB map, the tinyxml2 pools and the texture
  caches.

```cpp
extern "C" void *__real__Znwj(unsigned n);
extern "C" void *__wrap__Znwj(unsigned n)       // link with -Wl,--wrap=_Znwj
{
    void *p = __real__Znwj(n);                  // throws / calls new_handler on failure
    if (n >= (4u << 20)) log_big_alloc(n, __builtin_return_address(0));
    prof_account(__builtin_return_address(0), n);
    return p;
}
```

- Live texture KB next to heap KB every 64 loads: splits "textures vs everything else" in one line.
- Loads per asset set every 2048 loads: shows which cache thrashes.
- GCC deletes a `malloc(n); if (p) free(p);` probe because the pair has no visible effect, so a "is there room?"
  check always said yes; a streamer then never refused work and the console ran out of memory at world entry
  [measured: PSP-3000, streaming map engine]. Write
  `void *volatile p = malloc(n); if (p) { ((volatile char *)p)[0] = 0; free(p); }`.

## Sources

- SDL3, SDL3_gfx, cereal and tinyxml2 packages for the PSP: https://github.com/pspdev/psp-packages
- pspsdk (`src/user/pspkerror.h` error names): https://github.com/pspdev/pspsdk
