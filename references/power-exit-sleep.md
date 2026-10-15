# Power, exit and sleep

How a PSP program leaves cleanly (HOME, Exit, crash), survives suspend/resume, and why a console that "switches
itself off" is usually a kernel hang. Measurements are from a PSP-3000 (fw 6.6x, custom firmware), roughly 40-50
logged exits of one large program plus small test programs.

Related: [real-hw-gotchas.md](real-hw-gotchas.md), [media-engine.md](media-engine.md),
[overclocking.md](overclocking.md), [file-io-memory-stick.md](file-io-memory-stick.md).

## 1. Exit callback and HOME

HOME -> "Quit" only works if the program registered an exit callback and some thread sleeps with callbacks enabled.
The pspsdk samples use one pattern [public: pspsdk src/samples/controller/basic/main.c, src/samples/power/main.c]:

```c
static volatile int g_quit;

static int exit_cb(int a1, int a2, void *common) { g_quit = 1; return 0; }

static int callback_thread(SceSize args, void *argp) {
    int cbid = sceKernelCreateCallback("Exit Callback", exit_cb, NULL);
    sceKernelRegisterExitCallback(cbid);
    sceKernelSleepThreadCB();               /* callbacks run while this thread sleeps */
    return 0;
}

int SetupCallbacks(void) {
    int thid = sceKernelCreateThread("cb_thread", callback_thread, 0x11, 0xFA0, 0, 0);
    if (thid >= 0) sceKernelStartThread(thid, 0, 0);
    return thid;
}
```

- Register it at the very start of `main()`, before anything that can hang.
- The callback thread's priority must be higher (lower number) than the game thread's, or a busy game thread keeps
  HOME from doing anything [public: pspsdk src/user/pspthreadman.h, "Less if higher priority"]. An early build where
  HOME and Exit did nothing had exactly this [measured: PSP-3000, recompiled game].
- The sample calls `sceKernelExitGame()` straight from the callback. In a real program set a flag and let the game
  thread run the ordered exit below; a direct exit from the callback tears the program down under running threads,
  GE lists and claimed hardware.

## 2. Exit order [measured: PSP-3000, recompiled game]

Leave in a fixed order and commit one log line (open, append, close) **before** each step, so a hang names its step:

1. Stop audio output and any Media Engine work loop; hand the ME back (see [media-engine.md](media-engine.md)).
2. Finish the open GE list: wait briefly with `sceGuSync`, break it if it does not finish; then `sceGuTerm`.
3. Stop helper threads (profiler / counter sampling, loaders) and check that each one returned.
4. Restore any hardware registers you changed (performance counters and the like).
5. Stop and unload your kernel helper module and verify it is gone (query its info). A successful unload returns the
   module id: do not log that as a failure.
6. `sceKernelExitGame()`.

Before the ordered sequence, power-offs after exit appeared in 2 of 5 sessions with hardware counters on; a dedicated
exit test was clean after the ordered sequence with the register restore.

## 3. A kernel module must export `module_stop`

pspsdk's default export object exports only `module_start` and `module_info`
[public: pspsdk src/startup/prxexports.c; src/base/build.mak, `PRX_EXPORTS`]. The kernel finds the stop entry through
the exports, so without an exports file "stop module" returns success and runs nothing; unloading then frees code the
kernel still points at (an exception handler, a system-event handler, a replaced driver hook). Whatever calls it later
executes freed memory: intermittent hang, then power-off [measured: PSP-3000, recompiled game; the fix's A/B verdict
is open].

Give every kernel module an exports file (as pspsdk's `src/samples/template/kprx_template/exports.exp` does):

```
PSP_BEGIN_EXPORTS
PSP_EXPORT_START(syslib, 0, 0x8000)
PSP_EXPORT_FUNC(module_start)
PSP_EXPORT_FUNC(module_stop)
PSP_EXPORT_VAR(module_info)
PSP_EXPORT_END
PSP_END_EXPORTS
```

and make `module_stop` undo every registration. Alternative that removed one concrete defect in our exits: give the
exception handler back, but keep the module loaded until the reboot, so nothing the kernel can still call is freed.

## 4. "It switched itself off" = a kernel hang

A PSP that powers off by itself is in most cases not a power request: interrupts stayed off or a kernel wait never
returned, the watchdog stopped being fed, and the system controller cut power [practice; the Tachyon watchdog is
documented in public: uofw include/syscon.h]. Causes we found [measured: PSP-3000, recompiled game]:

- a kernel module unloaded without `module_stop` (section 3);
- handlers left registered over freed memory;
- an automatic exit in a test harness;
- the program's own clock-register writes ([overclocking.md](overclocking.md));
- the OS's Media Engine handlers waiting for an ME program that is no longer there (section 6).

**What remains Unknown:** after a Media Engine takeover about 5 of ~40-50 exits (~10-12 %) powered off, all after the
last logged exit step, during the reboot into the system menu, all on mains power. Our exit fix removed a real
defect (a marker in ME memory showed 0 system events reaching our handlers during exit) but not every power-off. A
plain test program without the ME or a kernel module also powered off once after `sceKernelExitGame`, and the same
console sometimes powers off by itself when entering USB mode or during boot. So part of the rate may belong to the
console (battery, power path, board) - cause partly Unknown.

Rules:
- Fixed exit order with a log line before each step (section 2).
- Test programs wait for a button before exiting; never exit automatically.
- With a 10-30 % fault rate, one to three runs per variant separate nothing: use **at least five runs per arm**, and
  run a control arm in the same session.
- Log the battery state and AC power in every run header; we have no battery-only data, which leaves that case open.

## 5. Power callbacks: suspend, resume, the power switch

Register a power callback on the same callback thread [public: pspsdk src/power/psppower.h,
src/samples/power/main.c]:

```c
static int power_cb(int unknown, int flags, void *common) {
    if (flags & (PSP_POWER_CB_POWER_SWITCH | PSP_POWER_CB_SUSPENDING)) { /* about to sleep: quiesce */ }
    else if (flags & PSP_POWER_CB_RESUMING)        { /* waking */ }
    else if (flags & PSP_POWER_CB_RESUME_COMPLETE) { /* reopen files, restart streams */ }
    return 0;
}
/* in callback_thread(): */
int pcb = sceKernelCreateCallback("Power Callback", power_cb, NULL);
scePowerRegisterCallback(-1, pcb);   /* -1 = any free slot, returns the slot */
```

- On suspend: stop issuing card reads, let the loader drain, flush and close log files. After resume, open file
  handles may be invalid on the memory stick; reopen them [practice].
- `scePowerLock(0)` holds off the power switch; a switch moved while locked fires right after `scePowerUnlock`
  [public: pspsdk src/power/psppower.h].
- `scePowerTick(PSP_POWER_TICK_ALL)` once per frame keeps the auto-sleep / auto-off timers from firing during long
  unattended runs and keeps a hung main thread from disguising itself as a power-off
  ([real-hw-gotchas.md](real-hw-gotchas.md) section 8).

### The volatile 4 MB and the power lock
`sceKernelVolatileMemLock` gives the extra 4 MB block [public: pspsdk src/user/pspsuspend.h]. Locking it also holds a
power lock: while it is held, the power switch and sleep wait, and a switch moved meanwhile fires at unlock
[measured: PSP-3000, recompiled game; PPSSPP has no power lock and the call fails there]. Side effects:
- Release it before any system dialog.
- Do not release it in the middle of the exit sequence after a kernel module or ME handler is gone: a suspend held off
  during play then fires into half-torn-down state. Keeping the lock until the reboot (the held-off request then fires
  at the system menu, with the system's own ME program back) is the safer default [measured: PSP-3000, recompiled
  game; it did not remove every exit power-off].

## 6. Sleep and the Media Engine

The OS's ME wrapper registers a system-event handler that, on standby and on some power events, busy-waits on the ME
mailbox or waits for the ME's answer without a timeout [public: uofw src/kd/me_wrapper/me_wrapper.c, eventHandler].
If your own code owns the ME when such an event arrives, that wait never ends: a kernel hang, i.e. a power-off. So:
- Before suspend completes, park your ME code and give the ME back, or keep the OS's handlers from waiting on it.
- Never leave handlers that point into your freed module or EBOOT memory.
- ME rules and the suspend park: [media-engine.md](media-engine.md).
- With an overclock active, avoid sleep entirely unless tested; the custom firmware re-applies its clock after resume
  [public: ARK-5 source, Compat/PSP/src/syspatch.c] (see [overclocking.md](overclocking.md)).

## 7. Battery and temperature logging

Put the power state into every run header and into a periodic watchdog line [public: pspsdk src/power/psppower.h]:
`scePowerIsPowerOnline`, `scePowerIsBatteryExist`, `scePowerIsBatteryCharging`, `scePowerGetBatteryLifePercent`,
`scePowerGetBatteryTemp`, plus `scePowerGetCpuClockFrequency` / `scePowerGetBusClockFrequency`.
- On our console the reported battery temperature read 20 C in every one of ~60 runs and never moved; whether that
  value is real is Unknown [measured: PSP-3000, recompiled game]. Do not rely on it as a thermal guard alone.
- The clock getters do not see a custom-firmware PLL overclock ([overclocking.md](overclocking.md)).

## Sources

- pspsdk (https://github.com/pspdev/pspsdk): `src/power/psppower.h`, `src/user/pspsuspend.h`,
  `src/user/pspthreadman.h`, `src/startup/prxexports.c`, `src/base/build.mak`,
  `src/samples/power/main.c`, `src/samples/controller/basic/main.c`, `src/samples/template/kprx_template/exports.exp`
- uofw (https://github.com/uofw/uofw): `src/kd/me_wrapper/me_wrapper.c`, `include/syscon.h`
- ARK-5 (https://github.com/PSP-Arkfive/ARK-5): `Compat/PSP/src/syspatch.c`
