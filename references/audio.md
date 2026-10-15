# Audio on the PSP: channels, the SRC trap, mixer threads, streaming without hitches

Lessons from a software mixer plus streamed music running next to a region-streaming world on one Memory Stick,
and from moving that mixer to the Media Engine.

## 1. Hardware channels (pspsdk `pspaudio.h`, uofw `audio.c`)

- 8 regular output channels (`PSP_AUDIO_CHANNEL_MAX`), reserved with `sceAudioChReserve(ch or
  PSP_AUDIO_NEXT_CHANNEL, samples, PSP_AUDIO_FORMAT_STEREO / _MONO)`; the sample count is 64..65472 and a multiple of
  64 (`PSP_AUDIO_SAMPLE_ALIGN`); volume up to `PSP_AUDIO_VOLUME_MAX` (0x8000). [public: pspsdk src/audio/pspaudio.h]
- Regular channels play at the hardware output rate: 44.1 kHz by default; the kernel can switch it only between
  44100 and 48000. Feed regular channels 44.1 kHz. [public: uofw src/kd/audio/audio.c, sceAudioSetFrequency]
- `sceAudioOutputBlocking` / `sceAudioOutputPannedBlocking` return once the channel has taken the block; with two
  buffers in flight that is your audio clock (one block of error, ~23 ms at 1,024 frames). [public: pspsdk
  pspaudio.h; practice]
- The sample-rate-converting output is a single channel: `sceAudioSRCChReserve(samples, freq, 2)` has no channel
  argument, and `sceAudioOutput2*` is the same channel fixed at 44.1 kHz. It accepts only 8000, 11025, 12000, 16000,
  22050, 24000, 32000, 44100 and 48000 Hz; anything else returns `SCE_AUDIO_ERROR_INVALID_FREQUENCY` (0x8026000A).
  [public: uofw src/kd/audio/audio.c; pspsdk pspaudio.h (its comment misprints 11025 as 11050)]
  A sound bank recorded at 28 kHz therefore cannot use it: resample offline when you bake assets. [measured: PSP-3000,
  large 3D game port]
- **The SRC trap**: re-reserving the SRC channel right after releasing it failed on hardware (fine in PPSSPP). With
  several users (music, ambience, menu tune) handing it over by release + reserve, one failed handover killed sound for
  the rest of the session (a reserve failure at t = 183 s, 34 retries, all failed). [measured: PSP-3000, large 3D game
  port] If you use SRC, reserve it once and keep it. Better: give every stream a regular channel at 44.1 kHz and
  resample yourself (16.16 fixed-point linear interpolation is cheap). [practice]
- sceSas (the firmware's voice mixer) and sceAtrac decode run on the Media Engine; if you take the ME over they are
  gone for the session. [public: PPSSPP source, Core/HLE/sceSas.cpp] See [media-engine.md](media-engine.md).

## 2. Thread layout: decoder and feeder are different threads

Decoding (and reading the card) on the thread that feeds the DAC couples card stalls into audio, and its card reads
contend with asset streaming. Measured with music on: 14 frame spikes of 186-243 ms per session in random places (the
spike lands in whatever the main thread was doing - the signature of an external blocker); music off: 0.
[measured: PSP-3000, large 3D game port] The fix:

- **A low-priority decoder + reader thread** (below the main thread, e.g. 0x2E against main 0x20): reads the card in
  bursts, decodes and resamples ahead into a PCM ring of ~1 s (~160 KB), sleeps when the ring is full, so the card has
  long free windows for asset streaming.
- **A tiny high-priority feeder** (e.g. 0x12): only `sceAudioOutput*Blocking` from the ring. RAM only: a card stall
  can no longer stutter audio, and audio can no longer stall frames.
- Single-producer/single-consumer lock-free ring (decoder owns the write index, feeder the read index, `volatile` +
  a compiler barrier). Track change = bump a generation counter: the decoder reopens, the feeder snaps read = write.
- Result: worst frame 243 -> 93 ms, the spike class gone. [measured: PSP-3000, large 3D game port]
- Gapless track change: open the next stream a few seconds before the end, on the decoder thread (an open on a busy
  card can take seconds); route stdio buffers through one owner so two streams never get the same buffer. [practice]
- Card access rules (open cost, request floor, buffering): [file-io-memory-stick.md](file-io-memory-stick.md) and
  [streaming-architecture.md](streaming-architecture.md).

## 3. When the game runs slower than real time

If sound is produced in lockstep with game time (an emulated or recompiled sound chip, a deterministic mixer) and the
game runs below real time, the feeder runs dry. Choices, all on the feeder side:
- **time-stretch (WSOLA)**: grains of 1,024 frames, 50 % linear cross-fades, each grain placed where it best
  continues the previous one (a short similarity search on a decimated mono copy); same pitch, slower tempo; at real
  speed a grain is passed through bit for bit. Drive the stretch factor from the measured production rate plus a pull
  of the ring's low-watermark toward a target latency (~120 ms). Artefacts at ~2x: a slight echo on held notes;
- varispeed: pitch and tempo drop together;
- gaps: play what exists at 1x, silence for the rest (choppy). [practice]
The feeder's stretch costs a few ms per real second; it moved to the ME together with the mixer (section 5).

## 4. Robustness

- **Persistent worker threads.** Recreating an audio thread per track meant that one failed `sceKernelCreateThread`
  (a 256 KB stack under heap pressure) silently killed audio until restart. Create once per session, post
  {request, generation} to it, retry bring-up on a timer (~1.5 s heals busy-card starts), log every failure.
  [measured: PSP-3000, large 3D game port]
- **Voice stealing needs generation guards** in a software mixer: key-on bumps a per-voice generation before writing
  the fields; the mixer snapshots voice + generation, decodes from the snapshot, re-checks the generation every N
  samples and writes the cursor back only if it is unchanged. Worst case: one lost ~23 ms block of a dying sound
  instead of a corrupt restart. [practice]
- **Voice limit**: mix only the N loudest voices per block and advance the rest exactly (envelope, position, loop
  flags) without interpolation and summing. With 16 of up to 27 voices, 0.1 % of output frames differed by at most 4
  LSB in an offline comparison; 8 voices gave -40 dB rms difference. [derived: offline comparison, large 3D game port]
- **Buffers written by another core**: before handing an ME-written buffer to sceAudio, write back + invalidate your
  own cache lines over it, or old clean lines of a previous lap can be played. [practice] See
  [media-engine.md](media-engine.md).

## 5. Streaming music and its CPU cost

- Stream compressed music from the card instead of loading multi-MB tracks into the heap: stb_vorbis in FILE mode
  (do not define `STB_VORBIS_NO_STDIO`), the FILE wrapped with `setvbuf` 64 KB so small reads are batched.
  Validate converted OGGs carry exactly one Vorbis stream: a leftover cover-art video stream makes stb_vorbis reject
  the file (a silent station). [practice; measured: PSP-3000, large 3D game port]
- A Vorbis decode cost ~5-6 ms of main CPU per frame even with clean I/O. [measured: PSP-3000, large 3D game port]
- A software mixer for an emulated sound chip cost 7.5 ms per frame on the game thread plus ~2 ms of feeder
  time-stretch; moving mixer, resampler and stretcher to the ME saved 5.8 ms per frame. [measured: PSP-3000, large 3D
  game port]
- Open encoders for offline conversion: ffmpeg (resampling, Vorbis/PCM/ADPCM); an open ATRAC encoder exists
  (atracdenc, check which ATRAC variants its release supports) if you want `sceAtrac` playback or an XMB `SND0.AT3`
  ([packaging-eboot.md](packaging-eboot.md)). IMA ADPCM at 44.1 kHz (4:1) decodes on the main CPU for almost nothing
  and was our open sound path for movies ([movies-mpeg.md](movies-mpeg.md)). [practice]

## Sources

- pspsdk `src/audio/pspaudio.h`, `src/utility/psputility_modules.h`: https://github.com/pspdev/pspsdk
- uofw `src/kd/audio/audio.c` (sceAudioSetFrequency, SRC rates, sceAudioOutput2 = SRC at 44.1 kHz):
  https://github.com/uofw/uofw
- PPSSPP source `Core/HLE/sceSas.cpp`: https://github.com/hrydgard/ppsspp
- ffmpeg: https://ffmpeg.org ; atracdenc: https://github.com/dcherednik/atracdenc
