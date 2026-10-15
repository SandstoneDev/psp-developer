# Optimization and profiling: find the wall before you push on it

A PSP frame is limited by one of three things: the main CPU, the GE, or the vblank wait (you are
already done and idle). Which one it is depends on the program, and the emulator cannot tell you.
Measure on the device first, then pick levers. Discipline for proving a change on hardware is in
[instrumentation.md](instrumentation.md).

## 1. Both walls happen: two measured cases

**Case A - GE / fill bound (a streaming 3D map engine).**
- GE time on hardware was tens of ms per frame, above 150 ms in dense, overdrawn scenes
  [measured: PSP-3000, streaming map engine]. The same frame showed ~13 us of GE time in PPSSPP and
  30-48 ms on the device [measured: PSP-3000, streaming map engine].
- The GE also pays per triangle, not only per pixel: ~0.455 us per triangle on props, from three
  scene points on one straight line [measured: PSP-3000, streaming map engine]. Triangle count, LOD
  and far-layer simplification move GE time too.
- Rendering the world to a smaller target and upscaling took GE time from 21.9 to ~13 ms and the
  frame rate from 24 to 27-30 fps [measured: PSP-3000, streaming map engine] (section 9).

**Case B - CPU / memory bound (a large recompiled game).**
- Scripted outdoor runs took 55-62 ms per frame. The GE was not the limit: real GE time 6.4 ms
  (interior) to 12.7 ms (outdoor route), CPU wait for the GE at the flip 0.7-1.0 ms
  [measured: PSP-3000, recompiled game].
- The CPU retired ~161 M instructions per second at CPI 2.6-2.9, about half of the busy cycles
  stalled on memory (~80 cycles per miss), and the profile was flat
  [measured: PSP-3000, recompiled game] (section 6).

| Part of the frame, ms | Driving | On foot |
|---|---|---|
| Whole frame | 61.9 | 55.7 |
| 3D back-end (native draw path) | 17.4 | 17.2 |
| Render-list walk (translated) | 14.1 | 13.3 |
| Game logic (translated) | 14.6 | 13.2 |
| Effects / HUD | 9.2 | 8.5 |
| Runtime / system | 4.1 | 2.7 |
| Streaming | 2.4 | 0.7 |

[measured: PSP-3000, recompiled game]. Floor with every visual cut and no traffic: 50.6 ms
(19.8 fps). Halving the population density saved 18.6 ms; one moving actor cost ~1 ms (logic,
animation, draw) [measured: PSP-3000, recompiled game]. The large remaining levers changed the game
(density, draw distance, detail), so they were offered to the user as options [practice].

Lesson: a homebrew renderer with simple logic tends to hit the GE; a big ported or translated code
base tends to hit the CPU. Do not assume either - measure [practice].

## 2. The emulator gives booleans, not costs

- PPSSPP's GE cost is close to zero, so a PPSSPP profile always looks CPU bound
  [measured: PSP-3000, streaming map engine, vs PPSSPP].
- Its CPU numbers were ~2.5x optimistic, and relative costs differ too: rewriting `sceGum*` calls as
  direct `sceGuSetMatrix` measured -67 % in the emulator and ~-10 % on hardware
  [measured: PSP-3000, streaming map engine].
- It has no cache model: a 13-20 KB native path called between pieces of game code cost 13.5 us per
  mesh in PPSSPP and 55 us on the device [measured: PSP-3000, recompiled game].
- Accessing the hardware counter registers crashes it [measured: PPSSPP, recompiled game].

Use PPSSPP for "does it run / render / crash"; take every number from the device
([instrumentation.md](instrumentation.md) section 1).

## 3. Frame model and the vsync trap

Split one frame (end of previous frame -> end of this one) into:
- `build`: update + display-list build, up to `sceGuFinish`;
- `ge_tail`: the `sceGuSync` wait. In immediate (direct) mode the GE runs while the CPU builds, so
  this is only the tail of the GE work; a shrinking tail can mean the CPU got slower [derived];
- `vbl`: vblank wait + swap.

Compare **work = frame - vsync wait**, never raw frame time. At 60 Hz with vsync, 37 ms and 45 ms of
work both show as a 50 ms frame [derived], so "turning X off didn't change the frame" proves
nothing. A conclusion "it's fill, not triangles" was once read off vsync-quantized frame times and
was wrong (the per-triangle cost in section 1 was found afterwards) [measured: PSP-3000, streaming map engine].

CPU load per frame: `1 - delta(idleClocks) / delta(frame)` from `sceKernelReferSystemStatus`
[public: pspsdk src/user/pspthreadman.h]; it works in user mode on a retail unit
[measured: PSP-3000, PC game client port].

## 4. Per-phase profiler with a bottleneck verdict

Instrument the frame into phases and log a summary each interval (dev build, rare flush):
- `collect` (cull / frustum / distance scan), `sort`, `req` (streaming requests), `draw` (list build
  + draw calls), `cpu_us` (whole CPU frame), `ge_us` (GE time, see section 5), streaming health
  (miss %, evictions per frame, hitch %).
- Print a verdict so the fix is obvious [practice]:
  - hitch % high -> STREAMING (prefetch radius, budget, coalesced reads);
  - `ge_us` dominant (device only) -> GE / FILL (draw distance, overdraw, screen coverage, triangles);
  - `draw` >> `collect` -> CPU-DRAW (fewer instances, precomputed matrices, fewer submeshes);
  - many culled but slow -> CPU-CULL (cull whole cells before instances);
  - else -> balanced.
- Headroom check: if `cpu_us + ge_us` is far under the budget (say 3 ms of 33 ms), you are vsync
  capped and the biggest phase is not a bottleneck. Spend the headroom instead [practice].
- At boot, log CPU / bus MHz (`scePowerGetCpuClockFrequency`, `scePowerGetBusClockFrequency`
  [public: pspsdk src/power/psppower.h]), free heap (`sceKernelMaxFreeMemSize`,
  `sceKernelTotalFreeMemSize` [public: pspsdk src/user/pspsysmem.h]) and the budgets you derived.

### Zones on the device

- Clock: `sceKernelGetSystemTimeLow` (1 us) [public: pspsdk src/user/pspthreadman.h]. It is a
  syscall: calling `sceKernelGetSystemTimeWide` 2-4 times per instance added whole milliseconds to
  the phase it measured [measured: PSP-3000, streaming map engine]. Time per phase or pass, never per
  instance.
- Integer microseconds only. Soft-float double timing in hooked calls, plus a fixed 1-in-16 sample
  that aliased with a repeating call pattern, inflated some per-call costs 100-1000x
  [measured: PSP-3000, recompiled game]. Sample short hot functions with a hashed 1-in-N choice,
  never a fixed stride.
- One clock per thread (or per coroutine) so switches do not leak time into the wrong zone; account
  per caller edge; count recursion once.
- Report the profiler's own cost in every run: 3.2-4.4 ms per frame for a full zone set
  [measured: PSP-3000, recompiled game]; a text overlay cost ~2.5 ms of CPU itself
  [measured: PSP-3000, PC game client port]. Give it its own zone, switch it off for clean numbers,
  keep profiling builds in their own A/B folder.
- Parse explicit fields from logs: a script that read a 50 Hz log as 60 Hz overstated fps by 20 %
  [measured: PSP-3000, recompiled game].

## 5. Measuring GE time correctly

The GE runs concurrently with the CPU, so most obvious timers measure something else.

- `sceGuSync` after `sceGuFinish` measures only the **tail**: in one engine 27 ms of tail against
  ~41 ms of GE execution [measured: PSP-3000, streaming map engine].
- **Signal / finish timestamps.** `sceGuSignal(mode, id)` plus `sceGuSetCallback(GU_CALLBACK_SIGNAL /
  GU_CALLBACK_FINISH, fn)` [public: pspsdk src/gu/pspgu.h]; the lower layer is `sceGeSetCallback`
  [public: pspsdk src/ge/pspge.h]. The callbacks run in interrupt context: store a timestamp, nothing
  else. A pass = its signal -> next signal (last pass -> FINISH). The signal fires when the GE
  parser reaches it, so in direct mode a pass time = max(CPU build of the pass, GE execution): about
  equal to its CPU zone -> CPU bound; clearly longer -> GE bound [derived]. Never call `sceGuSync`
  mid-list to "measure".
- **Serial replay (the reliable number).** Build one frame's list in a capture mode, then execute
  it with the CPU waiting, optionally split by drawing category. This gave the real GE times in
  case B [measured: PSP-3000, recompiled game].
- **The busy-sampler pitfall.** A thread polling `sceGeDrawSync(1)` every 1 ms (peek mode; the state
  values are named COMPLETED / QUEUED / DRAWING / STALLING / PAUSED in
  [public: uofw include/ge_user.h], pspsdk names them differently in `PspGeListState`) reported the
  GE "drawing" 15-35 ms per frame. Over 147,390 samples it never saw a stall state; its "drawing"
  share equalled the time a display list was open, i.e. the CPU's render phase. Serial replay gave
  6.4 ms (interior, sampler 15 ms) and 12.5-12.7 ms (route, sampler 28 ms)
  [measured: PSP-3000, recompiled game]. PPSSPP never reports "drawing", so the instrument looks
  plausible on one side and empty on the other. Treat any always-on GE busy figure as list-open
  time until a serial probe agrees.
- Report the CPU's wait for the GE at the flip separately: 0.7-1.0 ms plus ~0.65 ms of mid-frame
  resyncs in case B [measured: PSP-3000, recompiled game]. An asynchronous flip with a presenter
  thread gave no gain there (+0.43 ms, 95 % interval -3.0..+2.6) [measured: PSP-3000, recompiled game].

```c
/* GE pass timing: signal + finish callbacks only store timestamps. */
#include <pspgu.h>
#include <pspthreadman.h>
#define MAX_PASS 16
static volatile unsigned int g_sig_t[MAX_PASS], g_fin_t;
static void on_signal(int id) { if (id < MAX_PASS) g_sig_t[id] = sceKernelGetSystemTimeLow(); }
static void on_finish(int id) { (void)id; g_fin_t = sceKernelGetSystemTimeLow(); }

void prof_ge_init(void) {
    sceGuSetCallback(GU_CALLBACK_SIGNAL, on_signal);
    sceGuSetCallback(GU_CALLBACK_FINISH, on_finish);
}
/* in the list, at the start of each pass: */
#define PROF_GE_PASS(id) sceGuSignal(GU_SIGNAL_NOWAIT, (id))
/* after the frame: pass i = g_sig_t[i+1] - g_sig_t[i]; last = g_fin_t - g_sig_t[last];
   ge_busy = g_fin_t - g_sig_t[0]. */
```

## 6. Hardware performance counters

The Allegrex has a memory-mapped profiler block, seen at 0xBC400000 from kernel mode: PROFEN (enable) followed by system-clock and CPU-clock cycle counters, stall counters (total,
internal, memory, coprocessor, VFPU), sleep, bus access, uncached / cached load and store counts,
I-cache miss, D-cache miss, D-cache writeback, COP0 / FPU / VFPU instruction counts, and a local-bus
counter at +0x50 [public: PSPTEK, PSP Profiler]. PSPTEK lists a control register with a "make the
profiler accessible in user mode?" bit [public: PSPTEK, system control registers]; treat the block as
kernel-only.

pspsdk ships helpers: `pspDebugProfilerEnable / Disable / Clear / GetRegs / Print`
[public: pspsdk src/debug/pspdebug.h, src/debug/profiler.c]. Two cautions before using them:
- `pspDebugProfilerGetRegs` reads 21 words, offsets 0x00-0x50, into a 20-field structure (the last
  word overruns it), and the field names have no "total stall" entry, so from offset 0x0C on they are
  one word off PSPTEK's table [public: pspsdk src/debug/profiler.c vs PSPTEK]. Validate names against behaviour (sleep should
  rise when the CPU idles; cycles should match the clock) before trusting a field.
- Reading the word at +0x50 killed the reading thread on a PSP-3000; words 0x00-0x4C were safe
  [measured: PSP-3000, homebrew test program].

Pattern that worked on hardware [measured: PSP-3000, recompiled game]:
- a kernel thread (in a kernel module; user code reaches it through a kernel call such as
  `kuKernelCall` [public: ARK-5 source, SystemControl kubridge]) wakes every 50 ms, reads words
  0x00-0x4C, adds 32-bit deltas into 64-bit sums;
- it sets PROFEN only if it was 0 and never resets the counters (another user of the block may be
  counting too);
- it writes a "step" breadcrumb before each register access, so a dead thread names the word that
  killed it;
- detect the emulator first and refuse (PPSSPP crashes on the block); stop the thread and restore
  PROFEN before the module unloads;
- each read through the kernel-call door cost ~65 us, so per-zone counter reads are for short
  diagnostic runs only.

Derived figures [derived from the PSPTEK counter list]:
- busy cycles = CPU cycles - sleep; instructions ~ CPU cycles - total stall - sleep;
  CPI = busy / instructions; memory-stall share = memory stall / busy;
- D-cache hit ratio = 1 - D-miss / (cached loads + cached stores);
- I-cache: lines are 64 bytes = 16 instructions [public: PSP cache how-to], so straight-line code
  that misses on every line still shows a 0.9375 hit ratio. Judge against that, not 1.0.
- misses per 1000 instructions, memory-stall cycles per miss.

Measured in a large recompiled game [measured: PSP-3000, recompiled game]: CPI 2.65-2.9; ~50 % of
busy cycles in memory stall; ~80 cycles per miss; I-cache misses 11.7-14 per 1000 instructions,
D-cache misses 5.6-8; ~161 M instructions per second at 333 MHz. Verdict rule used: >= 35 % of busy
cycles in memory stall = MEMORY-BOUND; >= 60 % retiring = INSTRUCTION-BOUND; else mixed [practice].

pspsdk also has gprof support (`-pg`, `gprof_start/gprof_stop`, `psp-gprof` on the host)
[public: pspsdk src/prof/pspprof.h, src/samples/gprof]. It is a statistical whole-program view;
zones answer "where did this frame go" better [practice].

## 7. Instruction-cache-bound translated code

The CPU has a 16 KB instruction cache and a 16 KB data cache [public: PSPTEK, memory map]. A
translated or very large program touches megabytes of code per frame: ~4.8 MB of code entered per
frame in one recompiled game [measured: PSP-3000, recompiled game]. Most of it runs once or a few
times per frame, so the misses are capacity misses; no single function is above ~1 % and
hand-optimising the "hottest" one gains little [measured: PSP-3000, recompiled game].

Levers, with what they did on hardware [measured: PSP-3000, recompiled game]:

| Lever | Result |
|---|---|
| Function layout: never-run functions marked cold, hot call chains placed together (`-ffunction-sections` + a section ordering file) | -1.9 % frame time |
| Basic-block placement hints | +0.9 % (worse) |
| Base-register masking (fewer address-fix instructions per guest memory access) | -6 % |
| `-Os` instead of `-O2` | 41 % slower despite 34 % less code |
| Assorted gcc flags (`-freorder-blocks-algorithm=simple`, `-fno-move-loop-invariants`, no tree-PRE / GCSE) | slower |
| Code-generation rounds removing ~4.3 % / ~9 % of executed instructions | -1.6 ms / -5 ms per frame |
| A 13-20 KB native helper called between game-code pieces, deferred and batched | 30 -> 13.6 ms per frame |
| Moving audio mixing to the Media Engine | removes its main-core cost; the ME's uncached GE-word stores did not slow the main core (39.69 vs 39.61 ms) |

- Host instruction counts are hypotheses: three levers that cut render-list calls by ~40 % on the
  host gave no measurable change on the PSP [measured: PSP-3000, recompiled game].
- An earlier "+11 ms main-core cost of Media Engine work" turned out to be hook work counted in
  another zone [measured: PSP-3000, recompiled game]. Check what a zone contains before blaming it.
- Prefer levers that remove code (fewer helper calls, fewer save/restores, branchless clamps) or
  replace whole API-level work, over tuning one function [practice].

## 8. CPU micro-levers

**Allegrex builtins** in pspdev gcc (listed from the compiler, gcc 4.3.5 and 15.2.0):
`__builtin_allegrex_min`, `_max`, `_clz`, `_clo`, `_ctz`, `_cto`, `_bitrev`, `_wsbh`, `_wsbw`,
`_seb`, `_seh`, `_rotr` (4.3.5 also `_rotl`), `_sqrt_s`, `_round_w_s`, `_trunc_w_s`, `_ceil_w_s`,
`_floor_w_s`, `_sync`, `_cache` [public: pspdev gcc, builtin list]. pspsdk itself uses
`__builtin_allegrex_wsbh/wsbw` for `htons/htonl` [public: pspsdk src/libcglue/netinet/in.h].
Branchless `min/max` suit clip clamps and AABB reductions in hot loops; taken branches and jumps
measure 3-4 cycles [public: vfpu-docs, inst cycles].

**FPU cost** [public: vfpu-docs, inst cycles, PSP-3000]: `add.s` / `mul.s` throughput ~1 cycle,
latency ~5; `div.s` ~31 cycles, `sqrt.s` ~30; integer `div` ~21. On the VFPU, `vrcp.s` / `vrsq.s`
issue in ~1 cycle with ~7 latency, while `vdiv.s` costs ~14 per element. Compute a reciprocal once
and multiply; interleave independent work to hide the 5-cycle latency [derived]. See
[vfpu-math.md](vfpu-math.md).

**Caches and coherency** [public: PSP cache how-to]: 64-byte lines; no snooping, so other bus
masters (GE, DMA, ME) see only what was written back. Functions: `sceKernelDcacheWritebackAll`,
`sceKernelDcacheWritebackInvalidateAll`, `sceKernelDcacheWritebackRange`,
`sceKernelDcacheWritebackInvalidateRange`, `sceKernelDcacheInvalidateRange`,
`sceKernelIcacheInvalidateRange` (after loading or patching code) [public: pspsdk src/user/psputils.h].
- Range writeback beats the whole-cache call for small ranges; above ~16 KB writing back everything
  is likely cheaper [public: PSP cache how-to].
- The uncached alias sets bit 30 of a user address (0x08800000 -> 0x48800000). Never let cached and
  uncached data share a 64-byte line: `memalign(64, ...)`, sizes rounded to 64, and
  writeback-invalidate before the first uncached access [public: PSP cache how-to].

**DMA copies.** `sceDmacMemcpy(dst, src, n)` and `sceDmacTryMemcpy` exist in pspsdk
[public: pspsdk src/dmac/pspdmac.h]. With no snooping, write back the source range before and
invalidate the destination after [derived from PSP cache how-to]. Measure the crossover against
`memcpy` on the device; small copies lose to setup cost [practice].

## 9. Overclocking does not move the GE

A CFW PLL overclock measured at ~460 MHz sped up CPU-side work but GE time did not change
[measured: PSP-3000, streaming map engine]. `scePowerGetCpuClockFrequency` kept reporting 333
under that overclock, so confirm with a calibrated boot loop; and once raised, a later call with a
lower frequency was silently ignored, so "333 vs 460" must be two separate runs
[measured: PSP-3000, streaming map engine]. Spend an overclock on the CPU half (decompression,
culling, physics). Details: [overclocking.md](overclocking.md).

## 10. GE-side levers

**Fill / bandwidth** [practice unless tagged]:
1. Backface culling on, and a 16-bit framebuffer (5650 / 5551 / 4444 halve the bytes per pixel of
   8888 [derived]).
2. Draw distance: fill scales with visible pixels; keep a coarse far layer and a short detail
   distance. Adaptive distance: shrink on eviction / miss / over budget, grow slowly when idle.
3. Front-to-back opaque order (a few coarse depth bands) lets the depth test reject overdraw.
4. Mipmaps and a mip bias on tiled surfaces: fewer texels fetched at distance, less aliasing
   (see [textures-uv.md](textures-uv.md)).
5. Fewer and smaller textures (lower resolution, 4/8-bit CLUT): halving texture resolution took a
   route's real GE time from 12.5-12.7 to 8.7 ms [measured: PSP-3000, recompiled game].
6. Sort by texture + palette and skip redundant binds / CLUT loads; state changes cost GE time,
   draw-call count much less so.
7. Post-processing passes cost real GE time: 1.27 ms per frame for two full-screen passes
   [measured: PSP-3000, recompiled game].

**Vertices.** The GE also pays per triangle (section 1). Strips cut the vertex count 2-3x against a
triangle list [derived]. Treat an index buffer as a RAM-saving tool until an on-device A/B shows it
is faster; PPSSPP's host-GPU back-ends have their own vertex caching, so a strip-vs-indexed A/B is
meaningless in the emulator [practice].

**Render small, upscale (dynamic resolution)** when the GE time on hardware sits above ~12-15 ms
with a GE verdict [measured: PSP-3000, streaming map engine]:
- render the world to a smaller target (432x244, 400x226, 352x200), stretch it to the framebuffer,
  draw HUD / menus at native resolution;
- blit in vertical strips (~32 px wide): the GE reads textures through an 8 KB texture cache
  [public: PPSSPP GE docs, texture cache], and one 480-wide sprite thrashes it;
- do the upscale in its own list after the world list finished; sampling a target written in the
  same list flickered intermittently on hardware and rendered fine in PPSSPP
  [measured: PSP-3000, streaming map engine];
- drive the level with an EMA of GE time (e.g. >15 ms -> down a level, <10.5 ms -> up) with wide
  hysteresis and ~10 s cooldowns; a tight controller visibly ladders up and down;
- verify the wall first: when frames were vsync capped or CPU bound, an -89 % fill cut moved
  nothing, and the feature waited until the GE really was the wall [measured: PSP-3000, streaming map engine];
- if lowering resolution stops helping, the floor is vertex cost: fewer, bigger triangles.

**Move per-vertex CPU work to the GE (and when not to)** [measured: PSP-3000, streaming map engine]:
- A per-vertex CPU relight (sun + sheen over every body vertex, even every 3rd frame) cost ~5 ms per
  frame. GE lighting (normals in the vertex, `sceGuAmbient` + one directional light + up to 3 point
  lights, baked vertex colour kept as the material) replaced it: 1.3 ms + 5 ms spikes -> 0.34 ms. It
  also removed a flicker caused by the CPU rewriting vertices the GE was reading.
- GE skinning (`sceGuBoneMatrix`, up to 8 bones per draw, `GU_WEIGHTS(n)` [public: pspsdk src/gu/pspgu.h])
  took one hero character from 3080 us to 428 us (7.2x). For a crowd with animation LOD it lost:
  14 bone clusters x 8 characters = 112 draws, 4.9 ms against 2.0 ms for cached CPU-skinned vertices.
  GE skinning wins for one hot instance; crowds win with vertex caching and animation LOD.
- Bone-matrix upload: a row-vector, row-major CPU convention and the GE's convention need the same
  bytes. `memcpy`; an element transpose made limbs splay outward.

**Dead ends others paid for** [practice]:
- texture atlases to dodge the lack of multitexturing: break UV tiling, duplicate texels;
- single buffering to save VRAM: tearing;
- static-batching instanced world geometry into merged buffers: large RAM cost, little GE gain.
  Bind the material once and loop instances with `sceGuSetMatrix` + `sceGuDrawArray` instead.

## 11. Recipe: profiler and session log on day one

Build these before the first optimisation, not after [practice]:

- **CPU zones**: a scoped `PROF_SCOPE("name")` on the 1 us clock, self / inclusive / calls per frame,
  main thread, bounded depth. Per phase, never per instance.
- **GE passes**: section 5 (signal + finish timestamps).
- **Counters per frame**: draw calls, triangles (strips with degenerate joins counted), vertices,
  texture binds, card reads, heap (`mallinfo().uordblks`, `sceKernelMaxFreeMemSize`).
- **Capture**: a button combo records N frames (300 frames ~480 KB) into a RAM buffer allocated on
  demand, then writes one file in one write and closes it (FAT commits the size on close); nothing
  touches the card during capture [measured: PSP-3000, PC game client port]. Convert on the PC to
  Chrome trace JSON (CPU, GE, frame tracks + counters) for Perfetto, plus percentiles and a zone
  table.
- **Periodic text summary** in the log every few seconds, short lines.
- **A/B switches**: button combos or config keys that turn a suspect subsystem off or to a simpler
  mode, so a tester can produce comparable numbers without a rebuild.

**Session log rules** (next to the EBOOT, truncated each run):
- one descriptor opened once on the main thread; threads created later may lack a working directory,
  so use an absolute path [measured: PSP-3000, PC game client port];
- header: build number, compiler, console model, 32/64 MB, firmware, CPU / bus MHz, free heap. The
  build number proves which EBOOT ran (see [instrumentation.md](instrumentation.md));
- one event per line, `[sec.ms] L TAG: key=value`, fixed tag list, English, so everything greps;
- buffered; flush from the main thread only (~2 ms per card write [measured: PSP-3000, PC game client port]);
  error lines flush at once; synchronous mode during boot, off before the main loop;
- no per-frame info lines: periodic summaries and capped event logs (`if (++n <= 60)`); every
  feature logs its load result and its refusals so one card log explains a run;
- the tail is lost on a hard crash, so the last line is not where it died; the console may stamp
  files with an old date, so find the log by name, not by date.

**Trace discipline** [practice unless tagged]:
- never flush mid-frame; on buffer overflow drop new events and set a flag;
- logging can be the bottleneck: 3307 log writes cost 42 s of a 340 s run, as much as the whole
  streaming system [measured: PSP-3000, streaming map engine]; details and the fix in
  [instrumentation.md](instrumentation.md) section 7;
- dump only at a frame boundary, after draining worker queues and a short settle;
- single-writer main-thread event queue needs no locks; worker threads get their own buffers;
- a static trace buffer in .bss is cheaper than heap;
- subsystems initialised before the logger lose their output silently: buffer early messages;
- cross-check a new tracer against the old counters on its first run.

## 12. Method

One change per measurement, same reproducible path (scripted input or autopilot), same metric, on
the device, against a control and a control-again ([instrumentation.md](instrumentation.md)
section 12). When a fix does not move the metric, the wall was elsewhere: re-read the verdict. Many
"perf" problems are correctness: a freeze is a hardware hazard, a slow load is usually file opens
rather than bytes, a 30 fps stutter is a hitch (a main-thread free or flush), not steady-state cost
[practice].

## Sources

- pspsdk: https://github.com/pspdev/pspsdk (`src/gu/pspgu.h`, `src/ge/pspge.h`, `src/debug/pspdebug.h`,
  `src/debug/profiler.c`, `src/dmac/pspdmac.h`, `src/user/psputils.h`, `src/user/pspthreadman.h`,
  `src/user/pspsysmem.h`, `src/power/psppower.h`, `src/prof/pspprof.h`, `src/samples/gprof`,
  `src/libcglue/netinet/in.h`)
- pspdev gcc (builtin list taken from the compiler): https://github.com/pspdev
- uofw, `include/ge_user.h`: https://github.com/uofw/uofw
- PSPTEK (PSP Profiler, memory map, system control registers): http://daifukkat.su/docs/psptek/
- PSP cache how-to (uofw upspd mirror; also in yapspd): hitmen.c02.at/files/yapspd/
- vfpu-docs, instruction cycle tables measured on a PSP-3000: https://pspdev.github.io/vfpu-docs/
- PPSSPP GE documentation (texture cache): https://www.ppsspp.org/docs/psp-hardware/gpu/
- ARK-5 source (SystemControl kubridge, `kuKernelCall`): https://github.com/PSP-Arkfive/ARK-5
