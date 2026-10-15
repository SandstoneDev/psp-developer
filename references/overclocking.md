# CPU clock and overclocking

Short version: run at the official maximum (333 MHz CPU, 166 MHz bus) through the documented API. Going above 333
is an experiment on the player's hardware; if you offer it at all, do it only through the custom firmware's own
call, at the first line of `main()`, as a player setting that is off by default, with a crash-safe fallback. Never
write the clock hardware yourself.

Related: [power-exit-sleep.md](power-exit-sleep.md), [optimization-profiling.md](optimization-profiling.md),
[real-hw-gotchas.md](real-hw-gotchas.md).

## 1. The official range

```c
scePowerSetClockFrequency(333, 333, 166);   /* pll, cpu, bus */
```

- Valid ranges per pspsdk: PLL 19-333, CPU 1-333, bus 1-167, with `cpu <= pll` and `bus * 2 <= pll`
  [public: pspsdk src/power/psppower.h]. `scePowerSetCpuClockFrequency` changes only the CPU (1-333).
- The PLL has only a few real steps (190 / 222 / 266 / 333 MHz) and the bus follows PLL / 2; a homebrew program starts
  at 222 / 111 [public: PPSSPP source, Core/HLE/scePower.cpp]. So 333 / 166 is the documented ceiling.
- Raise to 333 early; it is free CPU time [practice]. But **only raise, never lower** a clock you did not set: with a
  custom firmware clock setting active, the firmware has already replaced the scePower clock setters with stubs that
  return 0 and do nothing [public: ARK-5 source, SystemControl/src/cpuclock.c], and the getters keep reporting 333
  under a firmware overclock [measured: PSP-3000, recompiled game]. A return value of 0 proves nothing; measure
  (section 5).
- The GE fill-rate wall does not move with the CPU clock: in a measured case a CPU-only overclock changed GE time by
  0 % [measured: PSP-3000, large 3D game port]. Whether a PLL overclock also speeds the bus-side work is not measured
  here (Unknown). A memory-bound program gains less than the clock ratio (see
  [optimization-profiling.md](optimization-profiling.md)).

## 2. Above 333: only the custom firmware's call

The public CFW SDK declares

```c
void sctrlHENSetSpeed(int cpu, int bus);   /* psp-cfw-sdk include/systemctrl.h; link -lpspsystemctrl_user */
```

[public: pspdev/psp-cfw-sdk, include/systemctrl.h]. ARK-5 exports it from SystemControl (libraries
`SystemCtrlForUser` / `SystemCtrlForKernel`) [public: ARK-5 source, SystemControl/exports.exp]. On a PSP, ARK-5's
compatibility layer hooks it with its overclock handler [public: ARK-5 source, Compat/PSP/src/overclock.c]:

- `cpu` above 333 and up to 483 (`MAX_ALLOWED_FREQUENCY`) starts a stepped ramp from the stock clock; at or below 333
  it calls the normal setter [public: ARK-5 source, Compat/PSP/include/overclock.h, src/overclock.c].
- A request lower than the current overclock is ignored: the handler never walks back down. The overclock ends at the
  next reboot (it is not stored anywhere) [public: ARK-5 source, Compat/PSP/src/overclock.c].
- `sctrlHENGetSpeed` returns the last requested number, not a measured clock [public: same file].
- ARK re-applies its overclock after every resume from sleep [public: ARK-5 source, Compat/PSP/src/syspatch.c].
- ARK writes no voltage anywhere [public: ARK-5 source].

The ramp itself follows public research on the PSP's clock generator (github.com/mcidclan/psp-beyond-444mhz). This
reference deliberately does not describe how to program that hardware: use the firmware's call.

### Call it at the very first line of `main()`
Before the debug screen, threads, the log, any kernel module, the Media Engine, the GE, the volatile memory block -
the earliest point you control. Never change the clock again at run time [measured: PSP-3000, recompiled game]:
- The firmware's call at the first line of `main()`: 365 MHz stable in scripted runs and free play; an earlier
  engine saw 0 deaths in ~28 firmware ramps, the earliest call at 0.27 s after start.
- At 342 MHz: 1 power-off in 10 runs of the call. The one that died was placed later in boot (~1.5 s in, display
  already scanning, other set-up done); calls at the first line of `main()` and at the start of the game thread
  lived. Replaying the dead run's pre-steps in a test program did not reproduce it, so it may also be the console's
  own spontaneous power-off (see [power-exit-sleep.md](power-exit-sleep.md) section 4) - Unknown.
- A clock change while the GE, the ME or memory-stick DMA run is unsafe: interrupts-off only stops the main core
  [practice].

## 3. Never write the clock registers yourself

Our own writes to the PLL hardware (from a kernel helper; several step shapes, single and multi-step ramps):
**4 of 4 power-offs** in the latest tests; an older engine died in 6 of 11 clock changes, with memory-stick I/O next to
the write as the prime suspect [measured: PSP-3000, recompiled game and large 3D game port]. One board played at
~401 MHz this way once; 452 MHz switched it off on the ramp. There is no published voltage table above 333 and no
need to write one: never write voltages.

## 4. The firmware's MHz scale may be wrong for your board

ARK picks its multiplier constants by model [public: ARK-5 source, Compat/PSP/src/overclock.c], and the number you
request is not the clock you get. On our PSP-3000 board [measured: PSP-3000, recompiled game]:

| requested (ARK scale) | real CPU clock |
|---|---|
| 443 | 333 (stock) |
| 454 | 342 |
| 483 (maximum) | 365 |

ARK's lower presets (383 / 403 / 423) then land below stock (~287 / 306 / 319 MHz) on that board [derived from the
measured points and ARK's arithmetic]. Players reporting "317 MHz" are seeing this. Other boards: Unknown. Log the
requested value and the measured value, never only the request.

## 5. Measure the real clock

The API cannot tell you (section 1). Time a fixed loop against the system timer; the timer does not scale with the
CPU clock (the spin ratio matched the expected clock exactly, and vblank stayed at 60.00 Hz at 342 MHz)
[measured: PSP-3000, recompiled game]. Write the loop in asm: a C loop was a constant expression, hoisted out by GCC,
and read "1 us" in every log for weeks [measured: PSP-3000, recompiled game].

```c
static unsigned spin_us(unsigned n) {          /* n = 2,000,000; take the best of 5 */
    unsigned t0 = sceKernelGetSystemTimeLow();
    __asm__ volatile(".set push\n.set noreorder\n"
                     "1: addiu %0, %0, -1\n"
                     "   bnez  %0, 1b\n"
                     "   nop\n"
                     ".set pop\n" : "+r"(n));
    return sceKernelGetSystemTimeLow() - t0;
}
/* mhz_now = 333 * us_at_333 / us_now  (calibrate once at the known 333 MHz) */
```

PPSSPP does not advance its timer inside a JIT block; the emulator's number means nothing here.

## 6. Measured gains [measured: PSP-3000, recompiled game]

Scripted idle run, main-loop average over 399 frames:

| clock | frame | change | boot to main loop |
|---|---|---|---|
| 333 | 54.4 ms | - | 33.5 s |
| 342 | 53.0 ms | -2.6 % | 33.0 s |
| 365 | 49.7 ms | -8.6 % | 31.2 s |

In play about 9 % more frame rate at 365 MHz. Only the CPU-bound frames scale; loading hitches and frames waiting for
vblank or the GE do not. 342 alone is inside run-to-run noise for a single A/B pair.

## 7. Shipping it: player setting, crash-safe state file

- **Default off.** A setting the player turns on, with a one-line warning.
- **Refuse** in an emulator, without the expected custom firmware, or on a board you have not measured; log why.
- **State file** (one line in the save/data folder; open, write, close - the file size is only recorded on close,
  see [file-io-memory-stick.md](file-io-memory-stick.md)):
  - write `pending` before the call;
  - write `ok` after 60 s of play or at a clean exit;
  - a boot that finds `pending` skips the overclock this run, counts a failure and steps down one level next time
    (e.g. 365 -> 342); a second failure disables it;
  - changing the setting re-arms it (failures back to 0);
  - a power-off after the last exit step finds `ok` and does not count, so console-side exit power-offs cannot
    disable it.
- **Show it**: one line on screen and in the log at boot (`ACTIVE 365 MHz` / `OFF - reason`), the measured clock in
  the run header, a periodic watchdog line with clock and battery.
- **No sleep** while overclocked unless you have tested it on that board.

## 8. Risks to state to the player

- Heat and battery drain rise; the reported battery temperature may not move at all (see
  [power-exit-sleep.md](power-exit-sleep.md) section 7).
- Board variance: the same request gives different clocks, and a clock one board plays may switch another off.
- The hardware is old and out of warranty; a power-off during a save or a card write can corrupt data on the memory
  stick. Saves: write a temporary file, close, rename.
- No published voltage or memory-timing data exists above 333; you are outside every specification.

## Sources

- pspsdk (https://github.com/pspdev/pspsdk): `src/power/psppower.h`
- psp-cfw-sdk (https://github.com/pspdev/psp-cfw-sdk): `include/systemctrl.h`
- ARK-5 (https://github.com/PSP-Arkfive/ARK-5): `SystemControl/exports.exp`, `SystemControl/src/cpuclock.c`,
  `Compat/PSP/src/overclock.c`, `Compat/PSP/include/overclock.h`, `Compat/PSP/src/syspatch.c`
- PPSSPP (https://github.com/hrydgard/ppsspp): `Core/HLE/scePower.cpp`
- mcidclan, psp-beyond-444mhz (https://github.com/mcidclan/psp-beyond-444mhz) - background only
