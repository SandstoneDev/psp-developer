# Build toolchain, Makefile, deploy and the test loops

How to get a working pspdev toolchain, build an EBOOT that boots, get it onto a PSP or into PPSSPP, and avoid the
build traps that produce a binary which links fine and then misbehaves. Packaging details (PARAM.SFO, icons, menu
sound) are in [packaging-eboot.md](packaging-eboot.md); the memory side of `PSP_LARGE_MEMORY` and the heap macros is
in [memory-map.md](memory-map.md).

Never assume where anything is installed. Discover it: `echo $PSPDEV`, `which psp-gcc`, `psp-config --pspdev-path`,
`psp-config --pspsdk-path`. The skill's `scripts/doctor.py` does this for every tool; `../docs/DEPENDENCIES.md` lists
the official sources. If a tool lives somewhere the check does not look, ask the user for the path.

## 1. Installing pspdev

The pspdev project publishes prebuilt toolchain archives (psp-gcc, binutils, newlib/libcglue, PSPSDK, pack-pbp,
mksfoex, psp-prxgen, PSPLink host tools) on its GitHub releases page `[public: pspdev installation guide]`.

- **Linux**: install the distribution's build dependencies (the guide has one page per distribution: Ubuntu, Debian,
  Fedora, Arch), download the matching `pspdev-<distro>-latest` archive from
  https://github.com/pspdev/pspdev/releases/latest , unpack it into the home folder, then
  ```sh
  export PSPDEV="$HOME/pspdev"
  export PATH="$PATH:$PSPDEV/bin"
  ```
  in the shell profile. Check with `psp-config --pspdev-path` in a new shell `[public: pspdev installation guide]`.
- **macOS**: dependencies through Homebrew (the guide lists them), the `pspdev-macos-*` archive for Apple silicon
  or Intel, the same two exports in `~/.zprofile`, and `xattr -rd com.apple.quarantine "$HOME/pspdev"` so Gatekeeper
  lets the binaries run `[public: pspdev installation guide, macOS]`.
- **Windows**: there is no native Windows build of current pspdev. Install WSL 2 (`wsl --install` from an elevated
  PowerShell, restart, open Ubuntu once), install the Ubuntu dependencies inside it, and follow the Linux steps there
  (an arm64 archive exists for ARM Windows) `[public: pspdev installation guide, Windows]`. Build inside WSL; the
  memory stick and PPSSPP are usually on the Windows side, so find out how the user's WSL sees Windows drives
  (commonly under `/mnt/`) instead of guessing a letter.

Guide: https://pspdev.github.io/installation.html (Windows: `installation/windows.html`, macOS:
`installation/macos.html`, Linux: `installation/ubuntu.html`, `debian.html`, `fedora.html`, `arch.html`).

## 2. Which compiler is this? Check before writing modern C

Run `psp-gcc --version`. Two generations are in circulation, about fifteen years apart:

- **Legacy psp-gcc 4.3.5 / newlib 1.18 (around 2010)**, often a native Windows install from an old SDK bundle. Assume
  C99 and GCC 4.3 only: no `_Static_assert`, no `_Generic`, and no `__has_include` (added in GCC 5)
  `[public: GCC release notes]`.
  - The `__has_include` trap: `#if defined(__has_include) && __has_include("x.h")` compiles without a warning on the
    old compiler and is simply false, so the `#else` branch is built silently. One port shipped a placeholder asset
    for a build and only noticed from an object-file size and a preprocessor dump
    `[measured: PSP-3000, large 3D game port]`. Gate optional headers with a plain `-DHAVE_X=1` instead.
- **Current pspdev (psp-gcc 15.x)**, from the release archives above.
  - Moving a large existing codebase from 4.3.5 to it took 4 files and 10 mechanical edits: implicit function
    declarations are errors now (add the missing `#include <stdlib.h>` etc.), `PSP_HEAP_SIZE_MAX()` is gone (write
    `PSP_HEAP_SIZE_KB(-1)`), and the old `pspTime` struct is `ScePspDateTime` with `.minute` / `.second` fields. CPU
    time on the same scene dropped by about 30% `[measured: PSP-3000, large 3D game port]`.
  - Keep the last legacy EBOOT under another name as an instant fallback while you validate the new code generation
    `[practice]`.
- If two toolchains (or two build configurations) can write the same `EBOOT.PBP` path, sooner or later the wrong one
  gets deployed. Give each its own output name and print the build number at boot (section 6) `[practice]`.
- Prefer the vetted VFPU code paths over hand-written VFPU assembly: link `-lpspgum_vfpu` instead of `-lpspgum`
  (both are built by PSPSDK) `[public: pspsdk src/gum/Makefile.am]`. Hand-rolled VFPU is the part PPSSPP is least
  able to validate for you; see [vfpu-math.md](vfpu-math.md).

## 3. Minimal Makefile

PSPSDK's `build.mak` turns a short Makefile into an ELF plus `EBOOT.PBP` `[public: pspsdk src/base/build.mak]`:

```make
TARGET = mygame
OBJS   = main.o

CFLAGS   = -O2 -G0 -Wall
CXXFLAGS = $(CFLAGS) -fno-exceptions -fno-rtti
ASFLAGS  = $(CFLAGS)

LIBS = -lpspgum_vfpu -lpspgu -lm        # only what build.mak and the specs do not already add

EXTRA_TARGETS   = EBOOT.PBP
PSP_EBOOT_TITLE = My Game
# PSP_EBOOT_ICON  = ICON0.PNG           # menu art: see packaging-eboot.md
# PSP_LARGE_MEMORY = 1                  # 64 MB mode request: see memory-map.md

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
```

- `-G0` turns off the small-data section; mixed objects with and without it give relocation surprises `[practice]`.
- `build.mak` appends `-lpspdebug -lpspdisplay -lpspge -lpspctrl -lpspnet -lpspnet_apctl` after your `LIBS`
  (user-mode builds), runs `psp-fixup-imports` on the ELF, strips it, writes `PARAM.SFO` with `mksfoex` and packs
  the EBOOT with `pack-pbp` `[public: pspsdk src/base/build.mak]`.
- `make clean` deletes the SFO and the EBOOT too; `make rebuild` = clean + all `[public: pspsdk src/base/build.mak]`.

### Never list a library twice: "stubs out of order"

Adding to `LIBS` a library that `build.mak` or psp-gcc's spec file already links (the list above, `-lpspuser`,
`-lpsputility`, the libc glue) splits that library's import stubs into two non-adjacent blocks. Then
`psp-fixup-imports` prints

```
Warning: could not fixup imports, stubs out of order.
Ensure the SDK libraries are linked in last to correct this.
```

and the build still produces an EBOOT `[public: pspsdk tools/psp-fixup-imports.c]`. It is not cosmetic: the fixup
step is what trims each import table entry's function count down to the stubs actually linked. When it gives up, the
counts stay at the full size of the stub archive, the loader writes more stub jumps than the block holds, and they
overrun into the next library's stubs. One test program declared 6 functions for one library with room for 2
`[measured: PSP-3000, homebrew test program]`.

- Treat that warning as a build error (grep the build log for it in your build script) `[practice]`.
- Check with `psp-objdump -s -j .lib.stub app.elf`: each 20-byte entry ends with the NID-table and stub-table
  pointers; consecutive stub-table pointers must differ by exactly function count x 8 bytes `[derived]`.
- Fix: put in `LIBS` only libraries nothing else links. PSPSDK's own `samples/net/simple` uses the network library
  with no `LIBS` line at all `[public: pspsdk src/samples/net/simple/Makefile.sample]`.

## 4. Module, main thread and heap declarations

```c
#include <pspkernel.h>

PSP_MODULE_INFO("MYGAME", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU);   /* VFPU needed by the gum VFPU paths */
PSP_HEAP_SIZE_KB(-1);                /* heap = largest free block minus a threshold, see memory-map.md */
PSP_HEAP_THRESHOLD_SIZE_KB(1024);    /* what stays outside the heap for thread stacks, modules, dialogs */
```

All four macros are in `pspmoduleinfo.h`; `PSP_MAIN_THREAD_STACK_SIZE_KB` and `PSP_MAIN_THREAD_PRIORITY` are there
too `[public: pspsdk src/user/pspmoduleinfo.h]`. With a negative heap size the libc glue allocates the heap as one
partition block of (largest free block - threshold) on the first `malloc`; the default threshold is 512 KB
`[public: pspsdk src/libcglue/glue.c]`. Everything allocated from the partition afterwards (thread stacks, loaded
modules, system dialogs) must fit in that threshold. Details and the traps that follow from it:
[memory-map.md](memory-map.md).

## 5. Rebuild traps

- The `PARAM.SFO` rule in `build.mak` has no prerequisites: once the file exists, a plain `make` reuses it. After
  changing `PSP_EBOOT_TITLE`, `PSP_LARGE_MEMORY`, `SFOFLAGS` or anything else that goes into the SFO, run
  `make clean` first, or the EBOOT ships with the old values `[public: pspsdk src/base/build.mak]`.
- `build.mak` tracks no header dependencies. A version or build-number header changed without `make clean` leaves
  objects with the old number `[public: pspsdk src/base/build.mak]`; add `-MMD` dependency files or clean.
- `pack-pbp` copies the menu files as they are and does not validate them: a bad `SND0.AT3` does not fail the build,
  it just does not play `[public: pspsdk tools/pack-pbp.c]`.

## 6. Build number at boot

Bump a `BUILD_NUM` on every build and print it on screen and as the first line of every log. Stale EBOOTs are easy
to test by accident (a copy that failed, an emulator that kept the old image, the other toolchain's output); a
number on screen settles it in one glance, and keying every log analysis by build number (never by position in a
log that several runs appended to) avoids reading the previous build's frames `[measured: PSP-3000, large 3D game
port]`.

## 7. Deploy to a PSP

- An EBOOT runs from `ms0:/PSP/GAME/<FOLDER>/EBOOT.PBP` on the memory stick (PSP Go: also from its internal
  storage, `ef0:` `[public: pspsdk src/kernel/pspinit.h]`); data files can sit next to it.
- The memory stick's mount point on the PC is the user's: ask for it or detect it (for example the `PSP_MS_ROOT`
  variable the doctor script reads); never assume a drive letter or mount path. Never write into other folders on
  the stick.
- Find your own folder at run time from `argv[0]` (the path the EBOOT was started from) and open data files relative
  to it, with a fallback list, instead of hard-coding `ms0:/PSP/GAME/NAME/` `[practice]`.
- USB mass-storage copies to a PSP can drop mid-write without the copy tool noticing. Copy file by file, compare a
  hash of each copied file with the source, and safely eject. A memory stick whose FAT was damaged by interrupted
  copies mounted slowly, and that alone changed how custom firmware configured memory at the next launch (see
  [memory-map.md](memory-map.md)) `[measured: PSP-3000, recompiled game]`.
- Each test variant gets its own folder and a distinct menu title (with the build number) `[practice]`.

## 8. PPSSPP as a yes/no check

PPSSPP (https://www.ppsspp.org/) answers "does it boot, render, play, not crash". It does not give hardware times or
memory figures: it reports about 5 MB more free memory than a PSP-3000 for the same EBOOT, has no cache cost model,
does not emulate the Media Engine, and accepts some stack layouts the PSP kills `[measured: PSP-3000, recompiled
game]`. Take every number from the device ([instrumentation.md](instrumentation.md)).

Driving it (by hand, by script or by an automation API) has its own rules `[measured: PPSSPP vs PSP-3000, large 3D game
port]`:

- **Close PPSSPP before copying a new EBOOT or data into its memory stick folder**; it may hold files open.
- **Start a fresh PPSSPP process for a new EBOOT.** The in-emulator reset re-runs the image already in memory and
  silently tests the old build.
- **Start it detached** from the shell or tool call that launched it, so a timed-out command does not take the
  emulator down with it.
- **Logs written with buffered file I/O reach the host file only on a clean close.** Killing the process loses the
  run's log tail, exactly as a hard freeze does on the device. Close the log in your exit path, and put the numbers
  you need on screen as well.
- **Screenshots can be stale or black** with some graphics back-ends. When a screenshot is the evidence, read the
  framebuffer back from emulated VRAM and decode it yourself, or print the metric on screen.
- **Scripted input can drop a press.** After every input step check the expected state (a log line or a memory
  value) and retry.
- While an unattended run has keyboard focus, PPSSPP forwards the PC's keyboard and pad to the game; do not type
  into it.
- PPSSPP identifies itself to the program: `sceIoDevctl("kemulator:", 3, NULL, 0, NULL, 0)` (the "is emulator"
  request) succeeds only there `[public: PPSSPP source Core/HLE/sceIo.cpp]`. Use it to skip hardware-only code (performance counters, Media
  Engine, custom firmware calls) on the emulator.

A self-driving test mode helps on both targets: a fixed camera path or input script plus a per-second metrics log, so
one run covers the whole level reproducibly and you read a log instead of watching. Gate it behind a launch setting
and ship with it off `[practice]`.

## 9. PSPLink: run and debug over USB

PSPLink (https://github.com/pspdev/psplinkusb) runs programs on the PSP straight from the PC's build folder over USB,
without copying to the memory stick `[public: pspdev debugging guide]`:

1. Copy the PSPLink release to `ms0:/PSP/GAME/` once and start it from the PSP's menu; it waits for the PC.
2. Build your program as an unencrypted PRX: `BUILD_PRX = 1` in the Makefile (CMake: `psp-cmake -DBUILD_PRX=1`).
3. In the build folder run `usbhostfs_pc` and keep it running; in a second terminal run `pspsh`.
4. In `pspsh`: `./myprogram.prx` runs it; `reset` before running the next build; `scrshot name.bmp`, `exit`,
   `poweroff`, `help`.
5. Crash addresses: `psp-addr2line -e myprogram.elf <address>` on the ELF (not the PRX), built with `-g`.
6. Debugging: `debug myprogram.prx` in `pspsh`, then `psp-gdb myprogram.elf -q` and `target remote :10001`.
7. Profiling: build with `-pg -g`, exit the program cleanly to get `gmon.out`, then `psp-gprof`.

The host tools ship with pspdev; Windows and Linux need a USB driver/permission setup described in the guide.
PSPLink shortens the edit-run loop; final checks still run the packed EBOOT from the menu, the way users start it
`[practice]`.

## Sources

- pspdev installation guide: https://pspdev.github.io/installation.html (and its `installation/*.html` pages)
- pspdev debugging guide (PSPLink, gdb, gprof): https://pspdev.github.io/debugging.html
- pspdev toolchain releases: https://github.com/pspdev/pspdev/releases
- PSPSDK: https://github.com/pspdev/pspsdk (`src/base/build.mak`, `src/user/pspmoduleinfo.h`,
  `src/libcglue/glue.c`, `src/gum/Makefile.am`, `src/samples/net/simple/Makefile.sample`,
  `src/kernel/pspinit.h`, `pspsdk/tools/psp-fixup-imports.c`, `pspsdk/tools/pack-pbp.c`)
- PSPLink: https://github.com/pspdev/psplinkusb
- PPSSPP: https://github.com/hrydgard/ppsspp (`Core/HLE/sceIo.cpp`), https://www.ppsspp.org/
- GCC 5 release notes (`__has_include`): https://gcc.gnu.org/gcc-5/changes.html
