# VFPU: what the special functions compute, what the unit costs, when to use it

The VFPU is the Allegrex's vector coprocessor (COP2): 128 single-precision registers seen as 8 matrices of 4x4
(M000..M700), addressable as single elements (S), columns (C), rows (R) and matrices (M / E transposed).
Use this file for: the real numerics of `vsin vcos vrot vrcp vrsq vsqrt vexp2 vlog2 vasin`, measured latencies,
register and thread discipline under pspdev gcc, hazards and errata, and when VFPU code pays off.

## 1. What the hardware computes

- `vrcp vrsq vsqrt vexp2 vlog2 vsin vcos vasin` (and `vrot` = sin and cos at once) are a table-driven quadratic
  interpolator, not IEEE results. The top 7 mantissa bits pick one of 128 segments; each segment has a constant, a
  linear and a squared coefficient; the squared term goes through a squarer that rounds t^2 up to a multiple of 256.
  Inside every run of 64 consecutive inputs the output is exactly linear. [public: PPSSPP blog "VFPU math"]
- The result is masked to a multiple of 4 ulps: ~22 significant bits instead of 24, biased toward zero, never rounded.
  `vsin` truncates in the ulps of the segment's largest output; `vasin` keeps one extra bit when a value climbs into
  the next binade (1.25 % of its outputs have 23 bits); `vlog2` = exponent + interpolated log2 of the mantissa with
  per-level coefficient truncation, summed and truncated toward zero. [public: PPSSPP blog "VFPU math"]
- Deterministic: the same input gives the same output; the PPSSPP model of it is bit-exact over all 2^32 inputs and was
  checked against a real PSP with pspautotests `cpu/vfpu/exact`. [public: PPSSPP blog "VFPU math", PR #22343]
- Error bounds (vfpu-docs, each backed by an on-device accuracy test): `vrcp`/`vnrcp` 6.3e-7 relative, `vrsq` 7.3e-7
  relative, `vsqrt` 7.1e-7 relative, `vexp2`/`vrexp2` 7.2e-7 relative (input >= 128 gives +inf, <= -127 gives 0),
  `vsin`/`vnsin` 4.8e-7 absolute, `vcos` 4.0e-7 absolute, `vlog2` 3.0e-5 absolute (an order of magnitude worse than
  the rest; a `pow` built on it lands near 1e-5 relative), `vasin` ~2.5e-7 for |x| < 0.5 but up to 2.0e-2 absolute
  toward +-1. [public: vfpu-docs, inst-vfpu-desc.yaml accuracy]
- Angles are quarter turns: `vsin(x)` = sin(x * pi/2), so 4.0 is a full circle; `vasin` returns asin(x) / (pi/2).
  Convert radians with `vcst.s Sxxx, VFPU_2_PI` + `vmul.s`. [public: vfpu-docs, vsin/vcos/vasin]
- Reduce the angle before converting. Float spacing at a few hundred radians is ~3e-5, so a naive `fmodf` with a
  float 2*pi loses ~1e-4; Cody-Waite reduction (2*pi = 6.28125 + 1.9353071795864769e-3, exact high part) keeps it at
  float precision. [derived]

### Floating-point format, NaN, denormals

- Rounding is hardwired to round-to-nearest; denormal inputs are treated as zero and denormal results flush to zero.
  [public: vfpu-docs, introduction "Floating point format"]
- The main FPU is a different story. A compare against +-inf/NaN on the FPU raised a floating-point exception and the
  console powered off with no log line; PPSSPP stayed silent. [measured: PSP-3000, large 3D game port] So a NaN made by
  VFPU code is harmless until you `mfv` it into C and compare it: sanitize with an exponent bit test before any FPU
  compare (helper in section 4). "Moved to the VFPU" does not mean "NaN-safe end to end". [practice]

## 2. Cost (measured cycles)

All numbers from vfpu-docs' performance test run on a PSP-3000 (`vfpu-docs/docs/reference-outputs/inst_cycles.txt`):
latency / cycles per instruction. [public: vfpu-docs, inst cycles]

| instruction | latency | cycles/inst |
|---|---|---|
| `vadd` / `vmul` / `vscl` .s-.q | 5 | 1 |
| `vdot` / `vhdp` .p-.q | 7 | 1.2 |
| special function .s (`vrcp vrsq vsqrt vsin vcos vexp2 vlog2 vasin`) | 7 | 1 |
| same, .p / .t / .q | 8 / 9 / 10 | 2 / 3 / 4 |
| `vrot.p` / `vrot.q` | 7 / 8 | 2 |
| `vdiv.s` (.q) | 17 (60) | 14 (56) |
| `vmmul.p` / `.t` / `.q` | 10 / 15 / 22 | 4 / 9 / 16 |
| `vtfm4.q` / `vhtfm4.q` | 10 | 4 |
| `vcrsp.t` / `vqmul.q` | 9 / 10 | 3 / 4 |
| `vmov` / `vcst` / `vsat0` / `vt5551.q` / `vi2uc.q` | 3 | 1 |
| `vcmp` (to a branch) | 8 | - |
| FPU `add.s` / `mul.s` | 5 | 1 |
| FPU `div.s` / `sqrt.s` | 31 / 30 | 30 / 29 |

- `vdiv` is slower than `vrcp` + `vmul` (8 + 5 cycles of latency, 2 issue slots). [derived]
- A 4x4 matrix product costs 16 issue cycles in `vmmul.q` against ~112 scalar FPU ops (64 mul + 48 add) plus loads:
  ~5x or more, if the operands already sit in VFPU registers. [derived]
- Memory (vfpu-docs, PSP-1000): `lv.q` streams ~15 bytes/cycle over an 8 KB buffer (in the D-cache) and ~2
  bytes/cycle from RAM, against ~0.9 bytes/cycle for `lw` from RAM. Random accesses out of cache cost ~70 cycles each.
  [public: vfpu-docs, mem_cycles.txt]
- The VFPU wins on throughput (batches of independent data), not on one dependent element: a single
  `mtv` -> `vrsq` -> `mfv` round trip pays the full latency plus two transfers. Keep loops in VFPU registers from load
  to store; a loop that moves every element through GPRs gains little. [practice]
- `vt5551.q` / `vt5650.q` convert four 8888 pixels per instruction at 1 cycle each - one of the best VFPU uses
  (bulk colour conversion); mcidclan's `vfpu-render-565-from-8888` sample shows the 565 case.
  [public: vfpu-docs, inst cycles; github.com/mcidclan/vfpu-render-565-from-8888]

## 3. When to use it, and when not

Use (one-shot results; 6-7 digits are plenty):
- normalising vectors and quaternions: `1/sqrt` -> `vrsq`;
- sin/cos for a rotation, a ring of vertices, a heading: `vrot` gives both;
- colour curves, fog and light falloff: `pow(b, e) = vexp2(e * vlog2(b))` (~1e-5 relative);
- bulk conversions and transforms that end in GE-bound data (GE matrices keep fewer bits than a float anyway, see
  [ge-arithmetic.md](ge-arithmetic.md)). [practice]

Do not use:
- anything accumulated frame after frame (camera yaw, timers, physics integration, animation time): the truncation
  bias walks; keep those on the FPU; [practice]
- anything compared bit-for-bit with a PC-side tool or converter (those run libm): compare with a tolerance of at least
  2^-22 relative; [derived]
- `vasin` close to +-1 (split the range, e.g. via `asin(x) = pi/2 - 2*asin(sqrt((1-x)/2))`), `vlog2` where more than
  ~15 bits matter; [derived]
- per-vertex skinning on the CPU: the GE skins in hardware ([rendering.md](rendering.md)). [practice]
- Check soft-float `double` before reaching for the VFPU: pspdev gcc uses a single-precision FPU and every `double`
  operation is a libgcc call. `psp-objdump -d app.elf | grep -c 'jal.*<__\(add\|sub\|mul\|div\)df3>'` found 5,633 such
  call sites in one PC game client port; that, not missing VFPU code, was its math cost.
  [measured: pspdev gcc build, PC game client port]

## 4. How: inline asm under pspdev gcc

- The open toolchain assembles VFPU mnemonics in inline asm: `mtv`/`mfv` (GPR <-> S register), `vcst.s`, `vmul.s`,
  `vsin.s`, `vrsq.s`, `vrot.p`, `vexp2.s`, `vlog2.s`, `vrcp.s`, `vt5551.q` etc. A float operand passes through a GPR
  (`"r"` constraint; gcc inserts the `mfc1`/`mtc1`). [measured: pspdev gcc 15, disassembly]
- gcc knows nothing about VFPU registers: never keep a value in a VFPU register across separate `__asm__` blocks; each
  block loads, computes and stores. [practice]
- Register ownership: pspsdk's `libpspgum_vfpu` (`pspsdk/src/gum/pspgum_vfpu.c`) uses matrices 0-3 on its calls, so
  helpers that may run between `sceGum*` calls should use matrices 6-7 (S600..S733). pspsdk's `pspvfpu.h`
  (`pspvfpu_use_matrices`) saves and restores matrix sets when several users in one thread need persistent registers.
  [public: pspsdk src/gum/pspgum_vfpu.c, src/vfpu/pspvfpu.h]
- Thread attribute: a thread must have `PSP_THREAD_ATTR_VFPU` ("Enable VFPU access for the thread"): main thread
  `PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU)`, other threads the flag in `sceKernelCreateThread`, or
  `sceKernelChangeCurrentThreadAttr(0, PSP_THREAD_ATTR_VFPU)` at run time (what `pspvfpu_initcontext` does).
  [public: pspsdk src/user/pspthreadman.h, src/vfpu/pspvfpu.c] Loader, audio and network threads that call a VFPU
  helper need it too; PPSSPP does not enforce it, the hardware does. [practice]
- The firmware enables FPU/VFPU lazily: a thread's first FPU (VFPU) instruction raises exception 11 "coprocessor
  unusable" with Cause.CE = 1 (2), which the kernel handles by enabling the unit. An exception handler of your own that
  runs first must pass CE = 1/2 on to the firmware and report only real faults, or every new thread "crashes" at its
  first float instruction. [measured: PSP-3000, PC game client port]
- Keep a run-time switch (config key) between the VFPU path and the FPU/libm path, plus a start-up self-test that logs
  the time and the maximum error of both. PPSSPP 1.20.4's JIT returned `vrsq` exactly equal to `1/sqrtf`
  [measured in PPSSPP 1.20.4]; PR #22343 (merged 2026-09, milestone v1.21) makes the bit-exact model the default.
  Timings are never emulated. [public: github.com/hrydgard/ppsspp/pull/22343]

Helper (ours, generic; matrix 7 only):

```c
#include <math.h>

static inline float vf_rsqrt(float x) {          /* 1/sqrt(x), ~22 bits */
    float r;
    __asm__ volatile(
        "mtv     %1, S700\n"
        "vrsq.s  S700, S700\n"
        "mfv     %0, S700\n"
        : "=r"(r) : "r"(x));
    return r;
}

/* sin and cos of an angle in radians; reduce first (Cody-Waite), then quarter turns */
static inline void vf_sincos(float rad, float *s, float *c) {
    float n = rintf(rad * 0.15915494309189535f);          /* 1/(2*pi) */
    rad = (rad - n * 6.28125f) - n * 1.9353071795864769e-3f;
    float rs, rc;
    __asm__ volatile(
        "mtv      %2, S700\n"
        "vcst.s   S701, VFPU_2_PI\n"
        "vmul.s   S700, S700, S701\n"
        "vrot.p   C710, S700, [s, c]\n"
        "mfv      %0, S710\n"
        "mfv      %1, S711\n"
        : "=r"(rs), "=r"(rc) : "r"(rad));
    *s = rs; *c = rc;
}

static inline float vf_pow(float b, float e) {   /* b > 0; ~1e-5 relative */
    float r;
    __asm__ volatile(
        "mtv      %1, S700\n"
        "mtv      %2, S701\n"
        "vlog2.s  S700, S700\n"
        "vmul.s   S700, S700, S701\n"
        "vexp2.s  S700, S700\n"
        "mfv      %0, S700\n"
        : "=r"(r) : "r"(b), "r"(e));
    return r;
}

/* exponent all ones = inf or NaN; test the bits, never compare on the FPU */
static inline int f_is_finite(float f) {
    union { float f; unsigned u; } v = { f };
    return (v.u & (0xFFu << 23)) != (0xFFu << 23);
}
```

Off the PSP (host tools, unit tests) compile the libm path behind the same functions.

## 5. Hazards and errata (vfpu-docs)

- Ordinary register dependencies interlock in hardware (a stall, correct result). Exception: `mfvc` right after a
  `vcmp` reads the old condition code; put one VFPU instruction (e.g. `vnop`) between them.
  [public: vfpu-docs, introduction "Register hazards"]
- `vmmul`, `vtfm2/3/4`, `vhtfm2/3/4`, `vqmul`, `vcrsp` allow no overlap between destination and sources (they write
  partial results into registers they still read). The .p/.t/.q forms of the special functions, `vdiv`, `vmscl`,
  `vmmov` allow overlap only when identical in size and direction (`vcos.q R000, R000` fine, `vcos.q R000, C000` wrong).
  [public: vfpu-docs, introduction "Register hazards"]
- `vhtfm2.p` / `vhtfm3.t` write the wrong elements when the destination register index is 64 or higher (the shifted
  column/row forms), on all models; `vhtfm4.q` is fine. [public: vfpu-docs, docs/bugs.md]
- PSP-1000 only: `lvl.q` / `lvr.q` (what `ulv.q` expands to) corrupt the FPU register numbered by the low 5 bits of the
  VFPU destination (`C000` -> `$f0`, `C010` -> `$f1`). In inline asm, list that FPU register as clobbered.
  [public: vfpu-docs, docs/bugs.md]
- `lv.q` / `sv.q` need 16-byte aligned addresses or they fault. [public: vfpu-docs, inst-vfpu-desc.yaml]
- PPSSPP models none of the overlap or errata cases: "identical on PPSSPP, wrong on the PSP" in VFPU code points at an
  overlap, an erratum, a missing thread attribute, or a value kept across `__asm__` blocks. [practice]

## 6. The method worth copying

The PPSSPP team recovered the interpolator by dumping an exact oracle for all inputs and then using interval
feasibility (do any coefficients reproduce every output exactly?) instead of least squares, which reached only ~93 %
exact outputs and hid the discrete structure. The same recipe fits any other PSP behaviour you can dump exhaustively
on the device (GE rounding, fixed-point paths): dump, find the discrete structure, do not fit.
[public: PPSSPP blog "VFPU math"]

## Sources

- vfpu-docs (pspdev): https://pspdev.github.io/vfpu-docs/ , repo https://github.com/pspdev/vfpu-docs
  (`inst-vfpu-desc.yaml`, `vfpu-docs/docs/introduction.md`, `vfpu-docs/docs/bugs.md`, `vfpu-docs/docs/reference-outputs/inst_cycles.txt` measured on
  a PSP-3000, `vfpu-docs/docs/reference-outputs/mem_cycles.txt` measured on a PSP-1000)
- PPSSPP blog "VFPU math": https://www.ppsspp.org/blog/vfpu-math-re/ ; PR https://github.com/hrydgard/ppsspp/pull/22343
- pspautotests (`cpu/vfpu`): https://github.com/hrydgard/pspautotests
- pspsdk: https://github.com/pspdev/pspsdk (`src/vfpu/pspvfpu.h`, `src/vfpu/pspvfpu.c`, `src/gum/pspgum_vfpu.c`,
  `src/user/pspthreadman.h`, `src/user/pspmoduleinfo.h`)
- mcidclan VFPU 8888 -> 565 sample: https://github.com/mcidclan/vfpu-render-565-from-8888
