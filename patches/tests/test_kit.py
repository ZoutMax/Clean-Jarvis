"""apply.py itself: all or nothing, a real exit code, atomic writes, www beside the install. Only temp folders are used."""
import ast, json, os, shutil, subprocess, sys, tempfile, unittest

import jh_harness as H

APPLY = os.path.join(H.KIT, "apply.py")


class Kit(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jh-kit-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.base = H.fresh_upstream(self.root)
        self.config = os.path.dirname(os.path.dirname(self.base))

    def snapshot(self):
        out = {}
        for folder, _dirs, files in os.walk(self.base):
            for name in files:
                with open(os.path.join(folder, name), "rb") as f:
                    out[os.path.join(folder, name)] = f.read()
        return out

    def run_apply(self):
        return subprocess.run([sys.executable, APPLY, self.base], capture_output=True, text=True, encoding="utf-8")

    def test_a_good_run_exits_zero_and_is_idempotent(self):
        first = self.run_apply()
        self.assertEqual(first.returncode, 0, first.stdout[-800:] + first.stderr[-800:])
        self.assertIn("NEXT:", first.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.config, "www")))   # Clean Jarvis ships no dashboard pages
        self.assertTrue(os.path.exists(os.path.join(self.base, "agent.py.orig-kit")))
        for name in os.listdir(self.base):
            if name.endswith(".py"):
                with open(os.path.join(self.base, name), encoding="utf-8") as f:
                    ast.parse(f.read())
        before = self.snapshot()
        second = self.run_apply()
        self.assertEqual(second.returncode, 0)
        self.assertNotIn("[applied]", second.stdout)
        self.assertNotIn("NEXT:", second.stdout)
        self.assertEqual(before, self.snapshot())

    def test_one_broken_anchor_writes_nothing_and_exits_non_zero(self):
        path = os.path.join(self.base, "agent.py")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        anchor = "async def _exec_read_email(hass: HomeAssistant, args: dict) -> str:"
        self.assertIn(anchor, text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(anchor, "async def _exec_read_mail_renamed(hass, args):"))
        before = self.snapshot()
        out = self.run_apply()
        self.assertEqual(out.returncode, 1, out.stdout[-500:])
        self.assertIn("[FAILED ] 12 agent", out.stdout)
        self.assertIn("no file was changed", out.stdout)
        self.assertNotIn("NEXT:", out.stdout)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(os.path.exists(os.path.join(self.config, "www")))

    def test_missing_target_file_writes_nothing(self):
        os.remove(os.path.join(self.base, "briefing.py"))
        before = self.snapshot()
        out = self.run_apply()
        self.assertEqual(out.returncode, 1)
        self.assertIn("[FAILED ]", out.stdout)
        self.assertEqual(before, self.snapshot())

    def test_missing_translation_section_is_a_failure(self):
        path = os.path.join(self.base, "translations", "en.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        del data["options"]["step"]["core"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        before = self.snapshot()
        out = self.run_apply()
        self.assertEqual(out.returncode, 1)
        self.assertIn("47d labels", out.stdout)
        self.assertEqual(before, self.snapshot())

    def test_every_translation_file_gets_the_new_options(self):
        self.assertEqual(self.run_apply().returncode, 0)
        def core(name):
            with open(os.path.join(self.base, "translations", name), encoding="utf-8") as f:
                return json.load(f)["options"]["step"]["core"]["data"]
        en = core("en.json")
        for key in ("jh_strict_actions", "jh_mail_block", "jh_facts_only_briefings", "camera_watch_default"):
            self.assertIn(key, en)
        names = [n for n in os.listdir(os.path.join(self.base, "translations")) if n.endswith(".json")]
        self.assertGreater(len(names), 7)
        for name in names:
            self.assertEqual(set(core(name)), set(en), name)
            self.assertEqual(core(name)["jh_mail_block"], en["jh_mail_block"], name)   # English until someone translates it

    def test_a_translation_already_present_is_kept(self):
        path = os.path.join(self.base, "translations", "nl.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["options"]["step"]["core"]["data"]["jh_mail_block"] = "Blokkeer acties na het lezen van mail"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(self.run_apply().returncode, 0)
        with open(path, encoding="utf-8") as f:
            core = json.load(f)["options"]["step"]["core"]["data"]
        self.assertEqual(core["jh_mail_block"], "Blokkeer acties na het lezen van mail")
        self.assertIn("jh_strict_actions", core)

    def test_a_new_language_file_is_followed(self):
        path = os.path.join(self.base, "translations", "xx.json")
        shutil.copy(os.path.join(self.base, "translations", "nl.json"), path)
        self.assertEqual(self.run_apply().returncode, 0)
        with open(path, encoding="utf-8") as f:
            self.assertIn("jh_strict_actions", json.load(f)["options"]["step"]["core"]["data"])

    def test_no_temp_files_are_left(self):
        self.assertEqual(self.run_apply().returncode, 0)
        left = [n for _d, _s, files in os.walk(self.config) for n in files if n.endswith((".tmp", ".kit-tmp"))]
        self.assertEqual(left, [])

    def test_in_process_run_commits_like_check_update(self):
        results = H.run_kit(self.base)
        self.assertFalse([r for r in results if r.startswith("[FAILED")])
        self.assertTrue(any("[applied]" in r for r in results))
        with open(os.path.join(self.base, "agent.py"), encoding="utf-8") as f:
            self.assertIn("_jh_note_tool", f.read())
        self.assertFalse([r for r in H.run_kit(self.base) if "[applied]" in r])


class Atomic(unittest.TestCase):
    def kit(self):
        with open(APPLY, encoding="utf-8") as f:
            s = f.read()
        g = {"__name__": "tests", "__file__": APPLY}
        exec(compile(s[:s.index("# 1 ---")], "apply-head", "exec"), g)
        return g

    def test_a_failed_write_keeps_the_old_file(self):
        g = self.kit()
        folder = tempfile.mkdtemp(prefix="jh-atomic-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        good = os.path.join(folder, "a.py")
        with open(good, "w", encoding="utf-8") as f:
            f.write("old")
        with self.assertRaises(Exception):
            g["_atomic_writes"]([(good, "new"), (os.path.join(folder, "missing", "b.py"), "x")])
        with open(good, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old")
        self.assertEqual(os.listdir(folder), ["a.py"])


if __name__ == "__main__":
    unittest.main()
