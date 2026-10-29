# Lessons from statically recompiled console ports (Dreamcast / PS2 → PSP)

Lessons paid for while recompiling whole console games to the PSP (a Dreamcast SH-4 game and a PS2 game, both
translated to C and built with psp-gcc, plus native replacements for the console SDK). They apply to any large port
where the game's own code runs translated and only parts are native. Numbers are ours unless marked otherwise.

Related: [overclocking.md](overclocking.md), [optimization-profiling.md](optimization-profiling.md),
[instrumentation.md](instrumentation.md), [debug-tools.md](debug-tools.md), [rendering.md](rendering.md).

## 1. Know which clock your numbers came from

- A custom firmware clock setting can override the game's own `scePowerSetClockFrequency`, and the getters keep
  reporting 333 [measured: PSP-3000, ARK-5]. We ran 31 builds at 443 MHz while the log said 333. Every number from
  those builds was optimistic by 30–50 % [measured: same game, same scenes, 443 vs 333].
- Measure the clock inside the run: time a fixed loop with the system timer and log it (≈855 ns for our loop at
  333 MHz, ≈646 ns at 443). Put that line in every session log and check it before comparing builds.
- Benchmark and accept changes at 333. Treat an overclock as a separate experiment, labelled as such.

## 2. Count frames the way the player sees them

- With frame skip, "game frames per second" and "pictures per second" are different numbers. Our log reported game
  render calls per second (drawn **and** skipped). The on-screen counter showed drawn pictures. The gap was 3–4×
  (20.9 vs 6.0 in the same scene) [measured]. Report **drawn** frames and **game pace** (game time ÷ real time)
  together; report game frames only with that name.
- Removing slow motion (keeping the original game pace with frame skip) lowers the on-screen frame rate on a
  CPU-bound port. That is expected, not a regression: the CPU budget per second is fixed. In our case a drawn frame
  cost ≈94 ms and a logic-only frame ≈39 ms [measured]. Only making frames cheaper raises both numbers.

## 3. Profile the drawn frame before choosing a lever

- Split the profile by frame kind (drawn vs skipped) and by place (cutscene, gameplay, menus). In our port the game's
  own code was ≈1 % of a drawn frame. Our own renderer natives were ≈45 % [measured]. Rewriting game code as typed C would
  not have moved the picture rate at all.
- Amdahl applies brutally: a ×1.7 speed-up on functions worth 21 % of the time gives ≈+10 % overall, ×1.27 at most
  even if they became free.
- A sampler that rides an interrupt tick inflates **spin/wait loops**: the virtual clock stops until the next tick,
  so samples pile up in the loop. A wait loop showed 1–5 % in profiles but cost 0.1 % in measured wall time.
- Attribution breaks when a native calls back into translated code and does not restore the "current function"
  word. Time then lands on whatever ran last. Check suspicious entries against wall-clock timers around the call.

## 4. GE vs CPU on a recompiled renderer (console bench, synthetic scenes sized like the game)

| test (400 models, 14.4k vertices) | CPU time |
|---|---|
| CPU transform + light + clip per vertex (old path) | 16.5 ms |
| GE hardware transform, float vertices | 1.2 ms |
| cached display list (CALL per model) | 0.6 ms |
| lighting on CPU | ≈3.4 ms (on GE: 0) |
| near-plane clip on CPU vs GE clip | 2.1 vs 0.23 ms |

- Once the CPU is light, the GE becomes the limit. GE time for the same scene: 15.9 ms with textures in main RAM,
  **5.7 ms with textures in VRAM** [measured]. A texture pool for such a port must be VRAM-first.
- "Do the work once": convert vertices to GE format once, bake static lighting, then per frame submit only a matrix
  and a CALL. Moving only the transform to the GE inside an otherwise per-frame pipeline gained ≈3 % [measured].

## 5. Natives next to translated code

- A native is worth it only for a whole subtree with rare crossings. Typed C beat the translation by ×1.5–2.4
  (×1.7 average) on the same calls. A leaf native that synchronises the whole guest register state on every call
  was 3.4× **slower** than the translation [measured].
- Prove natives with **double execution** on a host stand: run the translated original and the native on the same
  state, compare registers and every memory write, and feed in a deliberately wrong native to prove the check
  catches it. Add a fuzz test for paths the test route never reaches (denormals, NaN).
- Natives taken from another port of a **different build** of the same game need the same proof. Copied code
  disagreed with our version's SDK in several places (sign of a constant, pointer vs value, swapped entry points).
- Replace through link-time wrapping (`-Wl,--wrap=f_<addr>`) when the native keeps the original's register
  contract: callers keep their cheap register sync and the wrapper can fall back to `__real_` per call.

## 6. Unattended runs

- **Card chain:** each test variant in its own folder (`GAME_A`, `GAME_B`, …), each with a config and a time
  limit. At a clean exit the game starts the next folder with `LoadExec`. The tester launches A once, a scripted
  pad file drives the run, and every folder writes its own log. A clean script removes old test folders after
  archiving their logs; a sync script copies by file and verifies hashes.
- **PSPLink over USB** (no card swaps): the host runs `usbhostfs_pc` + `pspsh`, the PSP runs PSPLink.
  - Under ARK the game must be built as a **PRX**: a static EBOOT is refused over this path (error 0x80020148)
    [measured by another project].
  - Never stop the host server while the PSP is connected. Stock PSPLink then hangs until a power cycle
    [documented by another project, patched there].
  - A PRX loaded through PSPLink gets ≈4 MB more user memory than the same code started from the Memory Stick (largest
    free block 11.8 MB vs 7.7 MB). Take final numbers from the card.
- PPSSPP reports ≈5 MB more free memory than the console. Use it for yes/no answers only, never for numbers or
  memory budgets.

## 7. Small traps worth a line

- Hot code placed in eDRAM: +6–8 % frame rate on a translated game [measured]. eDRAM D-cache miss 44 cycles
  vs 74 for main RAM [measured]. Watch the eDRAM budget; hot-code lists overflow quietly.
- A spin-wait on a frame counter that has no hardware access inside never advances a virtual clock. Deliver the
  pending interrupt at the loop entry instead of waiting for the next tick.
- Keep the exit path boring (fixed order, FPU flags cleared, kernel-mode helpers unloaded): rare console power-offs
  after exit appeared in a few builds and are still not explained.
