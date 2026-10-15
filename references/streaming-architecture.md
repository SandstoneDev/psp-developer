# Streaming architecture: chunks, async loader, residency, LOD

When the world is larger than RAM, stream it. The design below let a streaming map engine draw a whole large map in a
small fixed budget with no holes at a steady 30 fps on a PSP-3000 [measured: PSP-3000, streaming map engine], and was
later stress-tested by a large 3D game port. It is the usual console pattern: region chunks + table of contents,
an async loader, double-buffered GE lists, arena allocators.

Related: [file-io-memory-stick.md](file-io-memory-stick.md) (what a card read costs),
[real-hw-gotchas.md](real-hw-gotchas.md) (cache and heap hazards), [memory-map.md](memory-map.md),
[instrumentation.md](instrumentation.md).

## 1. Region chunks

Cut the world into spatial chunks (e.g. 512 m tiles), each a self-contained mini-world: its own index (model /
submesh / texture / instance tables + a local grid) and its geometry and texture pools. An offline packer writes one
archive per chunk (TOC + pools in load order) and a manifest with each chunk's grid coordinate, world bounds and byte
size (for budgeting). The engine keeps a small array of slots (loaded chunks) plus the manifest, and decides each
frame which chunks should be resident from camera proximity.

## 2. Atomic residency (no pop-in)

Loading a chunk's index and then streaming each model on first draw gives a one-frame hole on every first
appearance: 75-93 % of frames had a missing model [measured: PSP-3000, streaming map engine]. Instead load each chunk
**atomically and fully resident**: the loader reads the whole geometry and texture pools and publishes the slot only
when complete. A chunk is either all there or not drawn. Result: ~0 per-model pop-in, no eviction churn.

Read the vertex pool and the index pool in **two big reads**, not a seek + read per model, and point every model into
the shared buffer. Why: opens cost far more than requests, and requests far more than bytes -
[file-io-memory-stick.md](file-io-memory-stick.md).

## 3. Off-thread I/O and deferred free

- A dedicated **I/O thread** loads a chunk (open, read index, read pools, build side tables). The main thread
  reserves a slot with a "loading" sentinel; the I/O thread publishes the region id **last**, after dcache write-back
  and a barrier ([real-hw-gotchas.md](real-hw-gotchas.md) section 2). The tens of milliseconds of loading never touch
  the frame. A prefetch radius slightly beyond the draw distance finishes loads before the camera arrives.
- **Eviction frees on the I/O thread too.** If the main thread `free()`s while the I/O thread is inside `memalign`,
  they contend for the heap lock and the main thread stalls. The main thread only marks a slot "freeing"; the I/O
  thread frees and closes. The main thread never touches the heap during streaming [measured: PSP-3000, streaming
  map engine].
- Large RAM-to-RAM staging copies inside a load can go to the DMA controller with `sceDmacMemcpy`
  [public: pspsdk src/dmac/pspdmac.h]: write back the source range before, invalidate the destination after
  [practice]. It frees the CPU and keeps copied bytes out of the data cache; for small blobs a plain memcpy wins -
  measure the crossover.

## 4. Worker priority: the loader is the critical path

The scheduler is strict priority and **a lower number is a higher priority** [public: pspsdk
src/user/pspthreadman.h]. That is easy to write backwards in a design note and catastrophic to implement backwards.
A loader parked "politely" below everything (a collision-tile loader at 0x31, below a music decoder at 0x2E and the
geometry streamer at 0x2C) starved under load: ~0.1 MB/s at 100 ms per request while the main thread ran 50 ms frames
[measured: PSP-3000, large 3D game port]. When the player outruns streaming, the loader is not background work.

- Gameplay-critical small loads (collision) get a worker **above** decorative streams (e.g. 0x2B, above a 0x2E music
  decoder).
- **Emergency boost:** while a gate is actually holding the player waiting for data, frame rate is worthless - lift the
  worker above the main thread (e.g. 0x1D-0x1E) for a bounded window (~600 ms), restore when the queue drains. A
  multi-second stall became a sub-second nudge.
- **A failed load needs a retry path** that does not wait for the window to move. A gate holding the player still plus
  a loader that retries only on window change is a deadlock by priorities (hit in practice).

## 5. Demand shaping, never a bare admission cap

Everything the renderer enqueues is already wanted this frame. A naive "queue full -> accept only near requests" cap
stopped the whole distant city from ever loading (the near zone kept the queue above the cap): a 7x throughput
regression [measured: PSP-3000, large 3D game port]. The safe form:
- only under deep backlog (queue > ~150), far requests (> ~300 units) neither enqueue nor set a pending flag, so the
  next collect re-requests them once the queue breathes;
- near geometry and collision keep a guaranteed lane;
- distant scenery goes coarse-first under overload instead of starving the road ahead.

A shared per-frame admission budget consumed in a fixed order is priority by order, not by importance: a speculative
prefetch pass ran first and spent all 48 admissions on requests that were then rejected, so visible geometry
enqueued nothing. Give speculative paths their own quota and count "requests dropped for lack of budget" (must read 0).

## 6. Non-render consumers peek, never stamp or enqueue

Collision, AI and audio occlusion read the streaming cache **resident-only**: a `peek` that does no LRU stamp and no
load request. A collision fallback that called the renderer's `use_model` re-stamped every nearby model every frame
-> the cache became un-evictable -> the heap drained to 66 KB -> hard crash on the device [measured: PSP-3000, large 3D
game port]. Only the renderer expresses demand; other consumers use what is there and skip the rest.

## 7. Every side table is a second capacity, and it overflows silently

A chunk can load, occupy a slot, have valid tables, and never be drawn, with no log line. One port had a renderer
limit of 16 registered worlds and a streaming limit of 36 loaded regions, in two files, with nothing tying them:

```c
if (n_worlds < MAX_WORLDS) worlds[n_worlds++] = w;   /* did not fit -> silently not registered */
```

Past 16 resident regions the surplus was never "live", every draw was rejected, and the player stood in an invisible
region for a hundred seconds. Five builds hunted a race that did not exist, because one rejection counter merged
"world not live" with "generation stale" and so always showed a plausible cause [measured: PSP-3000, large 3D game
port].
- Assert the relation between the two capacities at build time.
- A counter that can be bumped for two reasons will always agree with your hypothesis: split it.
- A registration that can silently not happen must log or assert.

## 8. RAM budget and distance-priority eviction

- Track resident bytes; cap them at a budget derived from `sceKernelTotalFreeMemSize()` at boot, after reserving
  code, the far layer, indices (they live outside the geometry budget) and framebuffers.
- Prefer a **fixed** boot-probed budget over run-time auto-tuning: two "smart" queue soft-caps / scan windows were
  both regressions, reverted within two builds [measured: PSP-3000, streaming map engine].
- When the budget is full and a **nearer** chunk is needed, evict the **farthest** resident chunk (only if it is
  farther than the candidate). Merely skipping the load when full lets stale chunks behind the camera pin the budget,
  and new areas ahead stay coarse forever. Check that resident bytes cycle (rise and fall) as you move, instead of
  pinning at the cap.

### A budget below the working set thrashes every frame
After shrinking the heap so a port fit, the loader read in 100 % of frames even with the player standing still and
reloaded the same models 10-23 times in 45 s; on the device that cost ~9.1 ms per frame (4.75 ms re-conversion for the
GE + 3.9 ms synchronous reads). Giving 1.5 MB back brought the loader to 32-38 % busy and saved ~7.5 ms per frame
[measured: PSP-3000, recompiled game; load counters from a host build]. Size the heap as resident data + streaming
budget + 1.6-2 MB headroom, from load counters (loader busy share, reloads per model), not from a guess.

## 9. LOD pyramid and a far backdrop (no holes at distance)

Detail chunks cover only the near range. Beyond them draw a **coarse whole-map backdrop** built from the lowest LOD
tiers, as one streamed (or resident, if it fits) world, from where chunk detail ends to the horizon. Use the
middle-coarse tier in the transition band and the coarsest far away, so it is not blocky right past the detail edge.

**Underlay the backdrop from close in, and draw it first.** If a detail chunk is still loading or was evicted, the
coarse layer is already underneath: never an empty hole, only coarse-until-detail. Detail draws on top (with
reversed-Z and a GEQUAL test, the later draw wins coplanar ties; see [rendering.md](rendering.md)). This turns "things
pop in and out as I fly" into "everything is always there, sharpening as it loads" [measured: PSP-3000, streaming map
engine].

Within a chunk draw each LOD tier in its distance band with a small hysteresis overlap, and suppress a coarser tier
where a finer tier exists in the same cell (track the finest tier per cell) to avoid double draw and z-fighting.

## 10. Do not drop LOD data at export

An exporter option that keeps the detailed LOD tier only in dense tiles leaves most of the map with no near detail:
up close only the coarse proxy shows. Export every LOD tier everywhere and let the runtime budget decide what is
resident. The runtime, not the export, should bound memory [measured: PSP-3000, streaming map engine].

## Sources

- pspsdk (https://github.com/pspdev/pspsdk): `src/user/pspthreadman.h`, `src/dmac/pspdmac.h`
