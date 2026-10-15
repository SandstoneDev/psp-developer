# Media Engine (ME): the PSP's second CPU

The ME is a second MIPS core next to the main CPU. The firmware uses it for its media decoders; homebrew can run its
own code there. Everything in this file is device-only: PPSSPP implements the firmware's ME services (video, ATRAC,
sceSas) as HLE on the PC and does not run code on an emulated ME [public: PPSSPP source, Core/HLE/sceSas.cpp "We just
HLE it"], so a takeover simply does nothing in the emulator. Every ME claim needs a hardware run.

## 1. What it is

- Same core speed as the main CPU: a benchmark that splits work in half between main CPU and ME gets x2.0-x2.9 at the
  same clock, with identical results on a PSP-1000 and a PSP-3000.
  [public: github.com/mcidclan/psp-me-bench (fork of another author's benchmark), README/RESULTS]
- Own caches. Its D-cache is 16 KB, 2-way, 64-byte lines; a whole-cache flush is an index writeback-invalidate
  (`cache 0x14`) over indexes 0..8191 in steps of 64, issued twice per index (two ways). 8192 is the index range of one
  way, not the cache size. [public: github.com/mcidclan/me-minimal-handler, me-tiny-lib `meDcacheWritebackInvalidateAll`]
- Memory: main RAM (after the ME is allowed the full range, see section 2), a local eDRAM at ME physical address 0
  (2 MB on the PSP-1000, 4 MB on the PSP-2000 and later), and a shared boot RAM that holds the exception vectors.
  [public: PSPTEK, memory map; github.com/mcidclan/psp-media-engine-reload]
  The eDRAM is usable (stack, private data, DMACplus transfers) but needs the ME bus clock bit and a small VME
  configuration first; read psp-media-engine-reload before relying on it. We kept everything in main RAM. [practice]
- No VFPU on the ME as far as anyone uses it: the public ME stubs enable only COP0 and the FPU (Status CU0|CU1).
  [public: github.com/mcidclan/me-tiny-lib] Plan ME work as scalar integer/FPU code. [practice]
- Hardware helpers both CPUs share: a semaphore register at 0xBC100048 usable as a spinlock (the main CPU can set only
  bit 0, the ME only bit 1) and a "post interrupt to the other CPU" register at 0xBC100044.
  [public: PSPTEK, system controller (CPUSEMA, RPCINT); github.com/mcidclan/me-spinlock, me-soft-interrupt]
- The firmware's own users: AVC video decode (sceMpeg), ATRAC decode, and the sceSas software mixer run on the ME.
  [public: PPSSPP source, Core/HLE/sceSas.cpp, Core/HW/SasAudio.h]

## 2. Two ways to run code there

**A. Take it over through the reset vector** (what pspsdk's oldest sample and most homebrew do):
1. Kernel mode on the main CPU. From a user EBOOT: kubridge `kuKernelCall(fn, &args)` (resident in ARK custom
   firmware; runs `fn` with K1 cleared and kernel level) [public: ARK-5 source, SystemControl/src/kubridge.c], or a
   small kernel PRX (mcidclan's samples load a `kcall.prx`), or LibPspExploit (me-tiny-lib).
   In our setup loading our own kernel PRX with `pspSdkLoadStartModule(path, PSP_MEMORY_PARTITION_KERNEL)` from a user
   EBOOT hard-froze the console; the kubridge call worked every time. [measured: PSP-3000, large 3D game port]
2. Copy a small reset stub to the ME reset vector (the start of the shared boot RAM, uncached kernel segment; the
   address is in the samples below), write back + invalidate the main D-cache (and invalidate the I-cache).
3. Pulse the ME reset: bit 2 of RESETEN (0xBC10004C) set, then cleared, then `sync`. The ME starts at the vector.
   [public: PSPTEK, RESETEN bit 2 = ME; pspsdk samples/me/basic; github.com/mcidclan/me-minimal-handler]
   Use read-modify-write: on our PSP-3000 the register read 0x40 (bit 6, USB) before the first start, and a plain
   write of 0x04 / 0x00 would have released that unit too. [measured: PSP-3000, large 3D game port]
4. The stub, running uncached with garbage `$sp`/`$gp`: allow the ME the full 64 MB (0xBC100040 = 2), enable bus
   clocks in 0xBC100050 (mcidclan writes 0x0F = ME, AW RegA, RegB, eDRAM; 0x06 = RegA + RegB was enough for a
   main-RAM-only loop), Status = CU0|CU1 with interrupts off, **set `$sp` to a private stack**, then jump to the
   KSEG0 (cached kernel) alias of the C loop: `fn | 0x80000000`.
   [public: github.com/mcidclan/me-tiny-lib, PSPTEK BUSCLKEN; measured: PSP-3000, large 3D game port]
   Both registers read as all ones from the ME side, so OR-ing into the "current value" there writes all ones.
   [measured: PSP-3000, large 3D game port]

**B. Keep the firmware's ME program and inject tasks**: mcidclan's psp-media-engine-safe-task (Classic, Mini and MIST
methods) runs custom tasks through the firmware's ME syscall machinery, so sleep, sceMpeg and sceAtrac keep working;
it needs per-model tables and a kernel helper. [public: github.com/mcidclan/psp-media-engine-safe-task, README and
MIST.md] We did not use it; it is the route if you need ME work and firmware decoders in one session.

## 3. Rules (each one cost a debugging session)

- **No stack after reset.** A leaf loop survives by accident; the first real call chain corrupts memory. First thing
  in the stub: point `$sp` at a private static buffer (8-16 KB). [measured: PSP-3000, large 3D game port]
- **No libc, no syscalls, no GE.** ME code may call only its own functions and pure libgcc helpers (`__udivdi3`,
  `__divdi3`). gcc turns struct assignment into `memcpy` and, with `-ftree-loop-distribute-patterns` (on at -O2),
  copy/zero loops into `memcpy`/`memset`: compile ME files with
  `#pragma GCC optimize("no-tree-loop-distribute-patterns", "no-jump-tables")`, assign fields by hand, and check the
  object with `psp-objdump -dr` for calls or relocations to anything outside it. [practice; measured: ME code that
  passed this check ran]
- **An exception on the ME is a silent hang.** Install an exception handler in the stub that stores Cause / EPC /
  BadVAddr in a shared block and halts (mcidclan's samples set the ME exception base for their handlers), and give
  the main CPU a watchdog (heartbeat or progress deadline). [public: github.com/mcidclan/me-soft-interrupt; practice]
- **Caches are not coherent** (no snooping on the PSP) [public: PSP cache how-to (uofw upspd mirror)]:
  - producer: write -> `sceKernelDcacheWritebackRange` (on the ME: its own `cache` ops) -> `sync` -> publish a flag;
  - consumer: invalidate the range -> read;
  - small flags, counters and indexes live in uncached memory: set bit 30 of the address (the KU1 segment,
    0x08800000 -> 0x48800000) [public: PSPTEK, virtual memory map];
  - a cached and an uncached variable must never share a 64-byte line: the cached line's writeback silently undoes
    the uncached write. Align shared blocks to 64 bytes and pad them to whole lines;
  - a freshly allocated block may still have dirty lines from its previous owner: writeback-invalidate it before
    the first uncached use. [public: PSP cache how-to; github.com/mcidclan/psp-media-engine-reload, "Considering
    Memory Access"]
- **The ME's cache goes stale for reloadable buffers.** A loop that invalidates its D-cache once at entry works while
  shared data is written only at boot, and breaks the moment the main CPU reuses a buffer: the ME keeps reading the
  previous tenant's lines. A writeback on the writer's side cannot fix the reader's cache on the other core. Fix: a
  publication counter (or an explicit "drop range X" command) the ME checks per work block, followed by an
  invalidate, and a handshake before the main CPU reuses the memory. [measured: PSP-3000, large 3D game port]
- **Any function returning a pointer into a reloadable region must also return a validity token** (slot +
  generation), or callers cannot detect a reload between the lookup and the use. [practice]
- **Cross-core queues: a generation-guarded single-producer/single-consumer protocol in uncached memory.** Producer
  bumps a per-item generation before writing fields; consumer snapshots item + generation, works on the copy, and
  commits only if the generation is unchanged. A command ring with the write index published last (two uncached
  stores, `sync`, then the index) is the same idea for streams. [measured: PSP-3000, large 3D game port]
- **Polling costs bus traffic.** In one port the ME's idle polls were about two thirds of its uncached accesses; an
  idle back-off of ~75-90 us between polls kept that small. The ME's uncached stores of 30-60 KB of GE words per frame
  changed the main CPU's game-logic time by 0.0 ms (39.69 vs 39.61 ms). [measured: PSP-3000, large 3D game port]
  The 0xBC100044 interrupt avoids polling but needs an ME exception handler and a kernel-mode write from the main
  CPU per wake-up. [public: github.com/mcidclan/me-soft-interrupt]
- **The ME's CP0 Count** read a constant until the ME wrote it (`mtc0 $zero, $9` at loop start); then it ran at
  ~454 MHz and wraps every ~9.5 s: calibrate it against the main CPU's 1 MHz timer over windows shorter than the
  wrap, or a 10 s statistics window shows nonsense like "25 MHz". [measured: PSP-3000, large 3D game port]
- **Do not write the clock registers (0xBC200000..)** from ME code unless you own the clock policy; some custom ME
  cores do write them at start. [public: github.com/mcidclan/psp-media-engine-custom-core; practice]

## 4. Living with the firmware

- **Taking the reset vector stops the firmware's ME services for the session**: sceMpeg video, sceAtrac, sceSas.
  Play movies first and take the ME over afterwards (e.g. when the main loop starts). [measured: PSP-3000, large 3D
  game port; derived from section 1]
- **Sleep.** The firmware's ME driver registers a sysevent handler ("SceMeRpc") that, on suspend and resume, posts a
  request to its own ME program and waits for the answer without a timeout; uofw's comment on that registration:
  "This is what makes mediaengine.prx hang on standby/poweroff". [public: uofw src/kd/me_wrapper/me_wrapper.c]
  Once your code owns the ME nobody answers: a suspend with a live ME loop powered the console off, and parking the ME
  from the user-mode power callback is too late. [measured: PSP-3000, large 3D game port] Two fixes:
  - simple: `scePowerLock(0)` at takeover, kept until exit (the power switch does nothing while the game runs);
    [public: pspsdk src/power/psppower.h]
  - full (kernel helper, e.g. through kubridge): for the session, replace the SceMeRpc handler by your own sysevent
    handler; on suspend phase 0.5 (event 0x4005) ask the ME loop to write back its D-cache and halt, then hold it in
    reset; on resume phase 0.5 (0x10005) re-enable the ME bus clock (`sceSysregMeBusClockEnable`), re-copy the stub
    and cold-start; restore the original handler before exit. Measured: park acknowledged in 16 us, sleep/wake with
    0 mismatches over 3,000 blocks after the wake-up. [measured: PSP-3000, large 3D game port] mcidclan's custom core
    does the same through its kernel PRX. [public: github.com/mcidclan/psp-media-engine-custom-core]
- **Cold start after a reset**: D-cache contents are undefined, so do not write them back; clear the tags first
  (TagLo = TagHi = 0, Index Store Tag on every I- and D-cache index), as the firmware's own ME boot code does.
  [public: uofw src/kd/me_wrapper/init.S; measured: PSP-3000, cold start identical in a 1,500-block compare]
- **Exit**: ask the ME loop to stop and wait for its acknowledgement, hold it in reset, then `sceKernelExitGame` (the
  kernel reboot restores the firmware's ME program for the XMB). [measured: PSP-3000, clean exits in every run]
- Gate the whole takeover behind a run-time config key next to the EBOOT, so a freeze is fixed by editing a text
  file, and compile the main-CPU path in forever. [practice]

## 5. Qualify in steps

1. **Heartbeat**: the ME increments a word in uncached memory; the main CPU samples it twice ~50 ms apart and logs
   TICKING / DEAD. Prove this on hardware before writing any real ME code. (Ours ticked within 7 ms of the reset
   release.) [measured: PSP-3000, homebrew test program]
2. **Shadow mode**: both CPUs compute the same blocks, the ME publishes a running hash per block, the main CPU
   compares. Build the work from one shared header so both sides are byte-comparable. Promote only after a long run
   with zero mismatches (ours: 1,731 comparisons, 53,852 blocks, 0 mismatches). [measured: PSP-3000, large 3D game port]
3. **Live**, with a deadline watchdog (e.g. 40 ms for an audio hop, 500 ms without progress): on a miss, hold the ME
   in reset and hand the work back to the main CPU for the rest of the run. [practice]
4. For PPSSPP regression runs, compile the same ME loop body once more as a normal main-CPU thread (no cache ops) so
   the protocol itself (queues, barriers, hand-back) is testable where no ME exists. [practice]

## 6. What is worth moving

- **Audio mixing** is the best fit: self-contained buffers, no syscalls, a natural deadline. A software mixer +
  resampler + time-stretcher moved to the ME saved 5.8 ms per frame on the main CPU (85.8 -> 80.0 ms per frame, game
  speed x1.07); in a replay test the main CPU's audio cost fell from 135 to 30 ms per second of game time.
  [measured: PSP-3000, large 3D game port] See [audio.md](audio.md).
- **Batch compute over plain memory** (building GE display-list words, culling) works; the ME wrote display-list
  batches for the GE with 0 failures in hardware runs. [measured: PSP-3000, large 3D game port]
- **Not worth it**: VFPU-friendly work (the ME has no VFPU), anything branchy with file I/O or syscalls, anything that
  needs a firmware decoder in the same session.
- **Judge by the main CPU's total frame time**, not by the zones that moved. Once, moving more render work to the ME
  showed +11 ms of "game code"; the pure game-logic zones were unchanged, and the extra time was main-CPU work that had
  moved into a different profiler zone. [measured: PSP-3000, large 3D game port] See
  [optimization-profiling.md](optimization-profiling.md).

## Sources

- pspsdk `samples/me/basic` (reset-vector takeover, after crazyc's original code), `src/power/psppower.h`,
  `src/user/psputils.h`: https://github.com/pspdev/pspsdk
- PSPTEK hardware notes (memory map, system controller registers RESETEN / BUSCLKEN / CPUSEMA / RPCINT):
  http://daifukkat.su/docs/psptek/
- PSP cache how-to (uofw upspd mirror), J. Fitzhardinge 2005
- uofw `src/kd/me_wrapper/me_wrapper.c`, `init.S`: https://github.com/uofw/uofw
- ARK-5 `SystemControl/src/kubridge.c`: https://github.com/PSP-Arkfive/ARK-5
- mcidclan (MIT): https://github.com/mcidclan/me-minimal-handler , https://github.com/mcidclan/me-simple-count ,
  https://github.com/mcidclan/me-spinlock , https://github.com/mcidclan/me-soft-interrupt ,
  https://github.com/mcidclan/me-tiny-lib , https://github.com/mcidclan/psp-media-engine-reload ,
  https://github.com/mcidclan/psp-media-engine-safe-task , https://github.com/mcidclan/psp-media-engine-custom-core ,
  https://github.com/mcidclan/psp-me-bench (a fork)
- PPSSPP source (`Core/HLE/sceSas.cpp`, `Core/HW/SasAudio.h`): https://github.com/hrydgard/ppsspp
