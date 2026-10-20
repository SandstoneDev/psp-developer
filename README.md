# psp-developer: an agent skill for PSP homebrew

A skill for coding agents (Claude Code, or any agent that reads the Agent Skills format: `SKILL.md` + `references/`)
that writes and optimises PSP homebrew which runs well on **real hardware**: pspdev/PSPSDK builds, EBOOT packaging,
sceGu/sceGum rendering, textures and the GE, memory and the 64 MB mode, streaming, the VFPU, the Media Engine, audio,
power and exit, overclocking through custom firmware, memory-stick I/O, profiling and hardware A/B testing.

It carries lessons paid for on real consoles (mostly a PSP-3000): the hazards that freeze a PSP while the emulator runs
fine, the numbers the emulator cannot give you, and a method for proving a change on a device you can barely observe.

## Install

Clone this repository into a skills folder under the name `psp-developer`:

```bash
# for you, in every project
git clone https://github.com/SandstoneDev/psp-developer.git ~/.claude/skills/psp-developer
# or for one project only (commit it with the project if you like)
git clone https://github.com/SandstoneDev/psp-developer.git .claude/skills/psp-developer
```

On Windows the personal folder is `%USERPROFILE%\.claude\skills\psp-developer`. Update with `git pull` in that folder.
Claude Code picks the skill up when a task matches its description (PSP homebrew, EBOOT, sceGu, "port X to PSP",
PSP performance, ...); you can also ask for it by name. Other agents: point them at `SKILL.md`.

## First run

Ask the agent to check your PSP development setup. It runs the read-only environment check

```bash
python scripts/doctor.py          # Windows: python scripts/doctor.py --wsl
```

which finds the pspdev toolchain, make, PPSSPP, PSPLink, atracdenc and ffmpeg through environment variables, PATH and
the standard install places of your OS, and prints the official download source for anything missing
([docs/DEPENDENCIES.md](docs/DEPENDENCIES.md)). Nothing is downloaded or installed without your OK. Then the agent asks
a few questions it cannot discover: which PSP model(s) you test on, which firmware / custom firmware they run, where
the memory stick mounts on your PC (or whether you use PSPLink), and what you are building.

## Layout

| Path | What |
|---|---|
| `SKILL.md` | entry point: the golden rule (the emulator answers yes/no, the device gives numbers), first run, workflow, the traps that cost the most, the index of references |
| `references/*.md` | one topic per file: toolchain, packaging, memory map, rendering, textures, GE arithmetic, VFPU, Media Engine, audio, movies, real-hardware gotchas, power/exit/sleep, overclocking, file I/O, streaming, profiling, instrumentation and A/B testing, debugging, porting |
| `scripts/doctor.py` | read-only environment check (Windows, Linux, macOS, WSL) |
| `docs/DEPENDENCIES.md` | every tool with its official source, checked version and SHA-256 |
| `tests/test_portability.py` | keeps the tree free of machine-specific paths and private names |

## How facts are sourced

Every non-obvious fact carries a tag: `[public: <source>]` for public documentation and source code (pspsdk, PPSSPP's
hardware articles and source, vfpu-docs, uOFW, PSPTEK, custom-firmware source for its API), `[measured: <model>,
<context>]` for our own measurements on real consoles, `[derived]` for arithmetic from tagged facts and `[practice]`
for engineering advice. Facts that could not be attributed to a public source or to a measurement were left out. No
console-maker SDK material is used or included.

## Contributing

New lessons are welcome: put them in the matching `references/` file with the symptom, the cause, the fix and the
source tag, and run `python tests/test_portability.py` before sending a pull request.

## Community and credits

Thanks to:

- **DenielX** ([github.com/DenielX](https://github.com/DenielX/), YouTube
  [@sp-pteam-indev6976](https://www.youtube.com/@sp-pteam-indev6976)) - for advice during development.
- **mcidclan** ([github.com/mcidclan](https://github.com/mcidclan)) - PSP Media Engine research: his Media Engine samples
  (start-up, memory map, spinlock, soft interrupts, DMA) and clock research are sources of the Media Engine and
  overclocking references.
- the public sources tagged in `references/` ([NOTICE.md](NOTICE.md)): [pspdev](https://github.com/pspdev),
  [PPSSPP](https://www.ppsspp.org/), [uOFW](https://github.com/uofw/uofw), [vfpu-docs](https://github.com/pspdev/vfpu-docs),
  [PSPTEK](http://daifukkat.su/docs/psptek/), Yet Another PSP Documentation and the
  [ARK custom firmware](https://github.com/PSP-Arkfive/ARK-5) source.

Written by SandstoneDev. Questions, results from other PSP models and new lessons: https://discord.gg/wV8x46ZTDu

Related: PS2RC, a PS2-to-PSP static recompiler (https://github.com/SandstoneDev/PS2RC), and universal-ps2-recompiler,
an agent kit for PS2-to-PSP ports (coming).

## Licence

Free under the PS2RC License 1.2 (see [LICENSE](LICENSE)): use it at no cost; forks must credit SandstoneDev, this repository and the community at https://discord.gg/wV8x46ZTDu in their README. In this repository, "PS2RC" in the licence text means the contents of this repository.

## Not affiliated

"PlayStation", "PSP" and related names are trademarks of Sony Interactive Entertainment Inc. This project is not
affiliated with, authorized or endorsed by Sony. It contains no Sony software, SDK material or firmware, and it does
not help to run commercial games without owning them.
