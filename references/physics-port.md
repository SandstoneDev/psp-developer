# Porting a commercial-engine-class physics system, and collision-query economics

Lessons from bringing an original game's vehicle and character physics into a PSP streaming map engine: about 30
builds from the core integrator through suspension and body collision to a full collision redesign. They apply to
any "make it drive like the original" job on PSP-class hardware.

Tag note: some timings below come from run logs whose platform was not recorded; they are tagged so and are good for
ratios, not for budgets.

## Look at the original math before reaching for a library

- Physics in engines of that era is often in-house scalar code. The system we ported was ~5-6k lines: one scalar
  turn mass (no inertia tensor), orientation kept as basis vectors rotated directly (no quaternions), a per-step
  gravity constant, frame-relative dt. That runs on a 333 MHz Allegrex without SIMD [measured: streaming map engine].
- A generic rigid-body library gives a different feel. It is fine for a small set of dynamic props, not for
  "drives like the original" [practice].
- Such engines are tuned around a fixed reference tick, typically 30 fps. A 30 fps lock on the PSP is that reference
  rate, so dt handling gets simpler and the feel matches for free [practice].
- Port function by function and keep a comment that names the original routine, so each one can be audited against
  the reference. Keep the quirks: the feel lives in them [practice]. Examples from the engine we ported:
  - the scalar turn mass;
  - the lever arm for the effective mass at a contact computed **without** subtracting the centre of mass;
  - a suspension damper fed by two velocities with a **one-sided** cap;
  - collision impulses scaled by 1/n over the frame's contact count. Write this down as a **caller contract**: a
    caller that forgets the averaging over-injects impulse on a 4-wheel landing.

## Rollout discipline

- Ship behind a toggle, default OFF, with the legacy path byte-identical (check by review that nothing calls the new
  code when OFF). Feel is accepted on hardware only [practice].
- A deterministic self-test with **hand-computed** expected values (integrator, both air-resistance branches, the
  collision-impulse split, non-zero centre-of-mass cases), runnable from the debug menu on the console. 25 checks
  caught port typos that "looks right when driving" never would [measured: streaming map engine].
- A data harness over every model in the game data catches what review misses: a reduced collision-sphere set that
  passed two code reviews let 138 of 147 vehicle models fall through a flat road in the harness (fix below)
  [measured: host data harness, streaming map engine].

## Speculative move + rollback needs its shift-out half

The usual vehicle-collision shape: speculative move -> collect contacts -> per-contact impulses -> **full pose
rollback**. The trap: **rollback alone freeze-traps on any persistent shallow contact** (a kerb, a beached
underbody). Every sub-step collides again, the pose is rolled back again, tangential motion dies, and the
settle/dormancy thresholds are never reached because gravity and the impulse form a limit cycle. Seen in a run log: a
vehicle frozen for 150 s under full throttle, one contact, vertical velocity cycling around -0.079
[measured: streaming map engine, platform not recorded].

The original pairs the rollback with a shift-out: **move out along the real contact normals by a bounded depth,
check again, revert if still colliding**. Port both halves or you have built a car trap, not a solver. Our tuning
(world units ~ metres) [practice]:
- one shift attempt per frame, from the frame-start pose; move capped at 0.5; mean depth capped at 0.15; re-check slop
  0.03 -> accept or revert;
- zero restitution when the normal speed is below 0.015, so resting contacts really rest;
- settle detection on the frozen pose (moved < 0.02 in 0.5 s, and only while up.z > 0.5, so a car on its side is not
  auto-flipped).

## Sub-step economics

- **Sub-step only above a displacement threshold** (a fraction of a unit per frame). Below it, one world query per
  frame instead of n_sub. Unconditional sub-stepping multiplies every world query by n_sub, for parked cars too
  [practice].
- **Springs and dampers belong inside the sub-step, with compression recomputed per sub-step.** Evaluating the
  spring once per frame and integrating it across sub-steps makes a pogo stick (wheel-launch oscillation)
  [measured: streaming map engine].
- Ground probes need headroom above the wheel (we start them 3 units up); probes that started at the wheel fell
  through road surfaces [measured: streaming map engine].

## The per-sphere world-scan trap

N per-sphere collision queries per frame walk the **same** collision cells N times. One sphere-vs-world scan cost
~180-380 us on target-class geometry; a vehicle body of 25 spheres x 2 sub-steps logged `push=50` and 8-10.7 ms per
frame of pure cell walking [measured: streaming map engine, platform not recorded]. If a profiler line shows a query
count equal to spheres x sub-steps, the trap is live. At the first hardware acceptance, physics collision plus probes
still cost 10-13 ms per frame against a 3 ms budget [measured: PSP-3000, streaming map engine].

Fixes, in order:
1. **One multi-sphere sweep per cell walk.** Prefilter cells and faces by the enclosing circle of the whole sphere
   set, then test every sphere against each face in a single traversal. 26 calls / 10.0 ms became 1 call / ~0.3 ms
   with identical contact math [measured: streaming map engine, platform not recorded].
2. **A reduced world-query sphere set**, kept separate from the full set used for damage. Build it as a **subset**
   (greedy farthest-point selection, at most 8), not by merging spheres into enclosing ones: merging inflated the
   hull and produced the 138-of-147 fall-through above; a subset cannot inflate (0 of 147)
   [measured: host data harness, streaming map engine]. Long vehicles: the middle of the body between query spheres
   can graze through, so raise the cap for them [practice].
3. **Cache repeated point queries.** Ground-height probes cluster (a follow camera's occlusion probes retrace the
   character's trail). A small hash + TTL cache keyed by cell and **quantised height** (so slopes and overhangs stay
   correct) collapsed ~5 scans per frame to ~1: character update 55 ms -> 0.05 ms in a dense district
   [measured: streaming map engine, platform not recorded]. The same cache (128 slots, 0.75-unit cells) on a second,
   uncached ground query called 10-18 times per frame took a 50 ms spike to ~8 ms [measured: streaming map engine,
   platform not recorded].

Collision, like every non-render consumer, must **peek** the streaming cache, never stamp or enqueue it: see
[streaming-architecture.md](streaming-architecture.md).

## Feel notes

- A faithful vehicle impulse multiplier (x1.2 on the vehicle side of a split) gave ~26 % extra bounce once body
  contacts came from our sphere set; body contacts use zero restitution [measured: streaming map engine].
- Damage from contact dv needed a scale (0.5) and a per-class factor: one very heavy vehicle class multiplied the
  punch through its mass and wrecked itself in one hit until scaled down (x0.04) [measured: streaming map engine].
- Keep a debug overlay of the collision spheres drawn with the full body basis (they must tilt with the body); it
  makes most of the above visible in one look [practice].

## Sources

No public sources: everything here is our own measurement or practice.
