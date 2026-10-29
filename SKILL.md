---
name: psp-developer
description: >
  Build and optimise PSP homebrew that runs well on real hardware - games, engines, ports, renderers, streaming
  demos - with the open pspdev toolchain (psp-gcc, PSPSDK), sceGu/sceGum rendering, swizzled CLUT textures, the
  64 MB mode, streaming, the VFPU, the Media Engine, audio, EBOOT.PBP packaging and hardware profiling. Use it
  WHENEVER the task is to write, port, package, debug or speed up code that targets the PSP, even if the user does
  not say "homebrew". Triggers: "PSP homebrew", "PSP engine", "EBOOT", "PARAM.SFO", "ICON0 / PIC1 / SND0",
  "sceGu / sceGum", "pspsdk build", "port X to PSP", "PSP framerate / fillrate", "PSP out of memory",
  "PSP 64 MB / large memory", "swizzled texture", "VFPU", "Media Engine", "PSP audio", "PSP overclock",
  "freezes on PSP but works in PPSSPP", "PSPLink". Carries lessons paid for on real consoles: the hazards PPSSPP
  hides, the numbers it cannot give, and an instrument-first method for proving a change on a device.
---

# PSP Developer

Knowledge for writing PSP homebrew that runs well **on real hardware**. It comes from building a region-streaming 3D
map engine, a streamed open-world 3D engine with traffic, physics, radio and a Media-Engine audio mixer, a port of a
PC game client, and a static recompiler that runs a large PS2 3D game on a PSP-3000. Most measurements were taken on
a PSP-3000; other models are noted where known.

Every non-obvious fact in this skill carries a source tag: `[public: ...]` (public documentation or source code),
`[measured: model, context]` (our hardware measurement), `[derived]` (arithmetic from tagged facts) or `[practice]`.
Keep that discipline when you add to it (last section).

## First run on a new machine or project

1. Run the read-only environment check: `python <skill folder>/scripts/doctor.py` (on Windows add `--wsl`; `--json`
   for machine-readable output). It looks for the pspdev toolchain, make, PPSSPP, PSPLink, atracdenc and ffmpeg in
   environment variables, PATH and standard install places. For anything missing, tell the user what it is, the
   official source from `docs/DEPENDENCIES.md`, the size, and ask before downloading or installing anything.
2. Ask what the doctor cannot discover, briefly:
   - which PSP model(s) the user tests on (PSP-1000 has 32 MB, later models 64 MB; see `references/memory-map.md`);
   - which firmware / custom firmware runs on them (needed for the 64 MB mode, kernel calls, overclocking);
   - how builds reach the console: the memory stick mount point on the PC (set `PSP_MS_ROOT`), or PSPLink over USB;
   - whether PPSSPP is available for quick yes/no checks;
   - what is being built (new homebrew, a port, an existing project) and the target frame rate.
3. Record the answers where the user wants them (the project's own notes file), never inside this skill's folder.
   Never assume a drive letter, a home folder or a tool location: discover it or ask.

## The golden rule: the emulator answers yes/no, the device gives the numbers

PPSSPP is the fast development emulator. It answers one kind of question: **does the code do what is written** (it
boots, it renders, this log line appears). It does not answer questions with a number in them: milliseconds,
megabytes, how many times.

- A texture-request priority feature logged hundreds of activations per run in PPSSPP and **zero in every hardware
  run** of the same build: a dead feature that emulator numbers would have shipped as a success
  [measured: PSP-3000, streaming map engine].
- The emulator-to-hardware cost ratio is not uniform, so even *relative* costs mislead: on one frame the CPU part
  went from 8.7 ms (emulator) to 18-27 ms (device), the GE part from ~13 us to 30-48 ms
  [measured: PSP-3000, streaming map engine]. In another project the device was CPU/memory-bound and the GE was not
  the wall [measured: PSP-3000, recompiled game]. Measure on the device before choosing a lever.
- PPSSPP reports ~5 MB more free memory than a PSP-3000 for the same EBOOT, allows more open files, has no cache
  model, does not emulate the Media Engine, and tolerates stack and memory-layout errors that kill a real console
  [measured: PSP-3000 vs PPSSPP]. It crashes on the hardware performance-counter registers [measured].
- Scoped exception, GE pixels only: PPSSPP's software renderer with the hardware-measured GE model (PR 22414 in
  `hrydgard/ppsspp`, see `references/ge-arithmetic.md` for its status) reproduces which pixels and depths the GE
  writes. Time, fill rate, memory and hazards stay hardware-only [public: PPSSPP news "GE bit-exact"].

So: iterate on PPSSPP, **validate on hardware**, and design defensively from the start. When something "works on
PPSSPP but freezes on the PSP", suspect a CPU-write / GE-read race, a missing data-cache write-back, a stack or
partition problem, or memory-stick I/O before anything else (`references/real-hw-gotchas.md`).

## Workflow for a new PSP project

1. **Toolchain and skeleton first.** A pspdev build that boots to a cleared screen and one triangle, deployed to
   `ms0:/PSP/GAME/<NAME>/` (or run through PSPLink), on PPSSPP and on the console. Print a build number at boot.
   `references/build-toolchain.md`, `references/packaging-eboot.md`.
2. **Instrument before features.** A session log that survives a crash, a per-phase profiler with a bottleneck
   verdict, a build number in every log line. `references/instrumentation.md`, `references/optimization-profiling.md`.
3. **Rendering core.** sceGu/sceGum init, double-buffered 16-bit framebuffer, culling, opaque then alpha pass, one
   textured mesh on screen correctly. `references/rendering.md`, `references/textures-uv.md`.
4. **Asset pipeline offline.** Quantise geometry and textures to the GE's native formats on the PC, not at runtime.
5. **Memory and streaming** when the data exceeds RAM: fixed pools, an async loader, atomic residency.
   `references/memory-map.md`, `references/streaming-architecture.md`, `references/file-io-memory-stick.md`.
6. **Profile and optimise on the device**, with A/B runs that include a control and a control-again.

Each step ends with a hardware check, not only an emulator run.

## The traps that cost the most

- **Vertices and textures the GE reads by pointer must stay untouched until the GE has drawn them.** The CPU runs
  ahead of the GE on hardware; PPSSPP executes lists almost synchronously. A shared skinning buffer drew every
  character with the last pose, on hardware only; copying texels into a VRAM slot the GE was still reading froze the
  console [measured: PSP-3000]. Per-instance buffers, double buffering, or a GE sync.
  `references/real-hw-gotchas.md`.
- **Write back the data cache, then publish the pointer.** A loader thread calls
  `sceKernelDcacheWritebackRange(buf, n)` and only then stores the pointer the main thread or the GE will read
  [public: pspsdk pspkernel.h; practice].
- **GE math is float24.** Matrices and float inputs keep 16 significant bits, so big world coordinates make the scene
  tremble while the camera moves. Render camera-relative, on every draw path [public: PPSSPP GE docs; measured].
  `references/rendering.md`, `references/ge-arithmetic.md`.
- **The GE has no X/Y clipping.** A primitive with a vertex outside the 4096-pixel screen window after the viewport
  transform is dropped whole; only the near plane clips [public: PPSSPP GE docs]. Tessellate or clip big triangles on
  the CPU; the tessellation size and the near plane are one setting.
- **s16 vertex positions are normalised** (value / 32768) in 3D transform mode: pair every s16 buffer with a scale
  matrix, or the scene collapses into a speck [public: PPSSPP GE docs; measured].
- **Vertex colour is `0xAABBGGRR`.** A baker that packs RGBA the other way round renders dark models with alpha
  holes, and all-white test data hides it because white is a palindrome [measured].
- **16-bit texture coordinates are unsigned.** Zero-centring UVs puts half the vertices across the wrap seam and
  triangles interpolate the long way round (stripes on tiled ground). Rebase into the window, cap tiling at pack
  time [measured]. `references/textures-uv.md`.
- **Thread stacks come from the user partition, not from your heap**: a thread created after boot fails with
  `0x80020190` while the log shows megabytes of free heap [measured]. Reserve early. `references/memory-map.md`.
- **The 64 MB mode is a request.** `PSP_LARGE_MEMORY = 1` sets `MEMSIZE` in PARAM.SFO; custom firmware decides at
  launch, and a slowly mounting memory stick made one console fall back to its stored "off" setting and 24 MB
  [measured: PSP-3000; public: ARK-5 source]. Check free memory at boot and say what to fix instead of crashing.
- **GCC deletes `malloc(n); if (p) free(p);` probes**, so an "is there room?" check always says yes. Touch the block
  through a volatile pointer [measured].
- **Memory-stick opens are the expensive part**: ~17 ms per `sceIoOpen`, ~34 ms per `fopen`, while bytes stream at
  15-18 MB/s [measured: PSP-3000]. One archive per region; few, long-lived handles; at most ~8 open files.
  `references/file-io-memory-stick.md`.
- **A file's size is recorded on close.** A log that is only flushed is empty after a crash: open, append, write,
  close for every commit, and buffer the log in the main loop so it does not become the bottleneck [measured].
- **Heap-lock contention**: a worker inside `malloc` and the main thread inside `free` stall each other; allocate and
  free streaming memory on the worker [measured].
- **Strict-priority scheduler**: a "polite" lowest-priority loader starves under load while the player outruns the
  world; raise it while the game waits for data [measured]. `references/streaming-architecture.md`.
- **Threads that touch the VFPU need `PSP_THREAD_ATTR_VFPU`** [public: pspsdk pspthreadman.h]; without it the state
  is corrupted across context switches, invisibly in PPSSPP. `references/vfpu-math.md`.
- **Exit in a fixed order** (audio and Media Engine, GE list, timers, kernel modules with an exported `module_stop`,
  then `sceKernelExitGame`), logging each step first; power-offs after exit are kernel hangs [measured].
  `references/power-exit-sleep.md`.
- **Overclock only through the custom firmware's documented call, at the first line of `main()`**; own clock-register
  writes switched the console off every time [measured: PSP-3000]. `references/overclocking.md`.
- **USB copies to the console drop silently**: copy file by file, hash both sides, eject safely, repair the stick's
  file system after an interrupted copy [measured]. `references/file-io-memory-stick.md`.

## Proving a change worked

You cannot step through a retail PSP, the emulator's numbers are fiction, and a hard freeze eats the buffered tail
of the log. The instrument is the only channel you have. Details and the incidents behind each line:
`references/instrumentation.md`.

- Build the instrument before the fix. "It reads zero" is not acceptance when zero also means "the code never ran":
  give the instrument a distinct state for every way it can fail.
- Test a test by breaking the thing on purpose and watching it go red. A fixture with one element cannot test a loop.
- A probe must touch what it claims to measure; a result equal to the bound of its own search range is not a
  measurement.
- Split a counter that can be bumped for two reasons.
- Decide speed questions on the device with a control and a control-again, or with variants interleaved inside one
  run and a confidence interval; one to three runs per arm separate nothing when a fault rate is 10-30 %.
- Every request for a hardware run is a numbered list: what to press, how long, what not to touch, which file to bring
  back, what success looks like by eye. "Closed" is a word about a run, with its build number, not about a change.

## Reference files

Read the one that matches the current step. Each is self-contained and ends with its public sources.

- `references/build-toolchain.md` - installing pspdev on Linux, macOS and Windows (WSL), legacy vs modern psp-gcc, the
  Makefile, the import-stub trap, heap and module macros, deploy, PPSSPP and PSPLink loops.
- `references/packaging-eboot.md` - EBOOT.PBP and PARAM.SFO layout, ICON0/ICON1/PIC0/PIC1/SND0, SND0 encodings tested
  on hardware.
- `references/memory-map.md` - 32 vs 64 MB models, partitions, the 64 MB mode and how to detect it, the volatile
  partition, VRAM, uncached mirrors, stacks, pools instead of heap.
- `references/rendering.md` - sceGu/sceGum, framebuffer, depth, passes, vertex formats, display-list safety,
  camera-relative rendering, seams, sprites, 2D passes.
- `references/textures-uv.md` - texture formats, swizzle, CLUTs, mipmaps, the texture cache, the unsigned-UV trap,
  texture fetch cost.
- `references/ge-arithmetic.md` - the GE's arithmetic and raster rules as measured on hardware by the PPSSPP team.
- `references/vfpu-math.md` - VFPU special functions, accuracy, timing, threads, safe inline assembly.
- `references/media-engine.md` - using the second CPU: start-up, cache protocol, no libc, sleep, qualification.
- `references/audio.md` - sceAudio channels and SRC, mixer threads, decoding vs card contention, music streaming.
- `references/movies-mpeg.md` - playing PSMF movies with the open sceMpeg API.
- `references/real-hw-gotchas.md` - every "fine in PPSSPP, freezes on the PSP" hazard and its fix.
- `references/power-exit-sleep.md` - exit callbacks, exit order, suspend/resume, power-offs.
- `references/overclocking.md` - official clock range, custom-firmware clock call, crash-safe stepping, risks.
- `references/file-io-memory-stick.md` - memory-stick costs, open-file limit, crash-safe logs, USB copy hygiene.
- `references/streaming-architecture.md` - region chunks, async loader, atomic residency, eviction, priorities.
- `references/optimization-profiling.md` - per-phase profiler, measuring the GE correctly, hardware counters, the
  levers that worked and the ones that did not.
- `references/instrumentation.md` - instrument-first method, mutation testing, hardware A/B design.
- `references/debug-tools.md` - PSPLink, gdb, PPSSPP's debugger, exception handlers, crash logs.
- `references/physics-port.md` - porting a commercial-engine-class physics system: sub-steps, collision economics.
- `references/recompiled-port-lessons.md` - whole-game recompiled ports: verify the clock, drawn vs game frames,
  profile the drawn frame first, GE-vs-CPU bench, proving natives, card chains and PSPLink runs.
- `references/porting-pc-codebases.md` - bringing a PC C++ codebase up on the PSP without rewriting it.
- `references/external-peripherals.md` - which channels (Wi-Fi, USB, remote port) connect a PSP to your own hardware.

## Keeping this skill alive

When a PSP lesson is learned the hard way, add it to the matching reference with the symptom, the cause, the fix and
the number. Two rules for what goes in:

1. **Every fact has a source tag**, and the source is public (open-source code, public documentation, a public article
   or forum post, with a link) or your own measurement (model, firmware, what was measured). Say whether a number is
   documented or measured: they fail differently and age differently.
2. **Never copy or paraphrase console-maker SDK documentation or headers**, even when they are on your disk. If a
   fact is known only from there, leave it out or measure it yourself.
