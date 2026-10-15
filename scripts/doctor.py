#!/usr/bin/env python3
"""doctor.py - what this machine has for PSP homebrew development, what is missing, and where to get it.

    python scripts/doctor.py [--json] [--wsl] [--find-card]

Read-only: installs nothing, downloads nothing, writes nothing. Every tool is looked for in the environment variable
that names it, on PATH and in the standard install places of this operating system; nothing is assumed to be in a
particular folder. For each missing item it prints the official source (docs/DEPENDENCIES.md has the same table).
Always ask the user before downloading or installing anything: say what, from where, how big, and where it goes.

Environment variables it reads (all optional):
  PSPDEV          root of the pspdev toolchain (contains bin/psp-gcc)
  PPSSPP          full path of the PPSSPP executable
  PSP_MS_ROOT     the folder where the user's PSP memory stick is mounted on this PC (USB mode)
  ATRACDENC       full path of the atracdenc executable
  FFMPEG          full path of the ffmpeg executable

--wsl        on Windows: also look for pspdev inside the default WSL distribution (starts WSL if it is stopped)
--find-card  list mounted volumes that contain PSP/GAME (read-only); the user still has to confirm which one
"""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import string
import subprocess
import sys

WIN = os.name == "nt"
MAC = sys.platform == "darwin"

# Official sources (checked 2026-10-15; docs/DEPENDENCIES.md has versions, sizes and SHA-256 values).
DEPS = {
    "python": dict(what="Python 3.8 or newer (this doctor; asset scripts)",
                   url="https://www.python.org/downloads/",
                   win="the 64-bit installer from python.org (tick 'Add python.exe to PATH')",
                   unix="the system package (python3)"),
    "psp-toolchain": dict(what="pspdev toolchain: psp-gcc, PSPSDK, pack-pbp, mksfoex",
                          url="https://github.com/pspdev/pspdev/releases/latest",
                          win="inside WSL 2 (https://pspdev.github.io/installation/windows.html): unpack the "
                              "pspdev-ubuntu-latest-x86_64.tar.gz release into ~/pspdev, then "
                              "export PSPDEV=~/pspdev and PATH=$PSPDEV/bin:$PATH; run again with --wsl",
                          unix="unpack the release archive for this OS into ~/pspdev, then export PSPDEV=~/pspdev "
                               "and PATH=$PSPDEV/bin:$PATH (https://pspdev.github.io/installation.html)"),
    "make": dict(what="GNU make (pspsdk build.mak)",
                 url="https://pspdev.github.io/installation.html",
                 win="inside WSL: sudo apt install build-essential",
                 unix="the system package (make / build-essential; macOS: xcode-select --install)"),
    "pspdev-in-wsl": dict(what="a modern pspdev toolchain inside WSL (optional when a native one works)",
                          url="https://pspdev.github.io/installation/windows.html",
                          win="unpack the pspdev-ubuntu-latest-x86_64.tar.gz release into ~/pspdev inside WSL",
                          unix="not needed"),
    "wsl": dict(what="WSL 2 with a Linux distribution (the pspdev toolchain on Windows)",
                url="https://learn.microsoft.com/windows/wsl/install",
                win="wsl --install in an elevated PowerShell, then restart Windows",
                unix="not needed"),
    "git": dict(what="git", url="https://github.com/git-for-windows/git/releases/latest",
                win="Git for Windows (64-bit installer)", unix="the system package (git; macOS: xcode-select --install)"),
    "ppsspp": dict(what="PPSSPP emulator (fast yes/no checks; never timing or memory numbers)",
                   url="https://www.ppsspp.org/download/",
                   win="the Windows zip or installer from ppsspp.org or https://github.com/hrydgard/ppsspp/releases; "
                       "set PPSSPP=<path of the exe> if it is not found",
                   unix="AppImage from https://github.com/hrydgard/ppsspp/releases, Flathub org.ppsspp.PPSSPP, or "
                        "the macOS build from ppsspp.org; set PPSSPP=<path> if it is not found"),
    "psplink": dict(what="PSPLink host tools usbhostfs_pc + pspsh (run and debug over USB without copying)",
                    url="https://github.com/pspdev/psplinkusb",
                    win="pspsh-windows.zip from its releases page (a libusb driver is needed, see its README)",
                    unix="built with the pspdev toolchain (look in $PSPDEV/bin) or from its source"),
    "atracdenc": dict(what="atracdenc (ATRAC3 / ATRAC3plus encoder for SND0.AT3 and game music)",
                      url="https://github.com/dcherednik/atracdenc",
                      win="atracdenc-win-x86 zip from its releases page; set ATRACDENC=<path of the exe>",
                      unix="build from its source release (CMake); set ATRACDENC=<path> if not on PATH"),
    "ffmpeg": dict(what="ffmpeg (audio/video conversion: ICON1 animated icon, movies, WAV for the encoder)",
                   url="https://ffmpeg.org/download.html",
                   win="a Windows build linked from ffmpeg.org; set FFMPEG=<path of the exe>",
                   unix="the system package (ffmpeg)"),
    "memory-stick": dict(what="where the PSP memory stick is mounted on this PC (USB mode)",
                         url="",
                         win="ask the user; then set PSP_MS_ROOT=<mount folder> for this session",
                         unix="ask the user; then set PSP_MS_ROOT=<mount folder> for this session"),
}


class Check:
    def __init__(self, id, need, state, found="", note=""):
        self.id, self.need, self.state, self.found, self.note = id, need, state, found, note

    def hint(self):
        d = DEPS.get(self.id)
        if not d or self.state == "ok":
            return ""
        s = "%s. Get it: %s" % (d["what"], d["win"] if WIN else d["unix"])
        if d["url"]:
            s += " (%s)" % d["url"]
        return s

    def as_dict(self):
        return dict(id=self.id, need=self.need, state=self.state, found=self.found, note=self.note, hint=self.hint())


def run(argv, timeout=20, inp=None):
    try:
        r = subprocess.run(argv, input=inp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def env_file(var):
    p = os.environ.get(var)
    return p if p and os.path.isfile(os.path.expanduser(p)) else None


def env_dirs(*pairs):
    """Standard Windows install folders built from environment variables (never fixed drive letters)."""
    out = []
    for var, sub in pairs:
        base = os.environ.get(var)
        if base:
            out.append(os.path.join(base, *sub.split("/")))
    return out


def which_any(names, extra_dirs=()):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for d in extra_dirs:
        for n in names:
            for p in glob.glob(os.path.join(d, n)):
                if os.path.isfile(p):
                    return p
    return None


def os_info():
    wsl = False
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/version", encoding="utf-8", errors="replace") as f:
                wsl = "microsoft" in f.read().lower()
        except OSError:
            pass
    kind = "windows" if WIN else "macos" if MAC else "wsl" if wsl else "linux" if sys.platform.startswith("linux") \
        else sys.platform
    return dict(kind=kind, system=platform.system(), release=platform.release(), machine=platform.machine(),
                python=platform.python_version())


def find_toolchain():
    exe = "psp-gcc.exe" if WIN else "psp-gcc"
    cands = [("PSPDEV", os.environ.get("PSPDEV")), ("~/pspdev", os.path.expanduser("~/pspdev"))]
    if not WIN:
        cands += [("/usr/local/pspdev", "/usr/local/pspdev"), ("/opt/pspdev", "/opt/pspdev")]
    for label, d in cands:
        if d and os.path.isfile(os.path.join(os.path.expanduser(d), "bin", exe)):
            return os.path.join(os.path.expanduser(d), "bin", exe), label
    p = shutil.which("psp-gcc")
    return (p, "PATH") if p else (None, "")


def wsl_probe():
    """(distribution names, probe output) of the default WSL distribution, or (None, '') without WSL."""
    if not shutil.which("wsl.exe"):
        return None, ""
    rc, out = run(["wsl.exe", "-l", "-q"], timeout=20)
    if rc != 0:
        return [], ""
    # wsl.exe -l prints UTF-16; run() decoded it as UTF-8, so drop the zero bytes
    names = [x.strip() for x in out.replace("\x00", "").splitlines() if x.strip()]
    script = ('for d in "$PSPDEV" "$HOME/pspdev" /usr/local/pspdev /opt/pspdev; do [ -x "$d/bin/psp-gcc" ] && '
              'echo "pspdev=$d" && "$d/bin/psp-gcc" -dumpversion | sed "s/^/pspgcc=/" && '
              '{ [ -x "$d/bin/pack-pbp" ] && echo packpbp=yes; [ -x "$d/bin/mksfoex" ] && echo mksfoex=yes; '
              '[ -x "$d/bin/usbhostfs_pc" ] && echo usbhostfs=yes; [ -x "$d/bin/pspsh" ] && echo pspsh=yes; } && '
              'break; done; command -v make >/dev/null && echo make=yes; true\n')
    rc, out = run(["wsl.exe", "-e", "bash", "-s"], timeout=90, inp=script.encode())
    return names, out if rc == 0 else ""


def card_candidates():
    """Mounted volumes that contain PSP/GAME. Read-only; the user confirms which one is the PSP."""
    roots = []
    if WIN:
        for letter in string.ascii_uppercase:
            roots.append(letter + ":" + os.sep)
    else:
        for base in ("/media", "/run/media", "/Volumes", "/mnt"):
            roots += glob.glob(os.path.join(base, "*")) + glob.glob(os.path.join(base, "*", "*"))
    out = []
    for r in roots:
        try:
            if os.path.isdir(os.path.join(r, "PSP", "GAME")):
                out.append(r)
        except OSError:
            pass
    return out


def checks(use_wsl=False, find_card=False):
    out, info = [], os_info()
    v = sys.version_info
    out.append(Check("python", "required", "ok" if v >= (3, 8) else "old", sys.executable,
                     "Python %d.%d.%d" % (v[0], v[1], v[2])))
    out.append(Check("git", "recommended", "ok" if shutil.which("git") else "missing", shutil.which("git") or ""))

    tc, how = find_toolchain()
    wsl_out = ""
    if WIN:
        names, wsl_ok = None, bool(shutil.which("wsl.exe"))
        if use_wsl:
            names, wsl_out = wsl_probe()
        out.append(Check("wsl", "recommended", "ok" if (names if use_wsl else wsl_ok) else "missing",
                         ", ".join(names) if names else ("wsl.exe" if wsl_ok else ""),
                         "" if use_wsl else "distributions not probed (add --wsl)"))
        m = re.search(r"pspdev=(\S+)", wsl_out)
        if not tc and m:
            tc, how = "WSL:" + m.group(1) + "/bin/psp-gcc", "WSL"
    note = how
    if tc and not tc.startswith("WSL:"):
        rc, ver = run([tc, "-dumpversion"])
        rc2, sdk = run([os.path.join(os.path.dirname(tc), "psp-config" + (".exe" if WIN else "")), "--pspsdk-path"])
        if rc2 != 0:
            # psp-config is a shell script on some systems: try it through PATH as well
            rc2, sdk = run(["psp-config", "--pspsdk-path"])
        note = "%s; psp-gcc %s; pspsdk %s" % (how, ver.strip() or "?", sdk.strip() if rc2 == 0 else "not resolved")
        if re.match(r"[1-8]\.", ver.strip()):
            note += "; LEGACY compiler: works, but read references/build-toolchain.md (modern pspdev recommended)"
        bindir = os.path.dirname(tc)
        tools = [t for t in ("pack-pbp", "mksfoex", "psp-fixup-imports", "psp-prxgen")
                 if not glob.glob(os.path.join(bindir, t + "*"))]
        if tools:
            note += "; missing next to psp-gcc: " + ", ".join(tools)
    elif tc:
        m = re.search(r"pspgcc=(\S+)", wsl_out)
        note = "WSL; psp-gcc %s; pack-pbp %s; mksfoex %s" % (m.group(1) if m else "?", "packpbp=yes" in wsl_out,
                                                           "mksfoex=yes" in wsl_out)
    out.append(Check("psp-toolchain", "required", "ok" if tc else "missing", tc or "", note))
    if WIN and use_wsl and tc and not tc.startswith("WSL:"):
        m, g = re.search(r"pspdev=(\S+)", wsl_out), re.search(r"pspgcc=(\S+)", wsl_out)
        out.append(Check("pspdev-in-wsl", "optional", "ok" if m else "missing", ("WSL:" + m.group(1)) if m else "",
                         ("psp-gcc %s" % g.group(1)) if g else "no pspdev inside the default WSL distribution"))

    if WIN and shutil.which("make"):
        out.append(Check("make", "required", "ok", shutil.which("make")))
    elif WIN:
        ok = "make=yes" in wsl_out
        out.append(Check("make", "required", "ok" if ok else ("unknown" if not use_wsl else "missing"),
                         "WSL" if ok else "", "" if use_wsl else "the build runs inside WSL: add --wsl to look"))
    else:
        mk = shutil.which("make")
        out.append(Check("make", "required", "ok" if mk else "missing", mk or ""))

    pp = env_file("PPSSPP")
    if not pp:
        dirs = env_dirs(("ProgramFiles", "PPSSPP"), ("ProgramFiles(x86)", "PPSSPP"),
                        ("LOCALAPPDATA", "Programs/PPSSPP")) if WIN else \
            [os.path.expanduser("~/Applications"), "/Applications", "/snap/bin"]
        pp = which_any(["PPSSPPWindows64.exe", "PPSSPPWindows.exe", "PPSSPPSDL", "PPSSPPQt", "ppsspp",
                        "PPSSPP*.AppImage", "PPSSPPSDL.app"], dirs)
        if not pp and not WIN and shutil.which("flatpak"):
            rc, _o = run(["flatpak", "info", "org.ppsspp.PPSSPP"])
            pp = "flatpak org.ppsspp.PPSSPP" if rc == 0 else None
    out.append(Check("ppsspp", "recommended", "ok" if pp else "missing", pp or ""))

    pdir = os.path.dirname(tc) if tc and not tc.startswith("WSL:") else None
    hostfs = which_any(["usbhostfs_pc", "usbhostfs_pc.exe"], [pdir] if pdir else [])
    if not hostfs and "usbhostfs=yes" in wsl_out:
        hostfs = "WSL"
    out.append(Check("psplink", "optional", "ok" if hostfs else "missing", hostfs or ""))

    at = env_file("ATRACDENC") or which_any(["atracdenc", "atracdenc.exe"])
    out.append(Check("atracdenc", "optional", "ok" if at else "missing", at or ""))
    ff = env_file("FFMPEG") or which_any(["ffmpeg", "ffmpeg.exe"])
    out.append(Check("ffmpeg", "optional", "ok" if ff else "missing", ff or ""))

    ms = os.environ.get("PSP_MS_ROOT")
    if ms:
        ok = os.path.isdir(os.path.join(os.path.expanduser(ms), "PSP", "GAME"))
        out.append(Check("memory-stick", "optional", "ok" if ok else "missing", ms,
                         "PSP/GAME found" if ok else "PSP/GAME not found under PSP_MS_ROOT (not mounted?)"))
    else:
        cands = card_candidates() if find_card else []
        out.append(Check("memory-stick", "optional", "unknown", "",
                         ("volumes with PSP/GAME: " + ", ".join(cands) + " - ask the user which one") if cands else
                         "not set: ask the user where the memory stick mounts (or run with --find-card)"))
    return info, out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="doctor.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--wsl", action="store_true")
    ap.add_argument("--find-card", action="store_true")
    a = ap.parse_args(argv)
    info, cs = checks(a.wsl, a.find_card)
    if a.json:
        print(json.dumps(dict(os=info, checks=[c.as_dict() for c in cs]), indent=2))
    else:
        print("machine: %s %s %s, Python %s" % (info["kind"], info["release"], info["machine"], info["python"]))
        for c in cs:
            tag = {"ok": "ok  ", "missing": "MISS", "old": "OLD ", "unknown": "??  "}.get(c.state, "!!  ")
            if c.state != "ok" and c.need == "optional":
                tag = "opt "
            print("[%s] %-14s %s%s" % (tag, c.id, c.found or "-", ("  (" + c.note + ")") if c.note else ""))
            if c.hint():
                print("        " + c.hint())
    bad = [c for c in cs if c.need == "required" and c.state not in ("ok", "unknown")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
