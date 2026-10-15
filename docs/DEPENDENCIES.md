# Dependencies: what PSP homebrew development needs and where to get it

Nothing here is assumed to be installed or to live in a particular folder. `python scripts/doctor.py` looks for each
item in the environment variable that names it, on PATH and in the standard install places of the operating system,
and prints what is missing with the source below. If a tool is installed somewhere the doctor does not look, ask the
user for the path and set the environment variable for the session (`PSPDEV`, `PPSSPP`, `ATRACDENC`, `FFMPEG`,
`PSP_MS_ROOT`).

**Ask before every download or install**: say what, from where, how big, and where it goes. Prefer the system's
package manager on Linux and macOS. Never download from mirrors or re-uploads while the official source works.

Supported hosts: Linux (x86-64, arm64), macOS (Intel, Apple silicon), Windows 10/11 through WSL 2 (the pspdev project
publishes no native Windows build; older native Windows toolchains exist and work, see
`references/build-toolchain.md`).

## Sources (checked 2026-10-15)

Sizes and SHA-256 values are the ones the release pages publish. Newer versions are fine; compare a download with the
SHA-256 its release page shows at download time.

| Item | Need | Official source | Checked version, file, SHA-256 |
|---|---|---|---|
| pspdev toolchain (psp-gcc, PSPSDK, pack-pbp, mksfoex, psp-prxgen, PSPLink host tools) | required | https://github.com/pspdev/pspdev/releases/latest , guide https://pspdev.github.io/installation.html | v20261001: `pspdev-ubuntu-latest-x86_64.tar.gz` 172,980,272 bytes, sha256 a86efe624e770005290919617859e9df24bd8fcd3e364f4587f23ec7364b0acd; `pspdev-debian-latest.tar.gz` sha256 39b20efc7c8492cdbd9929178f45afc801dabb0967b7c882573520f22536920c; `pspdev-fedora-latest.tar.gz` sha256 7874d4d696f1abe58d3d17fd8ae365e9b129c4bea16b383bf708655037c0e21c; `pspdev-macos-latest-arm64.tar.gz` sha256 8efc2e9c41ad3c7e0b0eab2ca0ce2ae69dff329cee577ffd9e4a53edbe2e3acb; `pspdev-macos-15-intel-x86_64.tar.gz` sha256 bcf1133c438cd632671312b1399cf57b1e2f066665cdc9b0141255ae1cf8d19d; `pspdev-ubuntu-24.04-arm-arm64.tar.gz` sha256 0638f5f32fe4354c70040f8ee987b2e7111a981e93499d3986d30b15d1fb8e61. Unpack to `~/pspdev`, `export PSPDEV=~/pspdev`, `export PATH=$PSPDEV/bin:$PATH` |
| WSL 2 + a Linux distribution | Windows | https://learn.microsoft.com/windows/wsl/install , https://pspdev.github.io/installation/windows.html | `wsl --install` in an elevated PowerShell, restart, then install pspdev inside it |
| make and a host C compiler | required | https://pspdev.github.io/installation.html | Debian/Ubuntu/WSL: `sudo apt install build-essential`; macOS: `xcode-select --install` |
| Python 3.8 or newer | required (doctor, asset scripts) | https://www.python.org/downloads/ | any current release; Windows: the 64-bit installer, tick "Add python.exe to PATH" |
| git | recommended | Windows: https://github.com/git-for-windows/git/releases/latest ; Linux/macOS: the system package | any current release |
| PPSSPP | recommended (yes/no checks only) | https://www.ppsspp.org/download/ , https://github.com/hrydgard/ppsspp/releases | v1.20.4: `PPSSPP-v1.20.4-Windows-x64.zip` 20,390,334 bytes, sha256 fbc9cd2f5131b159a92424e5c458c35ce43ba603cdded64dfc98e4bd4f17ff93; `PPSSPP-v1.20.4-anylinux-x86_64.AppImage` sha256 661c098e6b7f7610171a57b7c533ce8bba6f2312b71e76d61e850461973eba21; `PPSSPPSDL-macOS-v1.20.4.zip` sha256 fb32a215861a293c28f641bf47f3164766ba512214025f4457796c8d14242665 |
| PSPLink (psplinkusb: PSP side + usbhostfs_pc, pspsh) | optional (run over USB without copying) | https://github.com/pspdev/psplinkusb | v3.2.1: `psplink.zip` 145,750 bytes, sha256 721be3c1dd4f82f9ac9b76183651af993de23cd8dfdcd0b85ece109697a92bec; `pspsh-windows.zip` sha256 78c10ca9184366e84cf46204a289b0d0b4a0e65b1322c60468121cef74e148e0. The host tools also come with the pspdev toolchain |
| atracdenc | optional (SND0.AT3, music) | https://github.com/dcherednik/atracdenc | 0.2.3 (LGPL-2.1): `atracdenc-win-x86_0.2.3.zip` 151,564 bytes, sha256 16801a1d8b09d9585e03f8e212805a5d8d262b72ea06fc57461cf560f378d3e3; source `atracdenc-0.2.3.tar.xz` sha256 c2668c2cefb20722341f4a03370f4c7e8fe196c6b0e0c5ef7ab13fc5ffe6ae1a |
| ffmpeg | optional (audio/video conversion) | https://ffmpeg.org/download.html | the system package, or a Windows build linked from ffmpeg.org |
| Public reference documents | optional | see "Sources" at the end of each `references/*.md` | read online; nothing is bundled |

Never needed and never downloaded by this skill: console-maker SDK material, PSP firmware files, keys, game images.
Custom firmware (needed on a real PSP to run homebrew) is installed by the user on their own console; this skill cites
the public source code of custom firmware only to document the calls a homebrew program may make into it.

## Order on a new machine

1. Python, git.
2. Linux/macOS: the pspdev release for the OS, plus make. Windows: WSL 2, then pspdev and build-essential inside it.
3. `python scripts/doctor.py` (Windows: `--wsl`) until every required line says ok.
4. Build the minimal sample from `references/build-toolchain.md`, run it in PPSSPP, then on the PSP.
5. Optional tools when a task needs them: PSPLink (fast deploy/debug loop), atracdenc and ffmpeg (menu sound, music,
   animated icon, movies).

## When a link is dead or a version is gone

1. Retry the exact URL once, then open the project's home page and follow its own download link.
2. A moved repository: accept a new home only if it is the same project (same owner, or an announcement from it).
   Release assets: take the newest that matches the system and its published SHA-256.
3. A vanished page or document: the Wayback Machine (`https://web.archive.org/web/2024*/<url>`), then the project's
   own announcements.
4. Useful searches: `"<project> releases"`, the exact file name from the table, `site:github.com <project>`. Prefer
   the project's own domain and code-host organisation over any third party.
5. Tell the user what you found (URL, version, size, SHA-256) and ask before downloading. If nothing official exists
   any more, say so; never take a binary from an unofficial host.
