# Instrumentation: how to know a change worked

The PSP is hard to observe. A retail unit has no debugger attached by default, the emulator gets
every cost wrong, a hard freeze eats the buffered tail of the log, and the person holding the
console is often not the person who wrote the change. The lessons below come from several ports
tested continuously on a PSP-3000 (a streaming 3D engine with hundreds of builds, a recompiled
game, a PC game client port). Profiler design is in
[optimization-profiling.md](optimization-profiling.md); debugger and crash tools in
[debug-tools.md](debug-tools.md).

## 1. The emulator answers booleans, never numbers

PPSSPP answers one question: does the code do what is written? It does not answer any question
with a number in it: milliseconds, megabytes, "how many times".

The case that settles it: a new texture-request priority in a streaming engine
[measured: PSP-3000, streaming map engine, vs PPSSPP]:

| | activations logged |
|---|---|
| PPSSPP | 375 / 363 / 52 per counter - working hard |
| PSP-3000 | 0 / 0 / 0 in every run - never fired |

Same build, same data. The emulator's different eviction timing let a texture be evicted and
re-requested while its model was still resident; on hardware that window never opened. Emulator
numbers would have shipped a dead feature as a success.

Other measured differences between the same EBOOT in PPSSPP and on a PSP-3000
[measured: PSP-3000, recompiled game, vs PPSSPP]: ~5 MB more free memory in the emulator; more open
files allowed; GE cost near zero (one frame: ~13 us emulated, 30-48 ms on the device
[measured: PSP-3000, streaming map engine]); no cache model (a native path: 13.5 us vs 55 us per
mesh); no Media Engine; its own frame pacing (an in-run A/B always reports "no difference"); the
hardware counter registers crash it; it forwards the PC keyboard and pad to the game while its
window has focus.

- Take from the emulator only booleans: it booted, it crashed, this line is in the log.
- Confirm even a boolean against a hardware log before acting. (A `region load FAIL rc=-3` seen in
  the emulator was acted on only after the same line was found in two hardware logs.)
- Never A/B a performance change in the emulator. Its CPU numbers were ~2-3x optimistic and its
  relative costs differ, so even the direction can invert [measured: PSP-3000, streaming map engine].
- Disable hardware counters and Media Engine use when an emulator is detected; do not touch the PC
  while an unattended PPSSPP run has focus [practice].

## 2. Build the instrument first, and "it reads zero" is not acceptance

Instrument-first protects you from changing code for no reason. It does not protect you from an
instrument whose silence agrees with the bug.

A cached world-store feature had this acceptance check: with the feature off, the line must read
`ws=off/0K hit=0 miss=0`. It passed, literally and uselessly. Review found the one-shot `open` call
ran 84 source lines before the settings loader that wrote the enable flag. The open always beat the
switch, the flag could never be 1, and the whole read path was dead code. A run would have printed
exactly the line declared as success [practice; incident in a streaming map engine].

Zero from "nothing has happened yet" and zero from "nothing can ever happen" are the same text. A
check that cannot tell them apart is not acceptance.

- Give the instrument as many distinct states as there are ways to fail. That readout became four:
  `off` (switch), `noidx` (no file), `fail` (open failed), `on/228K` (working).
- When writing an acceptance check, ask "what breakage would produce the same output?" If one exists, widen
  the check before the run.
- One-shot initialisation plus a setting is the classic shape of this bug. Check the order by line
  number, not by intent.

## 3. A probe must touch what it claims

A binary search for "how much can `malloc` give us" answered with its own upper bound three builds
running [measured: PSP-3000, streaming map engine]:

```
hi = 24 MB  ->  malloc reached 24576KB
hi = 48 MB  ->  malloc reached 49152KB
hi = 56 MB  ->  malloc reached 57344KB
```

Read as "there's even more RAM", the streaming arena was raised from 16 to 24 MB, and the device hung
during load (`region load FAIL rc=-11`, power cycle to recover). The heap grew lazily: `malloc` handed
out pointers that were never backed by partition memory and never written, so the probe accepted any
size; the real arena then took the memory for real and left the resident regions nothing.

- A measurement equal to the bound of its own search range is not a measurement. Change the method,
  not the range.
- Make the probe touch what it claims (one byte every 64 KB plus the last byte), so a false answer
  fails at boot where it is diagnosable.
- Print `sceKernelMaxFreeMemSize` and `sceKernelTotalFreeMemSize` [public: pspsdk src/user/pspsysmem.h]
  next to it: three independent numbers, and disagreement shows at once.
- Ship the knob as a config line next to the EBOOT so the tester can revert without a rebuild. "Put
  the old EBOOT back" loses every other change in that build.

## 4. Test the test by mutation, not by reading

In a vertex-quantisation pass, three of four mandatory tests passed against a build with vertex
writing completely disabled. Mutation found it, reading did not [practice; incident in a streaming map engine].

The oracle checked that "everything except x, y, z is byte-identical to the source", deliberately
skipping the fields the pass is allowed to change. The other tests checked stats and headers.
Together they asserted everything except the transformation itself.

A differential oracle of the form "nothing else changed" is blind by construction to the field the
pass exists to change. It complements a direct assertion; it never replaces one.

- Wrote a "the rest is untouched" check? Ask immediately what it does not look at. That is the spec
  of the direct test you still owe. Here: recompute world positions (`centre + i16 * scale`) before
  and after and compare within tolerance. The one surviving test caught an x/y swap by 486 units.
- Break the code on purpose and confirm the test goes red. A test you have only read is a test you
  have not run.
- The same checker also ignored textures, the instance table and the grid: corrupting one byte in
  each still reported "184 tiles, 0 problems". Verify a checker by corrupting a byte in every area it
  claims to cover.

## 5. A fixture that cannot produce the failure cannot test for it

Three cases in one session, all passed review and were found later [practice; incident in a streaming map engine]:
- a fixture with one model: "reads `models[0]` instead of `models[i]`" is invisible;
- checking only the first and last submesh: the middle one can be corrupted freely;
- a space-filling-curve adjacency check on a dense 16x16 grid scored 100 %, while the real map is
  71.9 % occupied and scored 92.3 %; the comment claimed "always adjacent".

Ask "can this fixture even express the failure?" Real data is sparse, holey and plural; fixtures
must be too.

Default data is a palindrome: a vertex-colour byte order was mirrored between the asset converter
and the engine for a long time, unnoticed, because every checked model was all-white (0xFF in every
byte), which is symmetric under a byte swap. It surfaced on the first asset with real per-vertex
colour. Validating a format against white, an identity matrix, a zero offset or a one-element array
validates nothing; use asymmetric content [practice].

Related: an implementer edited the acceptance instrument so their own work would pass. The edit was
correct, but the gate belongs to someone else, and the question is "would I have derived the same
gate from the definition?", not "does it agree now?" [practice].

## 6. A containment loop must catch broadly

A batch loop whose purpose was "this item failed, keep going" was hardened over three review rounds
and still missed the most likely failure [practice; incident in a streaming map engine's asset converter]:

| round | found | added |
|---|---|---|
| 1 | corrupt count -> bare `struct.error` | wrap in `ValueError` |
| 2 | `os.replace` on a locked file killed the loop | `except OSError` |
| 3 | the blob won't decompress | not covered |

Python's `lz4.block.decompress` raises `LZ4BlockError`, which is not a subclass of `ValueError`. The
exact failure the rollback existed for walked past all three guards and killed a 184-item batch.

Enumerating exception types one incident at a time never converges.
- A loop meant to survive per-item failure takes `except Exception` and records
  `type(exc).__name__`. A narrow `except` there claims "I know every way this can fail", which is
  always false. Narrow handlers belong where you handle a specific case.
- Review confirms the instance, not the class. Having found a defect, ask "what else is of the same
  kind, and where am I enumerating instead of generalising?"
- Sibling defect: a tool with no magic / version check and no `try` at all. On 3 of 53,917 entries
  decompression "succeeded" with the wrong length (43 bytes instead of 2880) and a slice did not
  notice. Check the decompressed length against the declared length.

## 7. The instrument can be the disease

A 340 s run on a PSP-3000 [measured: PSP-3000, streaming map engine]:

```
log      = 3307 writes / 42231 ms / worst single write 1580826 us
streaming= seek 21627 + read 20487 + decompress 2679 = 44793 ms
```

Logging cost as much as the entire streaming system: 12.8 ms per write on average, 1.58 s at worst,
synchronously on the main thread. It was a loop, not a tax. The biggest writer, a "surface is
untextured" diagnostic (1326 of 3406 lines), fired every frame while the world was behind:

```
world can't keep up -> untextured surfaces -> a log line every frame
   -> main thread holds the card -> loader can't read -> world can't keep up
```

Four builds went into tuning the loader while the log paid the bill.

- Buffer the log: ~4 KB writes took 3307 writes down to ~40. Keep "the last line before a freeze
  reaches the card" selectively: flush at once only lines with a fixed prefix
  (`WATCHDOG/PANIC/FAIL/CORRUPT/ABORT/WARN/STARVE`).
- Check every `if (x != last)` log gate for per-frame-ness. That one looked event-triggered, but the
  condition was a level that changed every frame. Logging a level as an event is a class of bug.
- The mirror image, same run: spike and status lines went through a lossy logger that went silent
  above queue depth 64, while the queue sat at 157-442 during driving, so exactly the lines the
  instrument existed for were dropped (41 spikes survived the run). Contention throttling and
  instrument blindness are one setting; separate them deliberately.
- Keep a counter of log writes, total ms and worst write in the regular diagnostic line
  (`log=<n>/<ms>/w<worst>`). Measure the cost of measuring.

## 8. Verify the presentation layer before the reference

When a ported formula is right but the screen or the speakers disagree, suspect the presentation
layer and the instrument before re-checking the reference behaviour. Three times in one branch
[measured: PSP-3000, streaming map engine]:
- "there's no fire": the formula was right; a helper rotated a vector but did not translate it, so the
  effect spawned at the world origin and was culled.
- "there's no shake": arithmetic said so before the run. 0.6 x 0.35 with 1.0/s decay = 210 ms, times
  our own gain of 0.6 = 0.126 units, ~3 pixels. Our gain made it invisible.
- "the jet drowns out the radio": two rounds of reasoning over a peak meter that measured only the
  effects buffer; the music went out on its own channel. A second meter showed the jet 0.8 dB quieter.

- Before re-reading the reference over "it doesn't look / sound right", compute what your own formula
  produces in pixels or dB.
- Ask what the instrument measures. A meter on one buffer does not describe a mix of two channels.
- Keep "the reference law" and "our mapping of it to pixels / samples" apart and mark the second at
  the constant. When the tester says "too short", turn the gain, not the reference timing.
- After two reasoned guesses miss, put an instrument on an invariant, not on the symptom. One line
  comparing distance travelled with the sum of requested displacements (ratio must be 1.000, terms
  printed beside it) answered on the first run what three arguments had not.

## 9. Split counters that merge two causes

A zone where the player got stuck took five builds of hunting a race that did not exist, because
one rejection counter merged "the region isn't live" with "the generation is stale" and so always
showed a plausible cause [measured: PSP-3000, streaming map engine]. The real defect: the draw
registry held 16 regions, the streaming layer could load 36, and registration was

```c
if (n_drawn < MAX_DRAWN) drawn[n_drawn++] = r;   /* didn't fit -> silently never drawn */
```

The region loaded perfectly, every table was valid, and it was never drawn, without a log line.

- Two capacity constants that must agree, in two files, with no assertion between them, is a defect
  waiting for a big enough map. Assert the relation at build time (`_Static_assert`).
- A counter incremented for two reasons always shows the reason you expect. Split it.
- A shared admission budget consumed in a fixed order is priority by order: the speculative prefetch
  pass ran first and spent all 48 admissions on requests later rejected, so the visible-geometry pass
  got none. Give speculative paths their own quota, and add the missing counter: requests dropped
  because the budget was exhausted (must read 0).
- "When the resource was last touched" is not "how long the request has waited". A staleness check
  must measure the age of the thing it checks.

## 10. Asking a human to run the test

Prose over several paragraphs once got an I/O benchmark toggled instead of the change under test,
and the run was wasted [practice]. Every request for a
hardware run ends with:

```
## WHAT TO DO
1. <one action>
2. <one action>
3. Bring back <exact file path>
```

- Numbered, imperative, one action per item.
- Say what not to touch if it could spoil the measurement.
- Name the exact path of each file to bring back.
- Say how long it takes, and warn if the game may stutter or seem to hang, or normal behaviour gets
  reported as a bug.
- State on its own line what success and failure look like on screen, not in log lines.

## 11. "Closed" is a word about a run, and the log must prove which run

- Write "closed" only after a run in which the instrument read the value that proves it, and name the
  run (build number, log file). A note once called a bug "found and closed" when it described the
  intent of an edit, not a verified run.
- Every build handed to a tester gets a new build number, shown in the XMB title, the log header and
  on screen. In the code: a `BUILD_NUM` define stamped by the build script and printed in the first
  log line.
- The log header carries build number, console model, firmware, clocks; each run writes a run sequence
  number, the console clock time and the battery state.
- Before analysing a log, check `build=` and the run line in the log you are reading, not in the file
  you think you deployed. Logs rotate (`session.log` -> `session_prev.log`); thin test folders may
  read data from another folder but write logs next to their own EBOOT; folders of one A/B set look
  alike [measured: PSP-3000, recompiled game].
- Archive logs with the folder name and build number in the file name.
- Analysis tools parse explicit `key=value` fields, never positions in a line: a compare script read
  50 Hz logs as 60 Hz after a log text change and overstated fps 1.2x
  [measured: PSP-3000, recompiled game].
- A zero-hit text search in documentation is evidence about vocabulary, not about the hardware.
  Search for what the author would call it and read the chapter list by hand before concluding
  "undocumented"; and treat overturning a colleague's citation as a change needing the same evidence
  [practice].

## 12. Hardware A/B method

On the PSP the per-iteration repeatability is good; the noise is the run's path (traffic, streaming,
what the camera sees) and run-to-run drift (warm-up, battery, card cache). Two methods.

### 12.1 Separate folders with a control and a control-again

For boot-time options (memory layout, density, anything not re-read at run time):
- one change per set; one EBOOT per variant differing only in what is measured; distinct XMB titles;
  thin folders;
- a control and a control-again: the same EBOOT in two folders. Their difference is the drift;
- same scripted input, same power state, fixed run length; compare medians over fixed windows and the
  profiler zones; first check that both variants did the same work;
- profiling costs a few ms per frame: keep it in its own folder.

Measured [measured: PSP-3000, recompiled game]: drift between two controls was small (87.7 vs 87.9 ms;
68.0 vs 68.0 ms), but one "faster" variant's -3.3 ms held ~2 ms of path effect: the scripted run had
gone another way. Per-iteration sd between two controls on the same path: 3.8 ms.

### 12.2 One-run interleaved A/B

For options that every consumer re-reads at run time, switch variants inside one boot
[measured: PSP-3000, recompiled game]:
- up to 4 variants per run, one of them a control-again (an A/A check);
- cut the run into windows of 6 iterations; each block runs every variant once in a seeded random
  order; drop the first 2 iterations of each window (settle). Options that carry state between
  windows (draw distance, streaming) need longer windows (12, settle 4);
- verdict: median of paired window-median differences with a 95 % sign-test interval; also print the
  mean with a t-test. Say "faster" or "slower" only when the interval excludes 0;
- the run reports itself: a results file next to the EBOOT, the final screen as an image, one frame
  per variant captured in a settle iteration (the display lags at least 2 iterations);
- check what the scenario exercises: a scripted walk once left the player standing for most windows;
- hardware only: PPSSPP's own pacing always yields "no difference".

Validation [measured: PSP-3000, recompiled game]: a known lever measured -1.5 ms, interval
-2.29..-1.01, where the folder method had said -3.3 ms. Resolution ~0.7 ms with 3 variants over 2400
iterations. Tester time per verdict ~30 -> 5.5 minutes. A/A mean -0.11 ms, but one A/A check came out
"slower" (+1.08 ms, interval +0.25..+2.22): treat a lone interval edge near 1 ms with care.

Sign-test interval for the median of n paired differences d (sorted): with k the largest integer
such that P(Binomial(n, 0.5) <= k-1) <= 0.025, the 95 % interval is [d(k), d(n+1-k)] [practice].

### 12.3 Rare faults

For a freeze or glitch that happens in some runs only, a single clean run proves nothing. Run at least
5 runs per arm and report counts ("0 of 6 vs 4 of 6"), not "it didn't happen" [practice].

## Sources

- pspsdk, `src/user/pspsysmem.h`: https://github.com/pspdev/pspsdk
- PPSSPP: https://github.com/hrydgard/ppsspp
