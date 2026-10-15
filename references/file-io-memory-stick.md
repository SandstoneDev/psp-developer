# File I/O on the Memory Stick

What a read, an open and a log line really cost on the card, the limits PPSSPP hides, and how to copy builds to the
card without corrupting it. Numbers are from PSP-3000 consoles; there are no public timing figures for the card, so
every number here is ours.

Related: [streaming-architecture.md](streaming-architecture.md), [real-hw-gotchas.md](real-hw-gotchas.md),
[instrumentation.md](instrumentation.md), [power-exit-sleep.md](power-exit-sleep.md).

## 1. Cost hierarchy: opens >> requests >> bytes

| operation | cost on the device |
|---|---|
| `fopen` | ~34 ms [measured: PSP-3000, large 3D game port, folder of ~1700 entries] |
| `sceIoOpen` | ~17 ms (17-34 ms in another program) [measured: PSP-3000, large 3D game port; recompiled game] |
| an open that fails (file missing) | the same as a successful one [measured: PSP-3000, large 3D game port] |
| one read request | floor ~0.6-1.2 ms (32 KiB cluster; a sub-cluster read still touches the FAT); a storm measured 13.8 ms [measured: PSP-3000, large 3D game port] |
| sequential bytes | 18.6 MiB/s; ~15.5 MB/s in another program [measured: PSP-3000, large 3D game port; recompiled game] |
| random 5 KB blob | ~1.23 ms [measured: PSP-3000, large 3D game port] |

Worked example: a tile loader opened up to 14 files per tile (one side file per feature, the main file twice):
14 x 34 ms = 476 ms of opening against 28 ms of bytes (540 KB) - 94 % of the load. About 5 of the 14 were misses;
one side-file type existed for 0 of 184 tiles and was searched for 184 times. Worker priority, compression and
fragmentation were ruled out by experiment first [measured: PSP-3000, large 3D game port].

Rules:
- **One archive per region** (table of contents + chunks in load order), not a file per feature. A missing chunk
  inside an archive costs nothing.
- Keep the archive open and use positioned reads on one shared descriptor; never add a second card request per
  streaming read.
- Report **ms per request**, not MB/s.
- Compression is about neutral on the seek path (lz4 saved 1.3 % of a tile's cost): worth it for geometry, pointless
  for already-dense swizzled texels [measured: PSP-3000, large 3D game port].
- The often repeated "Memory Stick seek latency 5-15 ms" came from a **UMD** read log, not the card; do not budget the
  card with it. UMD latency itself is not measured here (Unknown); measure it if you stream from disc.

## 2. stdio `fseek` re-reads the whole buffer

With a large `setvbuf` buffer, an `fseek` outside the current window makes the next `fread` refill the entire buffer.
A 256 KB buffer serving 4.8 KB blobs hauled 24 MB to deliver 458 KB [measured: PSP-3000 and PPSSPP, large 3D game port, by
byte count]:

```
access pattern              read amplification
file order                      1.5x
cluster pull, 48 blobs          2.9x
cluster pull, 6 blobs          23x     <- a "reduce latency" change
no clustering                  41x
```

The 23x row was a feedback loop shipped on purpose: the card fell behind -> surfaces drew untextured -> a heuristic
cut the cluster size to shorten one blob's latency -> throughput collapsed 8x -> more untextured surfaces. Watch both
latency and effective MB/s, and ms per blob against queue depth (14 -> 17 ms, 123 -> 27 ms, 149 -> 40 ms in that run).
For random blobs use `sceIoLseek` + `sceIoRead` (no stdio buffer) or a small buffer sized to the blob
[practice].

PPSSPP hides this: on the host file system the 24 wasted MB cost 241 ms; on the card 2.7 s, and the emulator's split
between seek and read time does not transfer (a host `sceIoOpen` costs it ~10 ms). Base conclusions on byte counts
[measured: PSP-3000 vs PPSSPP, large 3D game port]. Also check your instrument: a log line that timed seek + read +
decompress together and counted KB with `>> 10` (so every blob under 1 KB counted 0) produced "the card gives
0.5 MB/s" for years.

## 3. Open-file limit

The device allows only a handful of open files; PPSSPP allows many more.
- With a log and stdio already open, a probe got **9 more** `sceIoOpen`s, then `0x80020320`
  (`SCE_KERNEL_ERROR_MFILE`, [public: pspsdk src/user/pspkerror.h]); PPSSPP gave 60. `fopen` then fails with
  `errno 0` ("Success"): libcglue does not map that code [measured: PSP-3000, PC game client port].
- In another program the session log stopped at the same line for four builds while the game ran on: the log's own
  open failed once the data files were open [measured: PSP-3000, recompiled game].
- A kept-open handle per resident region plus two logs overran the table: every new open failed, the world stopped
  loading, then froze [measured: PSP-3000, streaming map engine].

Rules:
- Keep at most ~8 files open, in a small LRU cache; count open failures.
- On any open failure other than "not found", close the least recently used file and retry. Give the log the same
  recovery.
- Close files you are not actively reading (right after a header read). An async reader opens its own handle.
- Log a boot probe (`open_limit n= err=`) so a firmware or card difference shows up.

## 4. The file size is recorded on close

The card's FAT records a file's size when the file is closed. Data written and flushed into a file that stays open is
gone after a crash or power-off: a log flushed many times was 0 bytes after a crash [measured: PSP-3000, recompiled
game].
- Every log commit: open for append, write, close.
- During boot commit every line at once, so the last line names the step that died.
- In the main loop buffer lines and commit them from another thread.
- Crash-safe saves and state files: write a temporary file, close it, rename it into place.

## 5. The log as the bottleneck

- One in-frame log write took 107 ms; buffered logging moved log time on the game thread from 0.60 to 0.00 ms per
  frame [measured: PSP-3000, recompiled game].
- Over a 340 s run logging cost 42.2 s (worst single write 1.58 s) against 44.8 s for all of streaming: a "surface is
  untextured" line fired every frame while the world was behind, holding the card and keeping it behind
  [measured: PSP-3000, large 3D game port].

Rules: buffer the log; commit immediately only boot, milestones, crashes, exit and a small whitelist of panic
prefixes; keep a counter of what logging costs; rate-limit repeated lines; never dump images or per-frame debug to the
card on hardware; gate all runtime file writes behind a debug flag.

## 6. Asynchronous reads

Keep synchronous card reads off the game thread:
- a reader thread at a priority just above the game thread, or the async calls (`sceIoReadAsync`, `sceIoLseekAsync`,
  `sceIoWaitAsync` / `sceIoPollAsync`, `sceIoChangeAsyncPriority`) [public: pspsdk src/user/pspiofilemgr.h];
- measured with a reader thread for a program's disc-image reads: mean frame 68.0 -> 64.8 ms, 99.9th percentile
  183 -> 119 ms, read time on the game thread 3.16 -> 0.04 ms per frame [measured: PSP-3000, recompiled game];
- keep a 1 ms frame-time histogram and the top sources of the slowest frames; averages hide hitches.

## 7. Copying builds to the card over USB

The USB mass-storage link drops mid-write and the copy tool does not always notice: once a truncated EBOOT, once a
file whose hash differed on the final re-read after a per-file "OK" [measured: PSP-3000, recompiled game].
- **Archive the old folder's logs to the PC before deleting anything.** Then remove old test builds from the card.
- Copy file by file; hash each file on both sides; retry a few times; at the end hash the whole folder again.
- Never make large card-to-card copies. **Never touch folders on the card that are not yours** (other projects' builds,
  the custom firmware's folders, saves).
- Eject safely. After an interrupted copy, check and repair the card's file system on the PC before the next run.
  Interrupted copies once left 150 lost chains; the damaged FAT mounted slowly, and the custom firmware then read its
  settings from internal flash instead of the card - which silently switched the extended 64 MB memory mode off
  [measured: PSP-3000, recompiled game]. See [memory-map.md](memory-map.md).
- Each test folder gets a distinct, short XMB title with the build number, and stays thin (EBOOT, settings, helper
  module) pointing to one shared data folder.

## 8. Know which log you are reading

Logs rotate, thin test folders read data from one folder and write logs next to their own EBOOT, and A/B folders look
alike [measured: PSP-3000, recompiled game].
- Every build gets a new build number, shown in the XMB title, the log header and on screen.
- The log header carries build, model, firmware, clocks; each run writes a sequence number, the PSP clock time and the
  battery state.
- Archive logs with folder name and build number in the file name; check `build=` and the run line before analysing.
- Analysis scripts parse explicit `key=value` fields, never positions in a line (one script read 50 Hz logs as 60 Hz
  after a text change).

## Sources

- pspsdk (https://github.com/pspdev/pspsdk): `src/user/pspiofilemgr.h`, `src/user/pspkerror.h`
- PPSSPP (https://github.com/hrydgard/ppsspp)
