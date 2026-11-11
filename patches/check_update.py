"""Tests our patches on the newest original JARVIS release, in a temporary folder. Never touches the live install.
Run on the PC:  python check_update.py [version]
Exit code 0: every patch applied, safe to install.
Exit code 1: something needs fixing first.
Exit code 2: could not test (GitHub or the network failed). Says nothing about the patches.
JavaScript is checked with node when node is installed; the output says so plainly when it was not."""
import ast, glob, http.client, io, json, os, re, shutil, subprocess, sys, tempfile, urllib.error, urllib.request, zipfile

REPO = "sam3gp8/jarvis-aio"
KIT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "patch-check"}
BASE_LINE = 'BASE = sys.argv[1] if len(sys.argv) > 1 else "/config/custom_components/jarvis"'
KIT_LINE = "KIT = os.path.dirname(os.path.abspath(__file__))"


class KitProblem(Exception):
    pass


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def get(url, t=120):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t).read()


def run_kit(base, kit=None):
    kit = kit or KIT
    s = read_text(os.path.join(kit, "apply.py"))
    head = s[:s.index("# 1 ---")]
    for line in (BASE_LINE, KIT_LINE):
        if line not in head:
            raise KitProblem("apply.py no longer has the line: " + line)   # the test would run against the wrong folder
    head = head.replace(BASE_LINE, "BASE = %r" % base).replace(KIT_LINE, "KIT = %r" % kit)
    g = {"__name__": "check"}
    exec(compile(head + s[s.index("# 1 ---"):s.index("\n# 10 ")], "apply", "exec"), g)
    return g["results"]


def missing_kit_files(kit=None):
    """Section 10 copies card files into /config/www. It is not run here, so check its file list without copying."""
    kit = kit or KIT
    s = read_text(os.path.join(kit, "apply.py"))
    names = re.findall(r'"([\w.-]+\.(?:js|png))"', s[s.index("\n# 10 "):])
    if not names:
        return ["section 10: no card file list found in apply.py"]
    return ["section 10: %s missing from the kit" % n for n in names if not os.path.exists(os.path.join(kit, n))]


def js_errors(files, node):
    """Syntax check. Module files only parse as .mjs, so a file that fails is tried once more that way."""
    bad = []
    for f in files:
        first = subprocess.run(node + ["--check", f], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if first.returncode == 0:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, os.path.basename(f) + ".mjs")
            shutil.copyfile(f, copy)
            second = subprocess.run(node + ["--check", copy], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if second.returncode != 0:
            bad.append("%s: %s" % (os.path.basename(f), (first.stderr.strip().splitlines() or ["syntax error"])[-1][:80]))
    return bad


def check_js(base, kit=None, node=None):
    """Returns (errors, note). The note tells whether JavaScript was really checked."""
    found = shutil.which("node") or next((p for p in (os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "node.exe"),) if os.path.isfile(p)), None)
    node = node or ([found] if found else None)
    if not node:
        return [], "JavaScript NOT checked: node is not installed on this PC"
    files = glob.glob(os.path.join(base, "**", "*.js"), recursive=True) + glob.glob(os.path.join(kit or KIT, "*.js"))
    return js_errors(files, node), "JavaScript checked: %d file(s)" % len(files)


def download(version, work):
    """Everything that needs the network. Raises OSError or BadZipFile or the like when it could not be fetched."""
    if not version:
        version = json.loads(get("https://api.github.com/repos/%s/releases/latest" % REPO, 30))["tag_name"]
    zipfile.ZipFile(io.BytesIO(get("https://github.com/%s/archive/refs/tags/%s.zip" % (REPO, version)))).extractall(work)
    return version, glob.glob(os.path.join(work, "*", "custom_components", "jarvis"))[0]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    work = tempfile.mkdtemp(prefix="jarvis-check-")
    try:
        try:
            version, base = download(argv[0] if argv else None, work)
        except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, http.client.HTTPException) as exc:
            print("RESULT: could not test, the release could not be fetched:", str(exc)[:200])
            return 2
        res = run_kit(base)
        applied = sum("[applied]" in r for r in res)
        failed = [r[:120] for r in res if "[FAILED" in r] + missing_kit_files()
        # A clean release has none of our markers, so a patch that says [already] on the first run was skipped,
        # not applied: the release happens to contain its marker text (2026-11-07).
        failed += ["skipped on a clean release, its marker is already there: " + r[10:110] for r in res if r.startswith("[already]")]
        second = run_kit(base)
        again = sum("[applied]" in r for r in second)
        failed += [r[:120] for r in second if "[FAILED" in r]
        bad = []
        for f in glob.glob(os.path.join(base, "**", "*.py"), recursive=True):
            try:
                ast.parse(read_text(f))
            except Exception as e:
                bad.append("%s: %s" % (os.path.basename(f), str(e)[:80]))
        js_bad, js_note = check_js(base)
        print("version", version, "| applied", applied, "| failed", len(failed), "| second run changed", again, "| python errors", len(bad), "| javascript errors", len(js_bad))
        print(js_note)
        for x in failed + bad + js_bad:
            print(" ", x)
        ok = applied > 0 and not failed and not again and not bad and not js_bad
        print("RESULT:", "safe to install" if ok else "NOT safe, fix the patches first")
        return 0 if ok else 1
    except KitProblem as exc:
        print(exc)
        print("RESULT: NOT safe, fix the patches first")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
