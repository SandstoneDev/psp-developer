# Packaging: EBOOT.PBP, PARAM.SFO and the menu files

What goes into an `EBOOT.PBP`, how `PARAM.SFO` is built, and what we know works for the menu icon, background and
music. Only facts with a public source or our own hardware test are given; where neither exists the text says
"untested". Building and deploying: [build-toolchain.md](build-toolchain.md). The `MEMSIZE` key and the 64 MB mode:
[memory-map.md](memory-map.md).

## 1. EBOOT.PBP layout

`pack-pbp` writes, and PPSSPP's reader reads `[public: pspsdk tools/pack-pbp.c; PPSSPP Core/ELF/PBPReader.cpp]`:

- A 40-byte header: the 4 bytes `00 50 42 50` ("\0PBP"), 4 version bytes `00 00 01 00`, then 8 little-endian u32
  offsets.
- Eight members in this fixed order, stored back to back with no padding: `PARAM.SFO`, `ICON0.PNG`, `ICON1.PMF`,
  `PIC0.PNG`, `PIC1.PNG`, `SND0.AT3`, `DATA.PSP` (the program: ELF or PRX), `DATA.PSAR`.
- The first offset is 40. A member's size is the next offset minus its own; the last one runs to the end of the file;
  an absent member has size 0 (its offset equals the next one). Offsets need no alignment: with an icon the program
  starts at an odd offset, as in every pspsdk-built homebrew with an ICON0.
- `build.mak` passes `NULL` for each member you do not set (`PSP_EBOOT_ICON`, `PSP_EBOOT_ICON1`, `PSP_EBOOT_PIC0`,
  `PSP_EBOOT_PIC1`, `PSP_EBOOT_SND0`, `PSP_EBOOT_PSAR`) `[public: pspsdk src/base/build.mak]`.
- PSPSDK also has `unpack-pbp` to take an EBOOT apart `[public: pspsdk tools/unpack-pbp.c]`.

To change only the menu files or the title, rebuild the PBP from its members (any PBP library or a small script
following the layout above); the program member stays byte-identical `[practice]`.

## 2. PARAM.SFO

Format `[public: pspsdk tools/mksfoex.c; PPSSPP Core/ELF/ParamSFO.cpp]`:

- Header: magic "\0PSF", version 1.1, offset of the key table, offset of the data table, entry count.
- 16-byte entries: key offset (u16), data format (u16), used length (u32), reserved length (u32), data offset (u32).
  Formats: UTF-8 string with terminating zero, u32 integer, raw bytes.
- Keys are zero-terminated ASCII; `mksfoex` reserves (length + 1) rounded up to 4 bytes for strings.
- `mksfoex` writes the `-d NAME=value` / `-s NAME=string` options first, then its defaults for any key not given,
  then `TITLE`. Defaults: `BOOTABLE` 1, `CATEGORY` "MG" (memory-stick game), `DISC_ID` (a fixed product-style id),
  `DISC_VERSION` "1.00", `PARENTAL_LEVEL` 1, `PSP_SYSTEM_VER` "1.00", `REGION` 32768.
- Key order does not matter to the firmware: `mksfoex` output (unsorted, `MEMSIZE` first) boots on hardware
  `[measured: PSP-3000, recompiled game]`.

Keys you set from the Makefile:

| Key | How | Notes |
|---|---|---|
| `TITLE` | `PSP_EBOOT_TITLE = My Game` | the name in the menu; UTF-8, one line, no control characters `[practice]` |
| `DISC_ID` | `SFOFLAGS += -s DISC_ID=ABCD12345` | set your own. PPSSPP names its per-game files after it (configs, cheats, texture packs, save states), so every program left at the default id shares one PPSSPP game config `[public: PPSSPP Core/ELF/ParamSFO.cpp]`. Use letters and digits only |
| `MEMSIZE` | `PSP_LARGE_MEMORY = 0 / 1 / 2`; current PSPSDK writes 2 by default | see [memory-map.md](memory-map.md) `[public: pspsdk src/base/build.mak]` |
| `CATEGORY` | default "MG" | PPSSPP treats "MG" as homebrew for its memory mapping `[public: PPSSPP Core/System.cpp]` |
| `PBOOT_TITLE` | `mksfoex -p` | only for a PBOOT (update) SFO `[public: pspsdk tools/mksfoex.c]` |

After changing any of these, `make clean`: the SFO rule has no prerequisites ([build-toolchain.md](build-toolchain.md)).

## 3. The menu files

Sizes below are the ones we generate and saw displayed `[measured: PSP-3000 with custom firmware on 6.61, homebrew
test program]` unless another tag is given. What the menu does with other sizes, bit depths or formats: untested.

| Member | What the menu does | What we use |
|---|---|---|
| `ICON0.PNG` | still icon in the game list | 144 x 80 PNG, RGB (no alpha) |
| `ICON1.PMF` | animated icon | 144 x 80 PSMF, H.264 (section 5); menu playback not confirmed in our runs |
| `PIC0.PNG` | picture drawn over the background | 310 x 180 PNG, RGBA (transparency over PIC1 works) |
| `PIC1.PNG` | full-screen background | 480 x 272 PNG, RGB; 480 x 272 is the screen size `[public: PSPTEK, technical data]` |
| `SND0.AT3` | music while the entry is selected | ATRAC3plus or ATRAC3 in a RIFF container (section 4) |

- Strip text, profile and EXIF chunks from the PNGs and optimise them `[practice]`.
- A user's custom firmware can hide PIC0 / PIC1 on purpose (ARK-5's `hidepics` setting). If the pictures do not show
  on one console only, check that before the files `[public: ARK-5 source, SystemControl settings.c]`.
- Keep ICON1 + SND0 together under ~500 KB; our tested pairs were at most ~420 KB. A combined limit around 500 KB is
  widely cited for these two files; we did not find a public source we could check, and we did not test larger pairs.
- Generate neutral art from code or the user's own pictures; never ship other people's artwork or trademarks in a
  public build `[practice]`.

## 4. SND0.AT3: what played on hardware

Three SND0 files, generated music with a loop, each played as menu music on a PSP-3000 with custom firmware on 6.61
`[measured: PSP-3000 with custom firmware on 6.61, homebrew test program]`:

| Codec | Bit rate | Length | File size |
|---|---|---|---|
| ATRAC3plus | 352 kbit/s | 9.6 s | 420,004 bytes |
| ATRAC3 (LP2) | 132 kbit/s | 24 s | 392,208 bytes |
| ATRAC3 (LP4, joint stereo) | 66 kbit/s | 48 s | 394,704 bytes |

- Within a ~500,000-byte budget that is about 11 s, 30 s and 60 s `[derived]`.
- ATRAC3 (not plus) as SND0 is outside the commonly documented format. It worked on our PSP-3000; other models and
  firmware: untested. Offer ATRAC3plus where quality matters and ATRAC3 when length matters.
- All 44.1 kHz stereo.

**Encoding with atracdenc** (https://github.com/dcherednik/atracdenc, LGPL-2.1), facts for version 0.2.3
`[measured: PC, atracdenc 0.2.3, checked by decoding with ffmpeg and by PPSSPP]`:

- `-e atrac3` gives ATRAC3 LP2 (132 kbit/s, 384-byte frames); `-e atrac3_lp4` gives LP4 (66 kbit/s, 192-byte frames,
  joint stereo). Both come out as RIFF WAVE with a 32-byte `fmt ` chunk and no `fact` chunk.
- ATRAC3plus comes out only in an OMA container in 0.2.3 (a container option appears in later sources). Re-wrap it
  into RIFF WAVE yourself: `fmt ` of 52 bytes = WAVE_FORMAT_EXTENSIBLE (tag 0xFFFE) with cbSize 34 and the ATRAC3plus
  sub-format GUID; ATRAC3 uses format tag 0x0270 `[public: ffmpeg libavformat/riff.c]`. PPSSPP refused the 40-byte
  `fmt ` that later atracdenc versions write for ATRAC3plus.
- Add a `fact` chunk (sample count and encoder delay) and an `smpl` chunk with one loop for a seamless repeat. The
  encoder delay, measured by aligning ffmpeg's decode against the input: 4096 samples for ATRAC3plus, 1247 for both
  ATRAC3 modes. Loop start = delay, loop end = delay + samples - 1. Pad the input so the tail gets coded, and cut the
  music to whole bars so the loop is musical.
- `data` holds whole codec frames only.
- On Windows, atracdenc 0.2.3 aborted on any non-ASCII character in an input or output path; run it inside an ASCII
  work folder with relative names.
- `pack-pbp` does not validate SND0; a wrong file does not fail the build, it just stays silent `[public: pspsdk
  tools/pack-pbp.c]`. Decode it back with ffmpeg (`ffmpeg -i SND0.AT3 out.wav`) as a check.

## 5. ICON1.PMF: the animated icon

What we produce `[measured: PC, ffmpeg + libx264]`:

- Video only, 144 x 80 (a whole number of 16 x 16 macroblocks), H.264 Main profile level 2.1, 29.97 fps progressive,
  IDR and P pictures only (no B), an IDR every 30 frames, VBV at most 3 Mbit/s, a few seconds long, bit rate chosen
  so the file fits what SND0 leaves of the budget. An ffmpeg command of this shape:
  ```sh
  ffmpeg -i in.gif -vf "scale=144:80,fps=30000/1001" -c:v libx264 -profile:v main -level 2.1 \
         -bf 0 -g 30 -maxrate 1M -bufsize 1M -pix_fmt yuv420p -f h264 icon1.264
  ```
- H.264 encoding needs an ffmpeg built with libx264 (GPL); an LGPL-only ffmpeg build had no H.264 encoder that makes
  Main profile `[measured: PC, ffmpeg builds]`.
- ffmpeg does not write PSMF; wrap the elementary stream with a PSMF muxer. The header PPSSPP parses: "PSMF" + a
  4-digit version, a big-endian stream offset at byte 8 (the stream starts at 2048), a stream count at 0x80 and
  16-byte stream entries from 0x82 `[public: PPSSPP Core/HLE/scePsmf.cpp]`.
- Write an entry-point map (one entry per IDR). PPSSPP's `scePsmfCheckEPMap` reports an empty map as not found
  `[public: PPSSPP Core/HLE/scePsmf.cpp]`, so a player that seeks or loops through the map fails without one. Whether
  the PSP menu needs it: untested; we write version "0012" with a map.
- Our ICON1 files were 34-250 KB for 0.7-4 s clips `[measured: PC, our encoder]`. Menu playback of our ICON1 on a PSP:
  not confirmed in our runs (the pictures and music were). Test ICON1 on the device before relying on it, and keep
  ICON0 as the fallback.

## 6. Checklist before shipping an EBOOT

- `make clean && make`; no "stubs out of order" line in the build log ([build-toolchain.md](build-toolchain.md)).
- Unpack the PBP and check: SFO keys (`TITLE`, your `DISC_ID`, the intended `MEMSIZE`), member sizes, ICON1 + SND0
  under the budget, SND0 decodes, PNG sizes as in section 3.
- Boot it from the menu on a PSP (not only through PSPLink or PPSSPP), look at the icon, background and music, and
  start it from the menu.

## Sources

- PSPSDK: https://github.com/pspdev/pspsdk (`pspsdk/tools/pack-pbp.c`, `pspsdk/tools/unpack-pbp.c`,
  `pspsdk/tools/mksfoex.c`, `src/base/build.mak`)
- PPSSPP: https://github.com/hrydgard/ppsspp (`Core/ELF/PBPReader.cpp`, `Core/ELF/PBPReader.h`,
  `Core/ELF/ParamSFO.cpp`, `Core/HLE/scePsmf.cpp`, `Core/System.cpp`)
- ARK-5 custom firmware source: https://github.com/PSP-Arkfive/ARK-5 (`SystemControl/src/settings.c`)
- PSPTEK hardware notes: http://daifukkat.su/docs/psptek/
- atracdenc: https://github.com/dcherednik/atracdenc
- ffmpeg: https://ffmpeg.org (`libavformat/riff.c` for the RIFF codec tags)
