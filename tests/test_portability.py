"""The skill must work on anyone's machine and name nothing private: no absolute paths, drive letters, home folders,
user or machine names, tool locations or names of the authors' private projects anywhere in the tree. Text is
ASCII with LF line endings.

Run:  python -m pytest tests          or, without pytest:  python tests/test_portability.py

Optional: PSPDEV_SKILL_AUDIT=<path of a release audit script> also runs that audit over the tree and expects
0 blocking hits.
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
THIS = "tests/test_portability.py"

# Windows drive paths, home folders, WSL mounts of a drive, UNC shares, internal report names, agent letters.
PATH_RULES = [
    ("drive-path", re.compile(r"(?<![A-Za-z0-9_%])[A-Za-z]:[\\/][A-Za-z0-9_]")),
    ("home-path", re.compile(r"(?i)(?:/home/|/Users/|\\Users\\)[A-Za-z0-9_.-]+")),
    ("wsl-drive", re.compile(r"/mnt/[a-z]/[A-Za-z0-9_]")),
    ("unc-path", re.compile(r"\\\\[A-Za-z0-9_-]+\\[A-Za-z0-9_$]")),
    ("private-report", re.compile(r"\b[A-Z]{1,2}_[a-z0-9]+(?:_[a-z0-9]+)*\.md\b")),
    ("agent-letter", re.compile(r"\bagents? [A-Z]{1,2}\b(?![a-z])")),
    ("build-number", re.compile(r"(?i)\b(?:build|b)\s?#?\d{2,4}\b(?=[\s,.;:)])")),
]
# (rule, exact match) -> why it is allowed.
ALLOWED_MATCHES = {}

# SHA-256 prefixes of lower-case words that must never appear: the authors' user, machine, folder and private
# project names (stored hashed so that this public test does not publish them).
DENY_HASHES = {
    "dde9a01be8091c8e025a", "df87b787d0028b62b01f", "a2c8c93103170a4a5b1f", "2a33352a9d84e2c810db",
    "2b3d4ca3d1124eceded8", "88ce9314be43cb6b60ab", "0662e53056d7278ba5f5", "03683e1ca883ade31150",
    "9768b26ba116f3bc300d", "5ed4d837732027a7949e", "ea3cb6b2c97f40924045", "084e552fd07969113c90",
    "5a7eb62500a9c9f22720", "7bd099cbdb191eb793e1", "5fe0ea16c6aecfb1cc7c", "7f4b1f3a8ed7bfd628f5",
    "d2f910373e4c092ba30b", "50b13ed59f6eb5c69763", "e445c06dcb89d4d45edc", "7f2bbb56aa6f3db50fc4",
    "27b6791cd50f3fd31eb3", "952381d05e2197387177", "f552b8ba37fbd2c6a120", "7230e8928ffd5e34e389",
    "fbb4d845f0c1f9e241aa",
}
WORD = re.compile(r"[A-Za-z0-9_-]+")


def tree_files():
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = os.path.join(dp, fn)
            yield os.path.relpath(p, ROOT).replace(os.sep, "/"), p


def text_of(p):
    with open(p, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def hashed(word):
    return hashlib.sha256(word.encode("utf-8")).hexdigest()[:20]


def test_rules_fire_on_samples():
    # built from pieces, so that a release audit does not read them as real paths or names
    samples = {"drive-path": "see D" + ":\\games\\x", "home-path": "/home" + "/someone/x", "wsl-drive": "/mnt" + "/c/Tools",
               "unc-path": "\\\\server\\share", "private-report": "read XY_some_report.md",
               "agent-letter": "agent " + "AB did it", "build-number": "fixed in b" + "945."}
    for name, rx in PATH_RULES:
        assert rx.search(samples[name]), name
    assert not PATH_RULES[0][1].search("%SystemDrive%\\x and " + "https://" + "example.org/a:b")
    assert not PATH_RULES[0][1].search("ms0:/PSP/GAME/X")
    assert hashed("example") not in DENY_HASHES


def test_no_machine_specific_text():
    bad = []
    for rel, p in tree_files():
        if rel == THIS:
            continue
        text = text_of(p)
        if text is None:
            bad.append("%s: binary file" % rel)
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            for name, rx in PATH_RULES:
                for m in rx.finditer(line):
                    if (name, m.group(0)) not in ALLOWED_MATCHES:
                        bad.append("%s:%d %s %r" % (rel, ln, name, m.group(0)))
            for w in WORD.findall(line):
                lw = w.lower()
                for part in {lw} | set(re.split(r"[-_]", lw)):
                    if part and hashed(part) in DENY_HASHES:
                        bad.append("%s:%d private name (a word whose hash is denied)" % (rel, ln))
    assert not bad, "\n".join(bad[:60])


def test_text_is_ascii_and_lf():
    bad = []
    for rel, p in tree_files():
        text = text_of(p)
        if text is None:
            continue
        if not text.isascii():
            bad.append("%s: non-ASCII" % rel)
        if "\r\n" in text:
            bad.append("%s: CRLF line endings" % rel)
    assert not bad, "\n".join(bad)


def test_markdown_links_resolve():
    link = re.compile(r"\]\(([^)\s]+)\)")
    bad = []
    for rel, p in tree_files():
        if not rel.endswith(".md"):
            continue
        for ln, line in enumerate((text_of(p) or "").splitlines(), 1):
            for m in link.finditer(line):
                t = m.group(1).split("#")[0]
                if not t or re.match(r"(?i)^(?:https?:|mailto:)", t):
                    continue
                if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(p), t))):
                    bad.append("%s:%d broken link %s" % (rel, ln, t))
    assert not bad, "\n".join(bad)


def test_release_audit_clean():
    audit = os.environ.get("PSPDEV_SKILL_AUDIT")
    if not audit:
        return
    r = subprocess.run([sys.executable, audit, ROOT, "--all-files"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0 and "TOTAL BLOCKING HITS: 0" in out, out[-4000:]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   " + name)
            except AssertionError as e:
                failed += 1
                print("FAIL " + name + "\n" + str(e))
    sys.exit(1 if failed else 0)
