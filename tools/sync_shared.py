#!/usr/bin/env python3
"""Keep trailgeek's copies of landslidescience's shared browser files in step
with their source, and fail CI if a copy is edited here.

trailgeek reuses, byte for byte, a handful of landslidescience's static files
(basemaps, the URL-hash codec, the demshade bridge and the vendored demshade
build). tools/shared.json pins the landslidescience commit they came from and
the SHA-256 of each file's upstream content. Three commands:

    python tools/sync_shared.py check
        CI. Every copy must match its pinned hash (after its provenance
        header, for the .js files that carry one). Needs nothing but this
        repo: no network, no landslidescience checkout.

    python tools/sync_shared.py status  [--repo PATH | --github] [--ref REF]
        Which files differ between the pin and REF (default origin/main with
        --repo, main with --github), i.e. what a sync would bring in.

    python tools/sync_shared.py sync    [--repo PATH | --github] [--ref REF]
        Copy every file at REF, rewrite the provenance headers, update the
        pin. Review the diff, test, commit.

--repo reads a local landslidescience clone (fetch it first); --github reads
raw.githubusercontent.com (the repo is public). Only the Python standard
library, so it runs anywhere, including CI before `pip install`.

To stop sharing a file, remove its entry from shared.json; to change one,
change it in landslidescience and sync (docs/SISTER_PROJECTS.md).
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tools" / "shared.json"

# The provenance header sync writes on top of a .js copy. check() strips
# exactly this block (first comment, "Copied verbatim from landslidescience")
# before hashing, so the header may name the commit without breaking the hash.
HEADER_RE = re.compile(r"\A/\* Copied verbatim from landslidescience .*?\*/\n", re.S)
HEADER = (
    "/* Copied verbatim from landslidescience {path}\n"
    " * (hig314/landslidescience @ {short}, {date}). One copy of every shared\n"
    " * thing: do not edit here; change it there and run\n"
    " * `python tools/sync_shared.py sync` (tools/shared.json pins the commit). */\n"
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load():
    return json.loads(MANIFEST.read_text())


def body_of(local_bytes, header):
    if not header:
        return local_bytes
    text = local_bytes.decode("utf-8")
    m = HEADER_RE.match(text)
    if not m:
        raise ValueError("missing the 'Copied verbatim from landslidescience' header")
    return text[m.end():].encode("utf-8")


def check(man):
    bad = 0
    for f in man["files"]:
        path = ROOT / f["local"]
        try:
            got = sha256(body_of(path.read_bytes(), f.get("header", False)))
        except FileNotFoundError:
            print(f"MISSING  {f['local']}")
            bad += 1
            continue
        except ValueError as e:
            print(f"HEADER   {f['local']}: {e}")
            bad += 1
            continue
        if got != f["sha256"]:
            print(f"EDITED   {f['local']}  (differs from landslidescience {f['upstream']} "
                  f"@ {man['ref'][:7]})")
            bad += 1
        else:
            print(f"ok       {f['local']}")
    if bad:
        print(f"\n{bad} shared file(s) differ from the pinned landslidescience copy. Shared files "
              "are changed in landslidescience and synced here (tools/sync_shared.py sync), never "
              "edited in place. See docs/SISTER_PROJECTS.md.", file=sys.stderr)
    return 1 if bad else 0


class Upstream:
    def __init__(self, repo=None, github=False, ref=None, slug="hig314/landslidescience"):
        self.repo, self.github, self.slug = repo, github, slug
        if repo:
            self.ref = ref or "origin/main"
            self.sha = self._git("rev-parse", self.ref).decode().strip()
            self.date = self._git("show", "-s", "--format=%cs", self.sha).decode().strip()
        else:
            self.ref = ref or "main"
            if re.fullmatch(r"[0-9a-f]{40}", self.ref):
                self.sha = self.ref
            else:
                out = subprocess.run(["git", "ls-remote", f"https://github.com/{slug}", self.ref],
                                     check=True, capture_output=True, text=True).stdout.split()
                if not out:
                    raise SystemExit(f"no ref {self.ref!r} in {slug}")
                self.sha = out[0]
            # The commit date only labels the headers; the API may be out of
            # reach (rate limits, a sandbox proxy), so it is best effort.
            try:
                api = f"https://api.github.com/repos/{slug}/commits/{self.sha}"
                with urllib.request.urlopen(urllib.request.Request(
                        api, headers={"Accept": "application/vnd.github+json"}), timeout=30) as r:
                    self.date = json.load(r)["commit"]["committer"]["date"][:10]
            except OSError:
                self.date = datetime.date.today().isoformat()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout

    def read(self, path, sha=None):
        sha = sha or self.sha
        if self.repo:
            return self._git("show", f"{sha}:{path}")
        url = f"https://raw.githubusercontent.com/{self.slug}/{sha}/{path}"
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()


def status(man, up):
    print(f"pinned  {man['ref'][:7]}  ({man['synced']})")
    print(f"target  {up.sha[:7]}  ({up.ref}, {up.date})\n")
    changed = 0
    for f in man["files"]:
        now = sha256(up.read(f["upstream"]))
        mark = "same   " if now == f["sha256"] else "CHANGED"
        changed += now != f["sha256"]
        print(f"{mark}  {f['upstream']}")
    for p in man.get("proposed", []):
        try:
            up.read(p["upstream"])
            print(f"ADOPTED  {p['upstream']}: now in landslidescience; move {p['local']} "
                  "into `files` and sync")
        except Exception:
            print(f"pending  {p['upstream']}  (proposed from {p['local']}, not upstream yet)")
    print(f"\n{changed} file(s) would change.")
    return 0


def sync(man, up):
    for f in man["files"]:
        data = up.read(f["upstream"])
        out = data
        if f.get("header"):
            out = HEADER.format(path=f["upstream"], short=up.sha[:7], date=up.date).encode() + data
        (ROOT / f["local"]).write_bytes(out)
        f["sha256"] = sha256(data)
    man["ref"] = up.sha
    man["synced"] = datetime.date.today().isoformat()
    MANIFEST.write_text(json.dumps(man, indent=2) + "\n")
    print(f"synced {len(man['files'])} files from {up.slug} @ {up.sha[:7]} ({up.date}).")
    print("Review `git diff`, update core/static/core/vendor/VENDOR.md if the demshade build "
          "changed, run the tests and the browser smoke test, then commit.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "status", "sync"])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--repo", type=pathlib.Path, help="a local landslidescience clone")
    src.add_argument("--github", action="store_true", help="read hig314/landslidescience on GitHub")
    ap.add_argument("--ref", help="commit, branch or tag (default origin/main, or main with --github)")
    a = ap.parse_args(argv)
    man = load()
    if a.command == "check":
        return check(man)
    if not a.repo and not a.github:
        ap.error(f"{a.command} needs --repo PATH or --github")
    up = Upstream(repo=a.repo, github=a.github, ref=a.ref, slug=man["upstream"])
    return status(man, up) if a.command == "status" else sync(man, up)


if __name__ == "__main__":
    sys.exit(main())
