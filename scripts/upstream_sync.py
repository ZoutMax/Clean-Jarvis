#!/usr/bin/env python3
"""Weekly Clean Jarvis upstream sync, run by .github/workflows/upstream-sync.yml.

prepare: find the newest release of the original JARVIS. When it is newer than patches/UPSTREAM (or with --force),
apply the Clean Jarvis patch kit (patches/apply.py) to it and build ONE Clean Jarvis commit on top of that release:
the previous Clean Jarvis commit is replayed, the component is replaced by the patched build, the version goes up by
one and the changelog gets a new Clean Jarvis entry. Nothing is pushed here: the publish job does that after all checks.

Writes into --out: candidate.bundle (the commit), info.json (what happened) and built/ (the patched component, for the
kit tests). Step outputs: needed, upstream, version, commit, head, built.

Usage:  python scripts/upstream_sync.py prepare --out DIR [--force] [--upstream TAG]
"""
import argparse, io, json, os, re, shutil, subprocess, sys, tarfile, urllib.error, urllib.request

UPSTREAM_REPO = "sam3gp8/jarvis-aio"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP = "custom_components/jarvis"
CJ_MANIFEST_KEYS = ("codeowners", "documentation", "issue_tracker", "version")
IDENTITY = {"GIT_AUTHOR_NAME": "ZoutMax", "GIT_AUTHOR_EMAIL": "ZoutMax@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "ZoutMax", "GIT_COMMITTER_EMAIL": "ZoutMax@users.noreply.github.com"}


class SyncError(Exception):
    pass


def git(*args, cwd=ROOT, check=True, env=None, binary=False):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env={**os.environ, **(env or {})})
    if check and r.returncode:
        raise SyncError("git %s failed: %s" % (" ".join(args), (r.stderr or r.stdout).decode("utf-8", "replace").strip()[-800:]))
    return r.stdout if binary else r.stdout.decode("utf-8", "replace").strip()


def vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def bump(v):
    a, b, c = (v.split(".") + ["0", "0"])[:3]
    return "%s.%s.%d" % (a, b, int(c) + 1)


def latest_upstream():
    req = urllib.request.Request("https://api.github.com/repos/%s/releases/latest" % UPSTREAM_REPO,
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "clean-jarvis-sync"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
    return json.load(urllib.request.urlopen(req, timeout=60))["tag_name"]


def output(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    for k, v in kw.items():
        print("%s=%s" % (k, v))
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s=%s\n" % (k, v))


def run_kit(base):
    sys.path.insert(0, os.path.join(ROOT, "patches"))
    import check_update
    return check_update.run_kit(base, kit=os.path.join(ROOT, "patches"))


def mirror(src, dst):
    """Make dst exactly the build in src (kit backups and caches left out)."""
    def files(root):
        out = set()
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d != "__pycache__"]
            out.update(os.path.relpath(os.path.join(dp, f), root) for f in fn if not f.endswith((".orig-kit", ".pyc")))
        return out
    s, d = files(src), files(dst)
    for rel in d - s:
        os.remove(os.path.join(dst, rel))
    for rel in s:
        os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
        shutil.copyfile(os.path.join(src, rel), os.path.join(dst, rel))


def changelog_entry(text, old_base, new_base, version):
    m = re.match(r"(## \[[^\]]+\] — Clean Jarvis on JARVIS [^\n]*\n.*?)(?=\n## \[)", text, re.S)
    if not m:
        raise SyncError("CHANGELOG.md does not start with a Clean Jarvis entry")
    lines = m.group(1).rstrip("\n").split("\n")
    lines[0] = "## [%s] — Clean Jarvis on JARVIS %s" % (version, new_base)
    entry = "\n".join(lines).replace("JARVIS %s" % old_base, "JARVIS %s" % new_base)
    entry = re.sub(r"(?m)^- Based on JARVIS .*$", "- Based on JARVIS %s; the changes of the original are listed below. "
                   "Existing JARVIS data and settings are kept." % new_base, entry)
    return entry + "\n"


def prepare(a):
    os.makedirs(a.out, exist_ok=True)
    info = {"ok": False}
    try:
        old_base = open(os.path.join(ROOT, "patches", "UPSTREAM"), encoding="utf-8").read().strip()
        latest = a.upstream or latest_upstream()
        info.update(old_base=old_base, upstream=latest)
        if vt(latest) <= vt(old_base) and not a.force:
            print("Clean Jarvis is up to date: JARVIS %s is the newest release." % latest)
            info.update(ok=True, needed=False)
            output(needed="false", upstream=latest)
            return 0
        head = git("rev-parse", "HEAD")
        head_msg = git("log", "-1", "--format=%B", head)
        if not head_msg.startswith("Clean Jarvis "):
            raise SyncError("the newest commit on main is not a Clean Jarvis commit: " + head_msg.splitlines()[0])
        git("fetch", "--no-tags", "--quiet", "https://github.com/%s.git" % UPSTREAM_REPO,
            "+refs/tags/%s:refs/tags/upstream/%s" % (latest, latest))
        up = git("rev-parse", "refs/tags/upstream/%s^{commit}" % latest)

        # 1. the patched component, built from the clean release
        built_root = os.path.join(a.out, "built")
        shutil.rmtree(built_root, ignore_errors=True)
        tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", up, COMP, binary=True))).extractall(built_root, filter="data")
        built = os.path.join(built_root, COMP)
        results = run_kit(built)
        failed = [r for r in results if r.startswith("[FAILED")]
        info["kit"] = {"applied": sum(r.startswith("[applied]") for r in results), "steps": len(results), "failed": failed}
        print("patch kit: %d steps, %d failed" % (len(results), len(failed)))
        if failed:
            raise SyncError("The Clean Jarvis patch does not fit JARVIS %s yet:\n%s" % (latest, "\n".join(failed)))

        # 2. the candidate: the release + the previous Clean Jarvis commit, component replaced by the build
        wt = os.path.join(a.out, "candidate")
        if os.path.exists(wt):
            git("worktree", "remove", "--force", wt, check=False)
            shutil.rmtree(wt, ignore_errors=True)
        git("worktree", "prune")
        git("worktree", "add", "--detach", "--force", wt, up)
        pick = subprocess.run(["git", "cherry-pick", "--no-commit", head], cwd=wt, capture_output=True, text=True)
        conflicts = git("diff", "--name-only", "--diff-filter=U", cwd=wt).split()
        other = [c for c in conflicts if not c.startswith(COMP + "/") and c != "CHANGELOG.md"]
        if other:
            raise SyncError("Clean Jarvis changes outside the component clash with JARVIS %s: %s" % (latest, ", ".join(other)))
        if pick.returncode and not conflicts:
            raise SyncError("replaying the Clean Jarvis commit failed: " + (pick.stderr or pick.stdout)[-800:])
        old_manifest = json.loads(git("show", "%s:%s/manifest.json" % (head, COMP)))
        mirror(built, os.path.join(wt, COMP))
        man_path = os.path.join(wt, COMP, "manifest.json")
        with open(man_path, encoding="utf-8") as f:
            manifest = json.load(f)
        for k in CJ_MANIFEST_KEYS:
            manifest[k] = old_manifest[k]
        version = bump(old_manifest["version"])
        manifest["version"] = version
        with open(man_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        os.makedirs(os.path.join(wt, "patches"), exist_ok=True)
        with open(os.path.join(wt, "patches", "UPSTREAM"), "w", encoding="utf-8", newline="\n") as f:
            f.write(latest + "\n")
        entry = changelog_entry(git("show", "%s:CHANGELOG.md" % head), old_base, latest, version)
        with open(os.path.join(wt, "CHANGELOG.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(entry + "\n" + git("show", "%s:CHANGELOG.md" % up).rstrip("\n") + "\n")
        git("add", "-A", cwd=wt)
        markers = git("grep", "-l", "-E", "^(<<<<<<<|>>>>>>>) ", cwd=wt, check=False)
        if markers:
            raise SyncError("conflict markers left in: " + markers.replace("\n", ", "))
        body = head_msg.split("\n", 1)[1].strip() if "\n" in head_msg else ""
        body = body.replace("JARVIS %s" % old_base, "JARVIS %s" % latest)
        subject = "Clean Jarvis %s: honest, private patch of JARVIS %s" % (version, latest)
        git("commit", "-q", "--no-verify", "-m", subject, *(["-m", body] if body else []), cwd=wt, env=IDENTITY)
        cand = git("rev-parse", "HEAD", cwd=wt)
        if git("rev-parse", cand + "^", cwd=wt) != up:
            raise SyncError("the candidate is not exactly one commit on top of JARVIS %s" % latest)
        git("update-ref", "refs/heads/cj-candidate", cand)
        git("bundle", "create", os.path.join(a.out, "candidate.bundle"), "refs/heads/cj-candidate", "^" + up)
        info.update(ok=True, needed=True, version=version, commit=cand, head=head, subject=subject)
        output(needed="true", upstream=latest, version=version, commit=cand, head=head, built=built)
        print("candidate %s: %s" % (cand[:7], subject))
        return 0
    except (SyncError, OSError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
        info["error"] = str(exc)
        print("::error::%s" % str(exc).replace("\n", "%0A"))
        return 1
    finally:
        with open(os.path.join(a.out, "info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["prepare"])
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true", help="build even when the newest release is not newer")
    p.add_argument("--upstream", help="use this release tag instead of the newest one")
    a = p.parse_args()
    a.out = os.path.abspath(a.out)
    return prepare(a)


if __name__ == "__main__":
    sys.exit(main())
