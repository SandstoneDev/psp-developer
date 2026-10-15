# Memory map, partitions and the 64 MB mode

What memory a PSP program really has, how to find out at boot which mode it got, and the allocation traps that only
show up on the device. Build-side settings are in [build-toolchain.md](build-toolchain.md) and
[packaging-eboot.md](packaging-eboot.md); streaming pools in [streaming-architecture.md](streaming-architecture.md).

## 1. Models and RAM

| Item | Size | Source |
|---|---|---|
| Main RAM, PSP-1000 | 32 MB | `[public: PSPTEK, technical data]` |
| Main RAM, PSP-2000 / PSP-3000 | 64 MB | `[public: PSPTEK, technical data]` |
| Main RAM, PSP Go (N1000), PSP Street (E1000) | 64 MB per community reports; not confirmed by the sources here or by our hardware | unverified |
| GE eDRAM (VRAM) | 2 MB enabled by default; 4 MB of hardware on newer chip revisions | `[public: uofw ge.c, sceGeEdramInit]` |
| Main CPU scratchpad | 16 KB at physical 0x10000 | `[public: PSPTEK, memmap; yapspd 7.2]` |
| Main CPU caches | 16 KB data + 16 KB instruction | `[public: PSPTEK, technical data]` |
| Cache line | 64 bytes | `[public: PSP cache how-to]` |
| Media Engine local memory | 2 MB, at the ME's physical address 0 | `[public: PSPTEK, memmap]` |

The RAM size can be read from the RAMSIZE register (0xBC100040, bits 0-1: 0 = 16, 1 = 32, 2 = 64, 3 = 128 MB)
`[public: PSPTEK, I/O map]`, but only from kernel mode. A user-mode program usually decides from free memory at boot
(section 4).

## 2. Address map

- Main RAM is at 0x08000000 (cached view). The kernel owns the low 8 MB (0x88000000-0x887FFFFF as seen by the
  kernel); user memory starts at 0x08800000. On a 32 MB model the user partition is the 24 MB 0x08800000-0x0A000000
  `[public: yapspd 7.3]`.
- VRAM is at 0x04000000. The address window repeats the 2 MB at +2, +4 and +6 MB with different depth-buffer
  arrangements: +2 MB is a "swizzled" view, +4 MB is identical to the base, +6 MB gives a linear view of the depth
  buffer; writes through the mirrors behave like normal VRAM writes `[public: yapspd 10.2]`. So 0x04200000 is not
  automatically "the extra 2 MB": on a model with the 4 MB enabled it is real memory, otherwise a mirror. In one port,
  textures placed in the upper 2 MB of VRAM showed stripes on a PSP-3000 `[measured: PSP-3000, PC game client port]`;
  keep to the low 2 MB unless you check `sceGeEdramGetSize()` first.
- **Uncached view**: the same physical memory is visible uncached with bit 30 of the address set (0x08800000 ->
  0x48800000, 0x04000000 -> 0x44000000); the cached and uncached pointers are aliases `[public: PSP cache how-to;
  PSPTEK, memmap (KU1 segment)]`.
- `sceGeEdramGetSize()` returns the enabled eDRAM size and is a user export; `sceGeEdramSetSize()` (2 or 4 MB only;
  4 MB fails on the oldest chip revision) is exported only to the kernel library `[public: uofw ge.c + exports.exp;
  pspsdk src/ge/pspge.h]`.
- The scratchpad sits at 0x10000 in the main CPU's map `[public: PSPTEK, memmap]`. Whether your user-mode code may
  use it on a given firmware: test it on the device before you depend on it.
- Media Engine memory has no system API for a normal user-mode program. The public ME samples reach it from code
  running on the ME or through DMA set up at kernel level `[public: mcidclan me-dmacplus samples]`. Do not plan a
  normal app's memory around it; see [media-engine.md](media-engine.md).

## 3. Partitions

- User-mode memory queries (`sceKernelMaxFreeMemSize`, `sceKernelTotalFreeMemSize`) report partition 2, the user
  partition `[public: uofw sysmem partition.c]`. `PSP_MEMORY_PARTITION_USER` is 2 `[public: pspsdk
  src/user/pspmodulemgr.h]`.
- Everything a program allocates comes from that partition: its own image (code, data, bss), the libc heap block,
  every thread's stack, loaded modules (audio/video codecs, network), and the system dialogs' working memory.
- **The volatile partition**: 4 MB at 0x08400000, outside the user partition, lent to a program with
  `sceKernelVolatileMemLock` / `sceKernelVolatileMemTryLock` / `sceKernelVolatileMemUnlock(0)` `[public: pspsdk
  src/user/pspsuspend.h; PPSSPP Core/HLE/scePower.cpp]`. The calls are served by the power/suspend service
  `[public: uofw sysmem suspend.c]`, and the system expects it back. Section 6.

## 4. The 64 MB mode

On a 64 MB model the firmware alone still gives a program the 24 MB user partition. Custom firmware can enlarge it
when the EBOOT's `PARAM.SFO` asks for it with the `MEMSIZE` key.

**What the build writes** `[public: pspsdk src/base/build.mak]`:
- Current PSPSDK writes `MEMSIZE=2` by default (when `PSP_FW_VERSION` is above 390, which is the default 600).
  `PSP_LARGE_MEMORY = 1` writes `MEMSIZE=1` (with a warning that it is "not necessary"), `PSP_LARGE_MEMORY = 0`
  writes `MEMSIZE=0`.
- After changing it run `make clean`: the SFO rule has no prerequisites ([build-toolchain.md](build-toolchain.md)).

**What ARK-5 custom firmware does with it** `[public: ARK-5 source, SystemControl settings.c, syspatch.c,
Compat/PSP high_mem.c]`:
- It reads its settings (`SETTINGS.TXT`) from the memory stick; if that fails it reads the copy in internal flash.
- Unless the high-memory setting is off, and only when the EBOOT is launched as a PSP executable from the memory
  stick or the PSP Go's internal storage (launch types 0x141 / 0x152 `[public: pspsdk src/kernel/pspinit.h]`), it
  reads `MEMSIZE`: `1` -> the largest user partition, `2` -> a 40 MB user partition. The partition is resized while
  the firmware starts its modules (at `sceImpose_Driver`), before your program runs.
- It never enlarges memory on a PSP-1000, nor for PlayStation executables or the system menu.
- The largest setting asks for 55 MB (`MAX_HIGH_MEMSIZE` `[public: psp-cfw-sdk cfwmacros.h]`); `high_mem.c` caps the
  user partition at 56 - 4 - 1 = 51 MB (52,224 KB) when the firmware keeps a 4 MB partition for itself
  `[derived from high_mem.c]`.
- When high memory is forced (by the setting or by `MEMSIZE`), ARK disables its pause-game feature.

**What we measured** `[measured: PSP-3000 with ARK-5 on 6.61, recompiled game and PC game client port]`:
- `MEMSIZE=1` is a request. A console that had run a 64 MB build for weeks suddenly gave it 24 MB. Cause: the memory
  stick's FAT was damaged by interrupted USB copies, the stick mounted slowly, the firmware fell back to the
  settings copy in internal flash, and that copy said high memory off. No error anywhere; the program just got the
  small partition. Repairing the stick (or formatting it on the PSP after a backup) fixed it.
- A program cannot enlarge the partition after it has started; the custom firmware's memory call returns an error
  from inside a running game. Do not touch other partitions to get memory.
- PPSSPP honours only `MEMSIZE=1`, and only when its emulated model is not a PSP-1000; for memory-stick homebrew it
  also maps the upper 32 MB of RAM without growing the partition `[public: PPSSPP Core/PSPLoaders.cpp,
  Core/System.cpp]`.

**Detect the mode at boot**, before any big allocation (right after reading your config, before a splash screen):
- Log `sceKernelTotalFreeMemSize()` and `sceKernelMaxFreeMemSize()`, and the main thread's stack range
  (`sceKernelReferThreadStatus` on `sceKernelGetThreadId()`).
- Measured signatures on a PSP-3000: 64 MB mode, main-thread stack near 0x0B9xxxxx and ~35,600 KB free at start with
  a 14 MB EBOOT; 24 MB mode, stack just below 0x0A000000 and under 24,576 KB free `[measured: PSP-3000, recompiled
  game]`. A PSP-1000 measured 17,415 KB free before the heap with a 6.7 MB EBOOT, i.e. about 465 KB taken by the
  system and the main stack `[measured: PSP-1000, streaming map engine; derived]`.
- If the program needs more than 24 MB and free memory says it did not get it, show a fixed message and leave
  through the normal exit: on a 64 MB model "extra memory is off: enable the custom firmware's extra memory setting
  and check the memory stick"; on a PSP-1000 "this model is not supported" (telling a PSP-1000 user to enable
  extra memory is wrong advice). Keep a launch setting that fakes a low free figure so the message path can be tested
  in PPSSPP `[practice]`.
- Log the custom firmware's own configuration too, as raw bytes (`sctrlSEGetConfig` in ARK's SystemControl library;
  its struct layout differs between custom firmware versions, see the CFW SDK headers). Skip such calls on the
  emulator. An import from a library that is not loaded stays unlinked; such a call returned
  `SCE_KERNEL_ERROR_LIBRARY_NOT_YET_LINKED` (0x8002013A, `[public: pspsdk src/user/pspkerror.h]`) in PPSSPP for us
  `[measured: PPSSPP, recompiled game]`. Check the return code before trusting the bytes.

## 5. Budget from the device, plan pools not heap

- PPSSPP reports about 5 MB more free memory than the device for the same EBOOT: 40,685 vs 35,571 KB at the same
  point, and 47.9 vs 42.8 MB in another program `[measured: PSP-3000, recompiled game and PC game client port]`.
  Every budget comes from hardware logs. On the emulator, allocate a ballast block at boot (emulator free minus
  device free) so it fails where the device would `[practice]`.
- More partition is not more free heap. After boot-time pools and arenas size themselves from the larger partition
  (the point of the 64 MB mode: one streaming cache grew 4x), the free remainder settled at 4.3-5 MB on both the
  emulator and a 64 MB device. Budget fixed pools and arenas at boot, probed from real free memory; treat runtime
  `malloc` headroom as small `[measured: PSP-3000, large 3D game port]`.
- Raise budgets on the bigger partition in steps, each with an on-device soak test, not in one jump. A port that
  grew its boot-time allocations past the old 24 MB limit before audio initialisation froze some runs inside the
  audio start-up's file read `[measured: PSP-3000, large 3D game port]`.
- Code size is RAM: the EBOOT image (text + data + bss) comes out of the same user partition `[practice]`.
- Log free and largest-block figures after every boot step; the numbers tell you where memory went (one port:
  35,571 KB free at start -> 7,027 KB after a 28.4 MB arena -> 4,173 KB after a 2.8 MB audio block -> 2,812 KB during
  a movie, whose decoder took ~1.3 MB) `[measured: PSP-3000, recompiled game]`.

## 6. The volatile 4 MB partition

`sceKernelVolatileMemTryLock(0, &ptr, &size)` returned 0x08400000 and 4,096 KB `[measured: PSP-3000 and PPSSPP,
recompiled game]`. Rules we follow:

- Touch every 4 KB page and check it before trusting the size `[practice]`.
- Hold a power lock (`scePowerLock(0)`) while you own it. While locked, the power switch and sleep wait; a switch
  moved meanwhile takes effect at unlock `[measured: PSP-3000, recompiled game]`. Whether the contents survive a
  suspend was not tested.
- Unlock it before opening any system dialog (save data, on-screen keyboard, network) `[practice]`; the system needs
  it back.
- PPSSPP always grants the lock (fixed address and size) and its `scePowerLock` was not linked in our build
  (0x8002013A), while the PSP returned 0: log both return codes `[public: PPSSPP Core/HLE/scePower.cpp;
  measured: PPSSPP, recompiled game]`.
- What worked from it on a PSP-3000: GE display lists (also through the uncached view 0x484xxxxx), texture arenas,
  and the work/ring/picture buffers of an MPEG movie player. Moving those there freed ~4.1 MB of the user partition;
  the game's heap grew by 4,288 KB, and the larger streaming budget saved 3.8 ms per frame (streaming busy 60 -> 38%)
  `[measured: PSP-3000, recompiled game]`.
- A PSP-1000 port relied on it too, keeping a 3.5 MB shared data file there `[measured: PSP-1000, streaming map
  engine]`.

## 7. Heap, stacks and probes: the traps

- **The heap is one block, taken on the first malloc.** With `PSP_HEAP_SIZE_KB(-1)` the libc glue allocates
  (largest free block - threshold) as a single partition block the first time `malloc` needs memory; the default
  threshold is 512 KB; `PSP_HEAP_THRESHOLD_SIZE_KB(n)` changes it `[public: pspsdk src/libcglue/glue.c]`. A fixed
  `PSP_HEAP_SIZE_KB(n)` makes the heap exactly n KB.
- **Thread stacks come from the partition, not from your heap.** A thread created after the heap took its block
  fails with `SCE_KERNEL_ERROR_NO_MEMORY` (0x80020190) while the log shows megabytes of free heap; a smaller stack
  does not help when only a few KB are left. Symptom: subsystems started during boot work and everything started
  later is silently absent `[measured: PSP-3000, large 3D game port]` (mechanism `[derived from glue.c]`). Fix:
  raise the threshold, or create all threads early, or reserve partition blocks early and hand them out. Before
  taking memory "nobody uses", check who already lives on the remainder: one fix took 200 KB that turned out to be
  the streaming threads' stacks, and the world stopped loading `[measured: PSP-3000, large 3D game port]`.
- **Reserve for system dialogs and network** at boot: a few 512 KB partition blocks allocated high
  (`PSP_SMEM_High`) and freed right before the dialog or network start `[practice]`.
- **Stacks switched inside one thread must live inside that thread's stack.** A runtime that ran coroutines on
  separately allocated stacks was switched off by the PSP at the first file-system call from a coroutine; PPSSPP
  accepted it. Carving the coroutine stacks out of one large thread stack fixed it `[measured: PSP-3000, recompiled
  game]`. Check the real stack range with `sceKernelReferThreadStatus`.
- **GCC deletes `malloc(n); if (p) free(p);` probes.** The pair has no visible effect, so the optimiser removes it
  and the "is there room?" check always says yes; a streamer never refused work and the device ran out of memory at
  world entry `[measured: PSP-3000, PC game client port]`. Write
  `void *volatile p = malloc(n); if (p) { ((volatile char *)p)[0] = 0; free(p); }`.
- **A probe whose answer equals its search bound is not a measurement.** A binary search for "how much can malloc
  give" answered 24, 48 and 56 MB for search ranges of 24, 48 and 56 MB, three builds running, on the legacy
  toolchain; an arena sized from it hung the device later `[measured: PSP-3000, large 3D game port]`. Touch the
  memory you claim (one byte per 64 KB plus the last byte), cross-check against `sceKernelMaxFreeMemSize`, and
  change the method rather than widening the range.
- **Data shared with the GE, the Media Engine or DMA**: the CPU has no hardware coherency. Write back the data cache
  for the range before another unit reads it, invalidate before the CPU reads what another unit wrote, align shared
  buffers to 64-byte lines, and never mix cached and uncached access within one line `[public: PSP cache how-to]`.
  `sceKernelDcacheWritebackRange` / `sceKernelDcacheWritebackInvalidateRange` / `sceKernelDcacheInvalidateRange`
  are the calls (the last one only on exclusively owned, line-aligned buffers) `[public: pspsdk
  src/user/psputils.h]`.

## Sources

- PSPTEK hardware notes: http://daifukkat.su/docs/psptek/ (technical data, memory map, I/O map RAMSIZE)
- yapspd (Yet Another PSP Documentation), section 7 memory map and 10.2 VRAM mirrors:
  http://hitmen.c02.at/files/yapspd/
- PSP cache how-to (J. Fitzhardinge, 2005; uofw upspd mirror)
- uofw: https://github.com/uofw/uofw (`src/kd/ge/ge.c`, `src/kd/ge/exports.exp`, `src/kd/sysmem/partition.c`,
  `src/kd/sysmem/suspend.c`)
- PSPSDK: https://github.com/pspdev/pspsdk (`src/base/build.mak`, `src/libcglue/glue.c`,
  `src/user/pspmoduleinfo.h`, `src/user/pspsysmem.h`, `src/user/pspsuspend.h`, `src/user/pspkerror.h`,
  `src/user/pspmodulemgr.h`, `src/user/psputils.h`, `src/kernel/pspinit.h`, `src/ge/pspge.h`)
- ARK-5 custom firmware source: https://github.com/PSP-Arkfive/ARK-5 (`SystemControl/src/settings.c`,
  `SystemControl/src/syspatch.c`, `Compat/PSP/src/high_mem.c`)
- CFW SDK headers: https://github.com/pspdev/psp-cfw-sdk (`cfwmacros.h`)
- PPSSPP: https://github.com/hrydgard/ppsspp (`Core/PSPLoaders.cpp`, `Core/System.cpp`, `Core/HLE/scePower.cpp`)
- mcidclan Media Engine samples: https://github.com/mcidclan/me-dmacplus-me2sc (and the other `me-dmacplus-*`
  repositories)
