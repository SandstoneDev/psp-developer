# Movies: PSMF files played through the firmware's sceMpeg (open pspsdk API)

The PSP's native movie container is PSMF (`*.pmf`): H.264 video (+ ATRAC3plus or PCM audio) in an MPEG-2 program
stream behind a 2 KB header. The firmware decodes the video on the Media Engine; you drive it with pspsdk's
`libpspmpeg` (`pspmpeg.h`) and write your own player loop. This file covers making PMFs with open tools, the API
traps, and the player design that played cleanly on a PSP-3000.

## 1. The file

Header, big endian, as PPSSPP's parser reads it [public: PPSSPP source, Core/HLE/scePsmf.cpp, sceMpeg.h]:
- `PSMF` + 4 version digits; +0x08 u32 stream offset (0x800); +0x0C u32 stream size; +0x54 / +0x5A 48-bit first /
  last timestamp (90 kHz); +0x80 u16 stream count; from +0x82 one 16-byte entry per stream:
  byte 0 stream id (0xE0.. AVC video, 0xBD private audio), byte 1 private id (0x0n ATRAC3plus, 0x1n PCM),
  AVC: +4 EP-map offset, +8 EP-map entries, +12 width/16, +13 height/16; audio: +14 channels, +15 rate code
  (2 = 44.1 kHz).
- The stream: 2048-byte MPEG-2 packs. Before each IDR picture the stream carries a pack with a system header and a
  private-stream-2 random-access record that the firmware's library uses; a plain ffmpeg MPEG-PS mux does not write
  it. [measured: muxer output accepted by a PSP-3000, see section 2]
- Video that played on hardware: H.264 Main profile, level 2.1, 480x272 (multiples of 16), 29.97 fps progressive,
  no B pictures, one reference frame, one slice. A 24 or 25 fps source becomes 29.97 by repeating frames (ffmpeg
  `fps=30000/1001`). ~1.5 Mbit/s video is ~11 MB per minute. [measured: PSP-3000, large 3D game port]

## 2. Making PMFs with open tools

One open route (verified on a PSP-3000): ffmpeg + libx264 encode the elementary stream, an open PSMF muxer wraps it.
- The PS2RC repository has a stdlib-only Python PSMF muxer and checker: `PS2RC/ps2rc/movies/pmfmux.py`,
  `PS2RC/ps2rc/movies/pmfinfo.py` (converter front-end `PS2RC/ps2rc/movies/convert_movies_open.py`).
  [public: github.com/SandstoneDev/PS2RC]
  It writes video-only PSMF, adds access-unit delimiters and the IDR recovery-point SEI where missing, and refuses
  what it cannot express (B slices / non-reference pictures, an access unit >= 64 KiB, a group of pictures longer
  than 490 pictures, more than 480x272, interlaced).
- x264 settings that the PSP-3000 decoder accepted (ours):
  `-c:v libx264 -profile:v main -level:v 2.1 -b:v 1500k` (2-pass) with
  `-x264-params ref=1:bframes=0:weightp=0:cabac=1:8x8dct=0:slices=1:keyint=59:min-keyint=12:aud=1:repeat-headers=1:nal-hrd=vbr:pic-struct=1:force-cfr=1:vbv-maxrate=3000:vbv-bufsize=1875:threads=1`
  plus `fps=30000/1001` and a scale/pad to 480x272. `threads=1` makes the output reproducible.
  Result on hardware: 462 pictures shown, 0 dropped; a 2,661-picture movie likewise. [measured: PSP-3000, large 3D
  game port]
- Sound without ATRAC: a separate `<name>.wav` next to the PMF (IMA ADPCM 44.1 kHz stereo, ~44 KB/s), decoded on the
  main CPU (idle during a movie) and fed into the same ring and clock as ATRAC would be. It played with 1 sound
  underrun over a 15 s movie. [measured: PSP-3000, large 3D game port]
- Check any PMF before shipping: ffmpeg must decode it frame-identical to the elementary stream (`-f framemd5` on
  both), and the frame count must equal the access-unit count. [practice]

## 3. API facts (pspsdk `pspmpeg.h`, behaviour checked in PPSSPP and on hardware)

- Modules: `sceUtilityLoadModule(PSP_MODULE_AV_AVCODEC)`, then `PSP_MODULE_AV_MPEGBASE`. 0x80111102
  (`SCE_ERROR_MODULE_ALREADY_LOADED`) means someone loaded it: do not unload what you did not load (AVCODEC also serves
  sceMp3). [public: pspsdk src/utility/psputility_modules.h]
- Link `-lpspmpeg -lpspaudio`. Adding `-lpsputility` by hand before libc split the sceUtility stubs ("stubs out of
  order"); the default link order already has it. [measured: pspdev toolchain build]
- **Structs need slack.** pspsdk's `SceMpegRingbuffer` is 11 words, but the library writes more: PPSSPP writes a 12th
  word (`gp`) for library version 0105 and later. Put each pspsdk struct (`SceMpegRingbuffer`, `SceMpegAu`,
  `SceMpegAvcMode`) at the front of a larger zeroed buffer, never directly next to other data.
  [public: PPSSPP source, Core/HLE/sceMpeg.h/.cpp (SceMpegRingBuffer, mpegLibVersion >= 0x0105); practice]
- Every sceMpeg call takes the handle through a pointer, including `sceMpegUnRegistStream`, which pspsdk declares by
  value: pass `&mpeg` (cast). [public: PPSSPP source, Core/HLE/sceMpeg.cpp getMpegCtx]
- `sceMpegAvcDecode(&mpeg, au, 512, &bufptr, &got)`: the 4th argument is the **address of a pointer** to the picture
  buffer (`void *bufs[1] = { dst };`, pass `bufs`); `got` comes back 1 when a picture was written (pspsdk names it
  `iInit`, misleading). Frame width 512 for a 480-wide picture. [public: PPSSPP source, sceMpegAvcDecode reads a
  pointer at bufferAddr; measured: PSP-3000]
- The decoder holds one picture: the first decode may write none, and a written picture belongs to an earlier access
  unit. Queue every decoded AU's PTS and give each written picture the oldest one; an AU without PTS (all-ones) gets
  previous + 3003 (one 29.97 fps frame in 90 kHz ticks). At the end `sceMpegAvcDecodeStop` hands out the held picture.
  [measured: PSP-3000 and PPSSPP, large 3D game port]
- `sceMpegAtracDecode(&mpeg, au, out, init)`: the first call with `init = 1` primes the decoder; drop that block. One
  call outputs 8192 bytes = 2048 stereo s16 frames at 44.1 kHz (`sceMpegQueryAtracEsSize`: ES 2112, output 8192).
  [public: PPSSPP source, MPEG_ATRAC_ES_SIZE / _OUTPUT_SIZE; measured: PSP-3000]
- `sceMpegGetAvcAu` / `sceMpegGetAtracAu` return 0x80618001 (`SCE_MPEG_ERROR_NO_DATA`) when no whole AU is in the
  ring: read more, or it is the end once everything was put and `sceMpegRingbufferAvailableSize` equals the packet
  count. [public: PPSSPP source, Core/HLE/ErrorCodes.h]
- Ring memory: `sceMpegRingbufferQueryMemSize(n)` = n x (2048 + 104). The put callback runs on the thread that calls
  `sceMpegRingbufferPut`; reading the card there is fine, but take the same card lock your streamers use.
  [public: PPSSPP source, __MpegRingbufferQueryMemSize]
- `SceInt32` is `long` under pspdev: cast before `%d`. [measured: pspdev gcc -Wformat]

## 4. Player design (rules our player followed; it played clean on a PSP-3000)

- **Drain every registered stream.** The ring frees space only behind the slowest registered stream: if you register
  ATRAC and then stop taking its AUs (sound off, decoder error), video starves. Take and drop them. PPSSPP does not
  show this (its video path reads the ring independently). [practice]
- **Size the ring for the muxer's video lead plus your audio lead**: video data for time T can sit up to ~1 s before T
  in the stream. 256 packs = 512 KB ~ 2.5 s at 1.6 Mbit/s, ~1.4 s at a 3 Mbit/s peak. Cap the decoded-audio lead
  (~0.5-0.75 s). [derived]
- **Audio-master clock with a fallback.** If the clock is "PCM frames the DAC took" and PCM runs dry, the picture that
  waits for the clock keeps the ring full, the next audio AU can never arrive: deadlock. While the PCM queue is under
  one block, advance the clock from the system timer. [practice]
- Show a picture when the clock reaches its PTS (half a frame early allowed); drop pictures more than 2 frames late,
  at most a few in a row. [practice; measured: 0 dropped on hardware]
- **Cache rule for decoder output**: the decoder writes behind the CPU's cache; a dirty line left over that memory
  (from its previous owner) can later be written back over the picture. Writeback-invalidate the output buffer
  (and the ATRAC output buffer, 64-byte aligned) before each decode, not a writeback after it.
  [public: PSP cache how-to (uofw upspd mirror); practice]
- **Picture format**: decoding RGBA8888 into RAM and drawing it with the GE worked on hardware [measured: PSP-3000,
  large 3D game port]. Decoding RGB565 straight into the VRAM draw buffer showed wrong colours on a PSP-3000, cause not
  isolated [measured: PSP-3000, PC game client port]. Keep the RAM + GE path, or at least a config switch to it.
- Memory: a RAM picture buffer of 512x272x4 = 544 KB is fine; four of them (2.2 MB) are not, in a big port. [derived]
- **A movie holds the main loop for minutes**: `scePowerTick(PSP_POWER_TICK_ALL)` once a second (no input for minutes
  = backlight off / auto sleep), keep checking the exit callback, and keep any network session alive (servers drop
  silent clients after ~60 s). [practice]
- **The ME**: sceMpeg decodes on the Media Engine. Play movies before any ME takeover; after it, sceMpeg is gone for
  the session ([media-engine.md](media-engine.md)). [public: PPSSPP source, sceMpeg.cpp "Media Engine"; measured:
  PSP-3000, large 3D game port]

## Sources

- pspsdk `src/mpeg/pspmpeg.h`, `src/utility/psputility_modules.h`, `src/power/psppower.h`:
  https://github.com/pspdev/pspsdk
- PPSSPP source `Core/HLE/scePsmf.cpp`, `Core/HLE/sceMpeg.cpp`, `Core/HLE/sceMpeg.h`, `Core/HLE/ErrorCodes.h`:
  https://github.com/hrydgard/ppsspp
- Open PSMF muxer: https://github.com/SandstoneDev/PS2RC (`PS2RC/ps2rc/movies/pmfmux.py`, `PS2RC/ps2rc/movies/pmfinfo.py`)
- ffmpeg (libx264): https://ffmpeg.org
- PSP cache how-to (uofw upspd mirror), J. Fitzhardinge 2005
