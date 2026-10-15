# Debugging and profiling toolbox

What is publicly available for finding bugs and costs in PSP homebrew, and what we learned using it on real
hardware. Three tiers, each answering a different question:

1. **PPSSPP** - does it boot, render, play, where does it crash. Fast, scriptable, but its timings and limits are
   not the console's (section 4).
2. **PSPLink over USB** - an interactive shell, `host0:` file access, module loading, exception inspection and a gdb
   server on the real console (sections 1-3).
3. **On-device instrumentation** - your own crash catcher, crash-safe logs and hardware counters, for the problems
   that only happen on hardware without a cable attached (sections 5-9).

In short: the emulator answers yes/no questions; every time, speed-up or memory figure comes from the console
[measured: PSP-3000, recompiled game]. See [real-hw-gotchas.md](real-hw-gotchas.md).

## 1. PSPLink (psplinkusb)

PSPLink starts and debugs homebrew over USB without copying files to the memory stick [public: pspdev/psplinkusb
README]. Parts:

| part | runs on | role |
|---|---|---|
| `psplink` EBOOT + PRX modules | PSP | shell server, module loader, exception catcher |
| `usbhostfs` | PSP | exposes the PC directory as `host0:` |
| `usbgdb` | PSP | gdb server |
| `usbhostfs_pc` | PC | serves the current directory, bridges the shell |
| `pspsh` | PC | the interactive shell |

Setup [public: pspdev.github.io debugging page and its psplink/windows, psplink/linux pages]:
- Extract the release `psplink.zip` into `ms0:/PSP/GAME` and start PSPLink from the XMB.
- Build your program as an unencrypted PRX: `BUILD_PRX=1` in the Makefile, or `psp-cmake -DBUILD_PRX=1`.
- PC: run `usbhostfs_pc` in the build directory and keep it open; in a second terminal run `pspsh`; at the
  `host0:/>` prompt type `./myprogram.prx`. `reset` returns to a clean state for the next build.
- Windows: the pspdev guide installs the **libusb-win32** driver with Zadig (Options -> List All Devices, pick the
  "PSP" type B entry, run Zadig with admin rights with PSPLink running and the cable connected). Prebuilt
  `usbhostfs_pc` / `pspsh` come in `pspsh-windows.zip` of the psplinkusb release. Under WSL the guide binds and
  attaches the device with usbipd on every session.
- Linux: a udev rule for vendor `054c`, product `01c9` with `MODE="0666"` lets `usbhostfs_pc` run without sudo.

What the shell gives you [public: psplinkusb psplink/shellcmd.h]: `modlist`, `thlist`, `meminfo`, `ldstart path`,
`memdump addr`, `peekw addr`, `savemem addr size path`, `disasm addr`, `cp src dst`, `scrshot file`, and an exception
group (`exprint`, `exlist`, `exresume`, `bpset`). On a crash PSPLink prints a report; feed the faulting address to
`psp-addr2line -e <elf> <addr>` (the ELF, not the PRX; build with `-g`) [public: pspdev.github.io debugging].

Lessons [measured: PSP-3000 with ARK CFW, streaming map engine]:
- **Never kill `usbhostfs_pc` abruptly** (task kill, closing the window mid-transfer). The PSP side is left mid
  transfer and the link starts a connect/reset loop about once per second that also degrades the shell. Leave
  `pspsh` with `exit`; if the loop starts, restart PSPLink on the PSP and start `usbhostfs_pc` once.
- In one setup, forwarding the device into WSL with usbipd made usbhostfs bulk transfers flap; a native Windows
  client with the libusb driver was stable.
- `ldstart` of a full game failed with `0x80020148` (SCE_KERNEL_ERROR_UNSUPPORTED_PRX_TYPE in pspsdk
  `src/user/pspkerror.h`): the game was a PBP-packed EBOOT that
  requests the large-memory partition and imports kubridge. Workaround that worked: **embed PSPLink** - behind a
  build flag plus a config switch, the game itself loads `psplink.prx` with `kuKernelLoadModule` + 
  `sceKernelStartModule` at boot. `pspsh` then attaches to the running game, `modlist` shows it resident, and the
  game's stdout streams into `pspsh`. Cost: about 2 fps (28 vs 30). The double gate keeps release builds untouched.
- While PSPLink owns the USB port the memory stick is not mounted on the PC; deploy with `cp host0:/<file> ms0:/<path>`
  from `pspsh`.

## 2. psp-gdb through PSPLink

[public: pspdev.github.io debugging page]
1. Build with `-g` (and without `-O` for readable stepping); check symbols with `psp-objdump --syms`.
2. `usbhostfs_pc` running and showing "connected to device".
3. In `pspsh`: `debug myprogram.prx` (loads it under the gdb server; `reset` fixes a stuck server).
4. `psp-gdb myprogram -q`, then `target remote :10001`. Usual commands: `b`, `c`, `s`, `n`, `finish`, `bt`, `p`,
   `info registers`.

It debugs a PRX that PSPLink loaded. A program that PSPLink cannot load (see the `ldstart` note above) needs the
embedding route plus PSPLink's own exception commands instead [derived].

## 3. psp-gprof

pspsdk ships gprof support (`pspsdk/src/prof`, sample `pspsdk/src/samples/gprof`). Build with `-pg -g`, run, exit
cleanly so `gmon.out` is written, then `psp-gprof <elf> gmon.out` [public: pspdev.github.io debugging page].
`-pg` instruments every call and shifts the timing of short functions; for frame-phase budgets use your own zones
and counters ([instrumentation.md](instrumentation.md), [optimization-profiling.md](optimization-profiling.md))
[practice].

## 4. PPSSPP as a debugger

- Keep the ELF next to `EBOOT.PBP`; the debugger reads symbols and line numbers from it [public: PPSSPP docs,
  Homebrew development with PPSSPP].
- CPU breakpoints and memory breakpoints (read, write, write-on-change), each with an optional condition expression
  and a log-only action instead of pausing [public: PPSSPP source Core/Debugger/Breakpoints.h; PPSSPP docs,
  Debugger expressions].
- Disassembly, memory view, struct viewer, console, JIT viewer and a GE view in the ImGui debugger
  [public: PPSSPP source UI/ImDebugger]; the Windows desktop build also has a separate GE debugger that steps through
  display lists and shows state, textures and framebuffers [public: PPSSPP source Windows/GEDebugger].
- **Frame dumps**: the GE recorder writes `<DISCID>_NNNN.ppdmp` into the dump directory; PPSSPP opens a `.ppdmp` like
  a game and replays that frame's GE work, which makes a rendering bug reproducible and shareable
  [public: PPSSPP source GPU/Debugger/Record.cpp].
- Settings -> Tools -> Developer tools: CPU core choice (interpreter / JIT / IR), DevMenu, debug overlays, log
  channels, and "Allow remote debugger" (a WebSocket API for scripted debugging) [public: PPSSPP docs, Developer
  tools; WebSocket API].
- Documented differences from a PSP: CPU caches not emulated, VFPU functions not bit-exact, 16-bit colour rendered
  at 32-bit, the OS simulated rather than emulated [public: PPSSPP docs, Known differences from the real PSP].

Differences we hit [measured: PSP-3000 vs PPSSPP, several projects]:
- About 5 MB more free heap in the emulator for the same large-memory EBOOT (~47.9 MB vs ~42.8 MB in one PC game
  client port). A scene that fits there can run out of memory on the console.
- Far more simultaneous open files (see section 7).
- The hardware counter block at `0xBC400000` is not emulated; touching it crashes PPSSPP. Detect the emulator first.
- The hardware-accelerated backends do not write rendered frames back into emulated VRAM, so a CPU read or a VRAM
  screenshot shows a stale frame.
- On some installs one backend never rasterised a homebrew GE stream (and CPU VRAM writes were not composited) while
  the software renderer was correct: if nothing shows, switch renderer before suspecting the display list.

## 5. The pspsdk debug library (libpspdebug)

Header `pspsdk/src/debug/pspdebug.h`.

- **Debug screen**: `pspDebugScreenInit`, `pspDebugScreenInitEx(vram, mode, setup)`, `pspDebugScreenSetBase`,
  `pspDebugScreenSetXY`, `pspDebugScreenPrintf`. `pspDebugScreenInitEx` **clears its whole target buffer on every
  call** [public: pspsdk src/debug/scr_printf.c]: calling it per frame to retarget a HUD erased the rendered frame
  [measured: PSP-3000, streaming map engine]. Call it once; per frame only `SetBase(front_buffer)` + `SetXY`.
  The built-in 8x8 font is ASCII: non-ASCII strings print as garbage blocks [measured: PSP-3000, homebrew test
  program].
- **Error handler**: `pspDebugInstallErrorHandler(handler)` registers a default exception handler through
  `sceKernelRegisterDefaultExceptionHandler`; `pspDebugDumpException(regs)` prints cause, EPC, Status, BadVAddr and
  all GPRs; `pspDebugGetStackTrace2` walks the stack heuristically (the header warns about false positives)
  [public: pspsdk src/debug/exception.c]. The samples run as kernel-mode applications (module attribute 0x1000 in
  `PSP_MODULE_INFO`), a 1.50-era model; on current firmware put the handler in a kernel PRX (section 6) [practice].
- **Remote-port serial**: `pspDebugSioInit` shuts down the headphone-remote driver, enables UART4 and powers the
  remote port; default 4800 8N1, `pspDebugSioSetBaud` up to 115200; `pspDebugSioInstallKprintf` routes Kprintf to it.
  Needs kernel mode [public: pspsdk src/debug/sio.c, samples/debug/sio]. Hardware side: see
  [external-peripherals.md](external-peripherals.md).
- **Screenshot**: `pspScreenshotSave(filename)` writes the displayed frame as BMP [public: pspdebug.h].
- **Profiler**: `pspDebugProfilerEnable/Disable/Clear/GetRegs/Print` drive the counter block at `0xBC400000`
  [public: pspsdk src/debug/profiler.c]. Two cautions:
  - `pspDebugProfilerGetRegs` reads 21 words into `PspDebugProfilerRegs`, which declares 20 fields: it writes one word
    past the struct, and the field names after `cpuck` are one register off against PSPTEK's table (PSPTEK has a
    total-stall counter at +0x0C and the local-bus counter at +0x50) [derived from pspsdk src/debug/profiler.c,
    pspdebug.h and PSPTEK, Profiler]. Use PSPTEK's offsets.
  - Reading the word at +0x50 killed the reading thread on a PSP-3000 [measured: PSP-3000, recompiled game].
    Do not read it; `pspDebugProfilerGetRegs` and `pspDebugProfilerPrint` do.

## 6. Your own crash catcher in a kernel PRX

A user EBOOT on current firmware cannot register exception handlers; ship a small kernel PRX and load it from the
EBOOT (`kuKernelLoadModule`, section 9) [practice]. A design that worked [measured: PSP-3000, recompiled game]:

- `PSP_MODULE_INFO("mycrash", PSP_MODULE_KERNEL, 1, 0)` + `PSP_NO_CREATE_MAIN_THREAD()`; register a default exception
  handler in `module_start`.
- Resolve the kernel function through the CFW's finder (`sctrlHENFindFunction`, declared in psp-cfw-sdk
  `systemctrl.h`) and fall back to the plain import. Before calling any import stub, check that it is linked (a
  linked stub is a `j` into kernel memory): in PPSSPP the imports stay unlinked and calling one jumped to a failure
  address.
- On an exception, copy GPRs, EPC, Cause, BadVAddr, Status, LO and HI into a block that the EBOOT owns, then resume the
  faulting thread in user mode inside a report function of the EBOOT. That function writes the log
  (open-append-close, section 7), draws a crash screen and waits for a button. One shot: a second exception spins.
- Decode `ExcCode = (Cause >> 2) & 31` (pspsdk's `exception.c` has a name table); for a `break`, the 20-bit code sits
  in instruction bits 6..25 [practice]. A `break` with your own code makes a tagged assert; there is no debugger to
  resume it on a retail unit [practice].

Two things the handler sees that are not your bugs [measured: PSP-3000, PC game client port]:
- **The firmware's own `break` in a foreign context** (at boot and again at world entry; kernel address 0x88045E58
  on that firmware). Passing it down the handler chain killed the console at boot. Parking that context silently
  (interrupts enabled, spin, draw nothing) let the game run on.
- **Lazy FPU/VFPU enabling.** A thread's first FPU or VFPU instruction raises exception 11 "coprocessor unusable"
  with Cause.CE = 1 (FPU) or 2 (VFPU); the firmware handles it by setting CU1 / CU2 and resuming. A handler at
  priority 0 must chain CE = 1 and CE = 2 to the firmware and report only CE = 0. Otherwise every new thread
  "crashes": the sound thread died and the crash screen flashed at boot.
- Only report exceptions whose EPC lies in your own module or threads; everything else goes down the chain unless
  you have measured, as above, that passing it on is fatal [practice].

**A kernel module must export `module_stop`.** PSPSDK's default export object exports only `module_start` and
`module_info` [public: pspsdk src/startup/prxexports.c; `PRX_EXPORTS` in src/base/build.mak]. The kernel finds the
stop entry through the exports, so `sceKernelStopModule` returned success without running anything, and the unload
freed an exception handler and a sysevent handler that the kernel still pointed at. That is a candidate cause of
intermittent power-offs after exit [measured: PSP-3000, recompiled game]. Add an exports file:

```
PSP_BEGIN_EXPORTS
PSP_EXPORT_START(syslib, 0, 0x8000)
PSP_EXPORT_FUNC_HASH(module_start)
PSP_EXPORT_FUNC_HASH(module_stop)
PSP_EXPORT_VAR_HASH(module_info)
PSP_EXPORT_END
PSP_END_EXPORTS
```

(`PRX_EXPORTS = exports.exp` in the Makefile.) `module_stop` must undo every registration (handlers, hooks, counter
state); after unloading, query the module to confirm it is gone [practice].

No frame pointers on Allegrex: for a backtrace, scan the faulting thread's stack for words that point into `.text`
and run them through `psp-addr2line -f -C -e <unstripped elf>`. Expect false positives; the real chain stands out
[measured: PSP-3000, PC game client port].

## 7. Crash-safe logs on the memory stick

- **The file size is recorded on close.** A log that is only written and flushed is 0 bytes after a crash or power
  loss. Every commit opens for append, writes and closes [measured: PSP-3000, recompiled game].
- During boot commit every line at once, so the last line names the step that died. In the main loop buffer lines
  and commit from a lower-priority thread; commit at once only for a short whitelist of panic prefixes [practice].
- **Logging can become the bottleneck**: in a 340-second hardware run, log writes cost 42.2 s (worst single write
  1.58 s) against 44.8 s for the whole streaming system, because a per-frame warning held the card while the world
  was behind [measured: PSP-3000, streaming map engine]. Count what logging costs and print it.
- `sceIoOpen` took 17-34 ms; sequential reads ran at ~15.5 MB/s [measured: PSP-3000, recompiled game].
- **Open-file limit**: a game that keeps its data files open can exhaust the small table, and then the log's own
  open fails silently. Keep at most ~8 files open in an LRU, and on an open failure (other than not-found) close the
  oldest and retry, for the log too [measured: PSP-3000, recompiled game]. Details:
  [file-io-memory-stick.md](file-io-memory-stick.md).
- HOME -> exit callback -> `sceKernelExitGame` skips your cleanup path [public: pspsdk src/user/psploadexec.h];
  close the log in the exit callback, and close it after setup instead of at program end [measured: PSP-3000,
  homebrew test program].
- The log header carries a build number, model, firmware, clocks and a run sequence number; analysis scripts parse
  named fields, never positions, and check the build before reading [practice].
- Photographing the screen is a poor channel; write to `ms0:` next to the EBOOT and read it on the PC [practice].
- Crash-safe saves: write a temporary file, close it, rename it into place [practice].

## 8. Hardware performance counters

The counter block at `0xBC400000` (enable word + 20 counters: system and CPU clocks, stall classes, sleep, bus,
cached/uncached loads and stores, I-cache and D-cache misses, write-backs, COP0/FPU/VFPU instruction counts, local bus)
[public: PSPTEK, Profiler]. PSPTEK's typical use: disable, zero the counters, enable. It is kernel-only;
PSPTEK lists a "make profiler accessible in user mode" bit in the register at `0xBC000030` with a question mark (we did
not try it).

What worked [measured: PSP-3000, recompiled game]: a kernel thread in the crash PRX samples the counters in 50 ms
windows, never resets them while counting, never reads the +0x50 word, starts only after the emulator check, and is
stopped (enable word restored, counters cleared) in `module_stop`. Reading through kubridge from the game cost
~65 us per call. Typical figures for a recompiled game: CPI 2.6-2.9 and ~50 % memory-stall cycles.
Interpretation and the cache side: [optimization-profiling.md](optimization-profiling.md).

## 9. kubridge: kernel calls from a user EBOOT

`kubridge.h` and `libpspkubridge` come with psp-cfw-sdk [public: pspdev/psp-cfw-sdk include/kubridge.h,
src/KUBridge]; the CFW provides the library at run time (ARK-5 exports it as `KUBridge` from SystemControl)
[public: ARK-5 source, SystemControl/src/kubridge.c].

- `kuKernelCall(func, &args)` runs a kernel-space function with up to 12 arguments (`KernelCallArg.arg1..arg12`,
  results in `ret1`/`ret2`); ARK's implementation sets K1 to 0 and the user level to 8 around the call.
  `kuKernelCallExtendStack(func, &args, stack_size)` does the same on a larger stack.
- `kuKernelLoadModule(path, flags, opt)` loads a kernel PRX from a user program (then `sceKernelStartModule`).
- Also `kuKernelPeekw` / `kuKernelPokew`, `kuKernelMemcpy`, `kuKernelGetModel`, `kuKernelFindModuleByName`,
  `kuKernelIcacheInvalidateAll`.

Only present on a CFW that provides KUBridge: in PPSSPP the import is unlinked, so check it before calling and run
without the kernel helpers there [measured: PPSSPP].

## Sources

- psplinkusb: https://github.com/pspdev/psplinkusb (README, `psplink/shellcmd.h`)
- pspdev debugging guide: https://pspdev.github.io/debugging.html , https://pspdev.github.io/psplink/windows.html ,
  https://pspdev.github.io/psplink/linux.html
- pspsdk: https://github.com/pspdev/pspsdk (`src/debug/pspdebug.h`, `exception.c`, `scr_printf.c`, `sio.c`,
  `profiler.c`, `src/startup/prxexports.c`, `src/base/build.mak`, `src/user/psploadexec.h`, `src/prof`,
  `src/samples/debug`, `src/samples/gprof`)
- psp-cfw-sdk: https://github.com/pspdev/psp-cfw-sdk (`include/kubridge.h`, `include/systemctrl.h`)
- ARK-5: https://github.com/PSP-Arkfive/ARK-5 (`SystemControl/src/kubridge.c`, `SystemControl/exports.exp`)
- PSPTEK, Profiler and system control registers: http://daifukkat.su/docs/psptek/
- PPSSPP docs: https://www.ppsspp.org/docs/development/homebrew-development/ ,
  https://www.ppsspp.org/docs/development/developer-tools/ ,
  https://www.ppsspp.org/docs/development/known-differences/ ,
  https://www.ppsspp.org/docs/development/debugger/expressions/ ,
  https://www.ppsspp.org/docs/reference/websocket-api/
- PPSSPP source: https://github.com/hrydgard/ppsspp (`Core/Debugger/Breakpoints.h`, `UI/ImDebugger`,
  `Windows/GEDebugger`, `GPU/Debugger/Record.cpp`)
