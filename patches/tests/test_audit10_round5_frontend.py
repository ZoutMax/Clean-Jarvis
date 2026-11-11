"""Audit 10 round 5 (2026-11-08): section 96d of apply.py, Camera Watch (a still is judged once it has loaded; while NO FRAME
shows, the chain is tried again about once a minute). The behaviour is tested in real Chrome in test_frontend.py (CameraInTheDark);
here, offline: the upgrade path and the real retry period.
Temp folders only, no Home Assistant and no network into the home."""
import os, re, shutil, tempfile, unittest

import jh_harness as H

APPLY = os.path.join(H.KIT, "apply.py")


def _run_source(src, base):
    head = src[:src.index("# 1 ---")]
    head = head.replace('BASE = sys.argv[1] if len(sys.argv) > 1 else "/config/custom_components/jarvis"', "BASE = %r" % base)
    head = head.replace("KIT = os.path.dirname(os.path.abspath(__file__))", "KIT = %r" % H.KIT)
    g = {"__name__": "tests"}
    exec(compile(head + src[src.index("# 1 ---"):src.index("\n# 10 ")], "apply", "exec"), g)
    return g["results"]


def _without_96d(src):
    start = src.index("# d (round 5): two review findings on 96c")
    stop = src.index("\n", src.index('], "96d panel:', start)) + 1
    return src[:start] + src[stop:]


class UpgradePath96d(unittest.TestCase):
    def test_an_install_with_every_earlier_step_gets_only_96d_and_ends_as_a_fresh_build(self):
        src = open(APPLY, encoding="utf-8").read()
        work = tempfile.mkdtemp(prefix="jh-round5fe-")
        self.addCleanup(shutil.rmtree, work, True)
        base = H.fresh_upstream(os.path.join(work, "up"))
        first = _run_source(_without_96d(src), base)
        self.assertFalse([r for r in first if r.startswith("[FAILED")], first)
        self.assertFalse([r for r in first if r.startswith("[applied] 96d")])
        self.assertTrue([r for r in first if r.startswith("[applied] 96c")])
        second = _run_source(src, base)
        self.assertFalse([r for r in second if r.startswith("[FAILED")], second)
        self.assertEqual([r.split()[1] for r in second if r.startswith("[applied]")], ["96d"], second)
        third = _run_source(src, base)
        self.assertFalse([r for r in third if r.startswith(("[applied]", "[FAILED"))], third)
        fresh = H.fresh_upstream(os.path.join(work, "fresh"))
        self.assertFalse([r for r in _run_source(src, fresh) if r.startswith("[FAILED")])
        panel = os.path.join("frontend", "jarvis-panel.js")
        with open(os.path.join(base, panel), encoding="utf-8") as a, open(os.path.join(fresh, panel), encoding="utf-8") as b:
            self.assertEqual(a.read(), b.read(), "the upgraded panel is the same as a fresh build")


class RetryPeriod(unittest.TestCase):
    """The tests in Chrome shorten the NO FRAME retry; the real one is about a minute, and it is asked for only by NO FRAME."""

    def test_the_real_retry_comes_about_once_a_minute_and_only_for_no_frame(self):
        with open(os.path.join(H.built_dir(), "frontend", "jarvis-panel.js"), encoding="utf-8") as f:
            js = f.read()
        calls = re.findall(r"this\._camRetryLater\(([^)]*)\)", js)
        self.assertEqual(calls, ["entity, 60000"], calls)
        line = next(l for l in js.splitlines() if "this._camRetryLater(entity, 60000)" in l)
        self.assertIn('if (text.startsWith("NO FRAME"))', line)


if __name__ == "__main__":
    unittest.main()
