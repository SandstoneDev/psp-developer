# Real-hardware gotchas: fine on PPSSPP, freezes on the PSP

The PSP's CPU runs ahead of the GE and has a real write-back data cache; PPSSPP couples CPU and GE much more
tightly, has no cache model, allows more memory and more open files, and ignores most bad accesses. So a build can be
clean in the emulator and hard-freeze on the console. Use PPSSPP for yes/no questions (boots, renders, no crash
line); take every time, memory and stability verdict from hardware [measured: PSP-3000, recompiled game].

**Signature of a hardware hazard (not a logic bug):** frame rate was fine, then a clean hard freeze mid-action; the
log stops cleanly with no miss / evict / out-of-memory line and the caches well under budget. That pattern points at
a GE / cache / memory-stick race first. Check this list in order.

Related: GE detail in [rendering.md](rendering.md), memory layout and partitions in [memory-map.md](memory-map.md),
card I/O in [file-io-memory-stick.md](file-io-memory-stick.md), exit and power-offs in
[power-exit-sleep.md](power-exit-sleep.md).

## 1. CPU writes into memory the GE may still be reading

### 1a. VRAM as a texture cache
Copying texels into a VRAM slot while the GE may still be sampling that slot for the previous frame is a
read/write hazard -> freeze. Either do not cache textures in VRAM, or double-buffer the slots and wait for the GE
(`sceGuSync` / a GE signal) before reusing one. A streaming map engine measured no frame-rate loss after dropping its
VRAM texture cache, so the win was not worth the hazard [measured: PSP-3000, streaming map engine].

### 1b. Vertex buffers reached by pointer
`sceGuDrawArray` stores a pointer in the display list, not a copy, and the GE runs the list later
[public: pspsdk src/gu/sceGuDrawArray.c]. A CPU-skinning path that writes every instance into one shared static
buffer makes every instance in the frame draw with the last instance's pose - on hardware only, because PPSSPP runs
lists nearly synchronously. Seen twice in one port (all remote players playing one animation; one actor drawn with
another's mesh) [measured: PSP-3000, large 3D game port].
Rule: a buffer referenced by a list lives until the GE has executed that frame and is never shared between
instances. Fixes: per-instance buffers, per-slot pose caches, or GE hardware skinning (bone matrices travel in the
list by value).

### 1c. GE state leaks between passes
GE registers persist across `sceGuStart`; a new list does not reset them [practice]. A pass that left a fixed-factor
blend enabled showed only a tint in PPSSPP and froze the GE on the console when the object came back on screen
[measured: PSP-3000, large 3D game port]. Every block that touches blend / alpha test / cull / texture scale restores
the neutral state at its end; draw the 2D HUD in its own list with a full state reset at its start. Details:
[rendering.md](rendering.md).

## 2. Cache coherency: write back, barrier, publish the pointer last

A loader thread that fills a buffer the GE or another thread will read:

```c
sceKernelDcacheWritebackRange(buf, nbytes);   /* push dirty lines to RAM */
__sync_synchronize();                          /* full barrier */
res->ptr = buf;                                /* publish LAST */
```

Without it the GE reads stale lines -> garbage geometry or textures, or a freeze. "Publish the pointer last, after
write-back and barrier" is the rule for every producer/consumer hand-off [practice]. The reverse direction (GE or DMA
wrote, CPU reads) needs `sceKernelDcacheInvalidateRange` before the read, on a range aligned to the 64-byte cache
line so no neighbouring data shares an invalidated line [public: pspsdk src/user/psputils.h; PSP cache how-to (uofw
upspd mirror)].

## 3. Heap and partition hazards

### 3a. Heap-lock contention between threads
`malloc` / `memalign` / `free` share one heap lock. A worker mid-allocation and the main thread freeing at the same
moment -> the main thread blocks -> visible stall. Do all streaming allocation and free on the worker; the main thread
only reads published pointers and marks slots for the worker to free. See
[streaming-architecture.md](streaming-architecture.md) (deferred free) [measured: PSP-3000, streaming map engine].

### 3b. newlib's `__malloc_lock` hooks must be recursive
newlib re-enters `__malloc_lock` from the same thread on some paths (seen: `free` merging into the top chunk, then the
trim path locks again). A plain binary semaphore behind the hooks deadlocks that thread inside an innocent `free()`,
and every other thread at its next allocation. The trigger depends on heap layout: dormant for many builds, then a
deterministic hang at the same frame after an unrelated boot-time allocation moved the top chunk
[measured: PSP-3000, large 3D game port]. Holder + depth lock:

```c
static SceUID sema = -1;               /* created at boot: sceKernelCreateSema("heap", 0, 1, 1, NULL) */
static volatile int holder, depth;
void __malloc_lock(struct _reent *r) {
    if (sema < 0) return;
    int me = sceKernelGetThreadId();
    if (holder == me) { ++depth; return; }      /* re-entry from the same thread */
    sceKernelWaitSema(sema, 1, 0); holder = me; depth = 1;
}
void __malloc_unlock(struct _reent *r) {
    if (sema < 0) return;
    if (--depth > 0) return;
    holder = 0; sceKernelSignalSema(sema, 1);
}
```

How it was found: a scripted route + a heartbeat watchdog naming the stuck phase + lock-holder tags printed by the
watchdog. `holder == the waiting thread's own id` is the proof of a self-deadlock.

### 3c. Fragmentation near the RAM ceiling
Big per-chunk `memalign`s of varying sizes fragment the heap; a large contiguous request fails with enough total free
memory. Keep real headroom, show free / largest block in the debug HUD, prefer per-chunk arenas that free whole
blocks [measured: PSP-3000, streaming map engine].

### 3d. A "take everything" heap starves the user partition
Sizing the newlib heap to all free memory leaves the user partition near zero. `malloc` and
`sceKernelTotalFreeMemSize` still look healthy, while everything allocated outside newlib fails or hangs: thread
stacks, file-open buffers, `sceKernelAllocPartitionMemory`. Signature: a 1 MB `memalign` succeeds and the next `fopen`
of a 30 KB file wedges [measured: PSP-3000, streaming map engine]. Use `PSP_HEAP_SIZE_KB(-N)` (all free minus N KB)
[public: pspsdk src/user/pspmoduleinfo.h] and log the partition figure next to the heap figure.

### 3e. Thread stacks come from the partition, not the heap
`sceKernelCreateThread` takes the stack from the user partition. Threads created during boot live; threads created
later fail with `0x80020190` (`SCE_KERNEL_ERROR_NO_MEMORY`, [public: pspsdk src/user/pspkerror.h]) while the log shows
megabytes of free heap. Shrinking the stack does not help when the remainder is ~16 KB. Fix: reserve partition memory
at the top of boot and hand it out through a create-thread wrapper. Before taking that remainder, find out who already
lives on it: a first fix reserved 200 KB and starved the streaming threads (no world on screen)
[measured: PSP-3000, large 3D game port]. Every failure branch of an I/O or audio path must log.
Details: [memory-map.md](memory-map.md).

### 3f. PPSSPP gives more memory than the device
Same EBOOT, 64 MB mode: ~47.9 MB free in PPSSPP vs ~42.8 MB on the console [measured: PSP-3000, PC game client port];
another port saw ~5,120 KB more in the emulator [measured: PSP-3000, recompiled game]. Budget from hardware logs; on
the emulator, allocate a ballast block at boot so its heap matches the device.

### 3g. GCC removes "is there room?" probes
`p = malloc(n); if (p) free(p);` has no visible effect and may be deleted, so the probe always says yes. One port's
streamer never refused a job and died of out-of-memory at world entry [measured: PSP-3000, PC game client port]. Write
`void *volatile p = malloc(n); if (p) { ((volatile char *)p)[0] = 0; free(p); }`.

## 4. Thread priorities

The scheduler is strict priority and a **lower number is a higher priority** [public: pspsdk src/user/pspthreadman.h].
A worker parked below everything starves exactly when it matters (a collision loader fell to ~0.1 MB/s while the main
thread ran 50 ms frames) [measured: PSP-3000, large 3D game port]. The exit/HOME callback thread must sit above the
game thread or HOME does nothing while the game thread spins. See
[streaming-architecture.md](streaming-architecture.md) for loader priorities and boosts.

## 5. Memory-stick writes during play

A per-frame `fflush`, a profiler CSV append or a screenshot dump to the card, while assets stream from the same card,
stalls or freezes. One in-frame log write took 107 ms [measured: PSP-3000, recompiled game]; over a 340 s run logging
cost 42.2 s (worst single write 1.58 s) against 44.8 s for the whole streaming system
[measured: PSP-3000, large 3D game port]. Gate runtime file I/O behind a debug flag, buffer the log and commit it from
another thread. The open-file limit (~8-9 extra handles on the device, 60 in PPSSPP) is a separate freeze source.
Both in [file-io-memory-stick.md](file-io-memory-stick.md).

## 6. Things PPSSPP tolerates that fault on the device

- **Out-of-bounds accesses.** PPSSPP can ignore bad memory accesses; the device faults. Treat any OOB as a real bug
  and test memory-touching changes on hardware [public: PPSSPP source, "IgnoreBadMemAccess" setting].
- **Hand-written VFPU asm.** PPSSPP does not model Allegrex pipeline hazards or a register held live across two
  separate `__asm__` blocks; a VFPU skin blend rendered identically in the emulator and distorted on the console, and
  measured no gain anyway [measured: PSP-3000, large 3D game port]. Prefer the vetted libraries (`libpspgum_vfpu`,
  `libpspmath`). Threads that touch the VFPU need `PSP_THREAD_ATTR_VFPU` [public: pspsdk src/user/pspthreadman.h].
  See [vfpu-math.md](vfpu-math.md).
- **Large triangles.** A triangle too large on screen can vanish intermittently while the camera turns, on hardware
  and not in some emulator backends; pre-split world geometry and change tessellation and the near plane together.
  Separately, edges spanning more than ~2730 px in projected Y draw extra pixels
  [measured by the PPSSPP team on a PSP]. Both in [rendering.md](rendering.md).
- **Upper VRAM on the Slim.** PSP-2000/3000 have 4 MiB of eDRAM but the system enables 2 MiB; a kernel helper can
  call `sceGeEdramSetSize` [public: uofw include/ge_kernel.h]. PPSSPP loads the helper and returns a positive UID
  anyway, and maps the upper 2 MiB as a mirror of the lower half. Detect by probe: trust only
  `sceGeEdramGetSize()` reporting 4 MiB after the call; gate every upper-half use on it; keep framebuffers in the
  lower 2 MiB; load the helper once at boot before GE init, never on a live scene
  [measured: PSP-3000, streaming map engine]. Calling an unresolved import (helper absent) crashes.

## 7. Crash catchers and the firmware's own exceptions

A catcher installed as a priority exception handler sees more than your crashes [measured: PSP-3000, PC game client
port]:

- **The firmware's own `break`.** On fw 6.6x a `break 0` inside kernel code, in a context that is not yours, fired at
  boot and at world entry. Passing it down the handler chain killed the console at boot (log cut, no report). Park
  that context silently (interrupts on, spin) and draw nothing: the game runs on.
- **Lazy FPU/VFPU enabling.** A thread's first FPU (or VFPU) instruction raises exception 11 "coprocessor unusable"
  [public: uofw include/exceptionman.h] with Cause.CE = 1 (VFPU: 2); the firmware handles it by enabling the
  coprocessor. A handler at priority 0 must chain CE = 1/2 to the firmware and report only CE = 0. Otherwise every
  new thread "crashes" and stays parked (sound thread dead, crash screen flashing at boot).
- Copies of a catcher drift between projects; before reuse, diff it against the newest copy and take both fixes.
  For symbols prefer `psp-addr2line -f -C -e <unstripped .elf>` over a map-file lookup (a map lacks many local
  symbols and named the neighbour function) [measured: PSP-3000, PC game client port]. See
  [debug-tools.md](debug-tools.md).

## 8. A hang that looks like a power-off

With nothing calling `scePowerTick` in the frame loop, only pad input resets the auto-sleep / auto-off timers; a
wedged main thread stops feeding them and the console later switches itself off. "Black screen, then it powered off"
reads as two faults and is one [measured: PSP-3000, streaming map engine]. Call
`scePowerTick(PSP_POWER_TICK_ALL)` once per frame (user mode, `-lpsppower`) [public: pspsdk src/power/psppower.h]; it
prevents nothing, it stops the hang from disguising itself. A kernel-mode hang (interrupts off, a wait without
timeout) is a different case: the watchdog ends it with a real power cut - see [power-exit-sleep.md](power-exit-sleep.md).

## 9. Debug screen and emulator display quirks

- **`pspDebugScreenInitEx` clears its whole target buffer on every call** [public: pspsdk src/debug/scr_printf.c].
  Calling it per frame to retarget the HUD erases the frame you just rendered. Call it once at start; per frame use
  only `pspDebugScreenSetBase(front_buffer)` and `pspDebugScreenSetXY`.
- **PPSSPP backend quirk.** On some PC installs one hardware backend (D3D11) drew nothing for a homebrew GE stream and
  did not composite CPU writes to VRAM (HUD invisible), while the software renderer was correct
  [measured: PPSSPP, homebrew test program]. If PPSSPP shows nothing, switch renderer before suspecting your list.
- **Format checks against default data check nothing.** Vertex colour is `0xAABBGGRR` (R in the low byte)
  [public: pspsdk src/gu/pspgu.h, GU_ABGR]; a baker that packed RGBA the other way hid for years because every
  checked model was all-white, and white reads the same both ways [measured: PSP-3000, large 3D game port].

## The discipline

- Validate on hardware after every milestone, not at the end. Keep a dev-only per-second diagnostic log so a freeze
  shows the last state.
- When PPSSPP and hardware disagree, trust hardware and walk this list in order.
- A test program waits for a button before it exits; never exit automatically (see
  [power-exit-sleep.md](power-exit-sleep.md)).
- Method: [instrumentation.md](instrumentation.md).

## Sources

- pspsdk (https://github.com/pspdev/pspsdk): `src/power/psppower.h`, `src/user/pspmoduleinfo.h`,
  `src/user/pspkerror.h`, `src/user/pspthreadman.h`, `src/user/psputils.h`, `src/gu/pspgu.h`,
  `src/gu/sceGuDrawArray.c`, `src/debug/scr_printf.c`
- uofw (https://github.com/uofw/uofw): `include/exceptionman.h`, `include/ge_kernel.h`
- PSP cache how-to (2005), uofw upspd mirror
- PPSSPP (https://github.com/hrydgard/ppsspp) and its GE hardware notes (https://www.ppsspp.org/docs/psp-hardware/gpu/)
