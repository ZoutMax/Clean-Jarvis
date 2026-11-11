"""Audit 10 round 3 (2026-11-07): the review-0 findings on section 94, fixed in section 97 of apply.py.
Offline: a fake hass and temp folders, no Home Assistant and no network into the home."""
import json, os, re, tempfile, shutil, unittest

import jh_harness as H

le = H.load("local_engine")
agent = H.load("agent")

APPLY = os.path.join(H.KIT, "apply.py")
FAILED_LINE = "That did not go through, sir. Nothing was changed."


def problem(text):
    """The truth check on a device command that had no tool call at all."""
    return agent._jh_problem(text, agent._jh_new_state(), True)


# ── 1 (medium): a question-word clause in the middle of a claim hides nothing ───────────────────────

class QuestionWordClause(unittest.TestCase):
    CLAIMS = ("The heater, which was off, is now on, sir, shall I set it to 21?",
              "The bedroom light, which was off, is on now, sir, would you like it dimmer?",
              "The kitchen light, where you asked, is on, sir, shall I dim it?",
              "Right, so when you asked, I switched on the heater, shall I set it to 21?",
              "What I did, sir: the heater is on, shall I set it to 21?",
              "The heater is on, sir, which temperature would you like?")
    QUESTIONS = ("Set to which temperature, sir?", "Set to what level, sir?", "Switched to which mode, sir?",
                 "Set to which temperature, madam?", "Op welke stand, meneer?", "Hoeveel graden, mevrouw?",
                 "Right away, sir, which colour?", "Of the two heaters, sir, which one shall I turn on?",
                 "Which one, sir: Disc or Disk?", "Kitchen or hall, sir?", "Which one should I set, sir, the heater or the lamp?")

    def test_a_relative_clause_does_not_hide_the_claim(self):
        for text in self.CLAIMS:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)

    def test_a_question_word_clause_at_the_end_or_before_an_address_is_the_question(self):
        for text in self.QUESTIONS:
            self.assertIsNone(problem(text), text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), text)


# ── 4 (low): an -ing word claims only where a clause starts, after now, or after I am / is / are ────

class IngWordWhereItClaims(unittest.TestCase):
    HONEST = ("Before turning anything on, sir, which room did you mean?",
              "Before switching anything on, sir, which room did you mean?",
              "To avoid switching the wrong one, sir, which lamp do you mean?",
              "Without changing anything yet, sir, do you mean the heater or the lamp?",
              "At the current setting of 20, sir, shall I raise it to 22?")
    CLAIMS = ("Turning the heater on now, sir, shall I set it to 21?",
              "Switching on the kitchen light, sir, would you like it dimmer?",
              "Sir, I'm turning the heater on, shall I set it to 21?",
              "I am switching on the lamp, sir, shall I dim it?",
              "Right away, sir, now switching on the lamp, would you like it dimmer?",
              "The heater is turning on, sir, shall I set it to 21?",
              "Locking the front door, sir, shall I arm the alarm too?")

    def test_honest_clarifying_questions_stand(self):
        for text in self.HONEST:
            self.assertIsNone(problem(text), text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), text)

    def test_ing_claims_are_still_claims(self):
        for text in self.CLAIMS:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)


# ── 5 (low): Dutch "is (nu) aan" claims, and a lead-in such as "Wat fijn," asks nothing ──────────────

class DutchClaimsAndLeadIns(unittest.TestCase):
    CLAIMS = ("De verwarming is nu aan, meneer, zal ik hem hoger zetten?",
              "Het licht is aan, meneer, wilt u het dimmen?",
              "De lampen zijn uit, meneer, zal ik de verwarming ook lager zetten?",
              "De deur is dicht, meneer, zal ik hem op slot doen?",
              "Wat fijn, meneer, de verwarming staat aan, zal ik hem op 21 zetten?",
              "Wat fijn, meneer, de verwarming is aan, zal ik hem op 21 zetten?",
              "How lovely, the heater is on, shall I set it to 21?",
              "When you said bedroom, I switched on the heater, shall I set it to 21?")
    QUESTIONS = ("Wat wilt u, meneer?", "Hoe laat, meneer, zal ik de wekker zetten?", "Is de verwarming aan, meneer?",
                 "Welke lamp, meneer?", "Kan ik nog iets doen, meneer?", "How may I help, sir?", "Where to, sir?",
                 "What colour would you like, sir?", "Shall I turn on the kitchen light, sir?", "Certainly, sir, shall I turn it on?")

    def test_dutch_and_lead_in_claims_are_caught(self):
        for text in self.CLAIMS:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)

    def test_real_questions_still_stand(self):
        for text in self.QUESTIONS:
            self.assertIsNone(problem(text), text)


# ── 2 (medium): a moving device plus one waiting for confirmation ───────────────────────────────────

class MovingAndWaiting(unittest.TestCase):
    MOVING = {"success": False, "entity_id": "cover.garage", "previous_state": "closed", "new_state": "opening", "confirmed": False,
              "action": "open", "sent": True, "in_progress": True, "warning": "still opening"}
    WAIT = {"status": "awaiting_confirmation", "entity_id": "lock.front", "message": "Confirmation required before unlock on lock.front."}
    BOTH = ("Only part of that went through, sir. Garage Door is still opening, sir. "
            "Front Door is waiting for your confirmation, sir.")

    def setUp(self):
        self.h = H.Hass()
        self.h.states.set("cover.garage", "opening", friendly_name="Garage Door")
        self.h.states.set("lock.front", "locked", friendly_name="Front Door")

    def st(self, moving=True, waiting=True):
        st = agent._jh_new_state()
        if moving:
            agent._jh_note_tool(st, "control_device", json.dumps(self.MOVING), {"entity_id": "cover.garage", "action": "open"}, self.h)
        if waiting:
            agent._jh_note_tool(st, "control_device", json.dumps(self.WAIT), {"entity_id": "lock.front", "action": "unlock"}, self.h)
        return st

    def test_the_moving_one_is_never_reported_as_nothing_changed(self):
        for reply in ("The garage door is opening, sir. Shall I unlock the front door once you confirm?",
                      "The garage door is opening, sir.", "The garage door is open, sir.", "Done, sir."):
            final = agent._jh_final(reply, self.st(), True, "sir")
            self.assertNotIn("Nothing has been changed", final, reply)
            self.assertEqual(final, self.BOTH, reply)

    def test_a_reply_that_names_both_truthfully_stands(self):
        reply = "The garage door is opening and the front door is waiting for your confirmation, sir."
        self.assertIsNone(agent._jh_problem(reply, self.st(), True))
        self.assertEqual(agent._jh_final(reply, self.st(), True, "sir"), reply)

    def test_no_retry_is_asked_for_a_device_that_waits(self):
        self.assertIsNone(agent._jh_nudge("The garage door is opening, sir.", self.st(), True))

    def test_waiting_alone_and_moving_alone_are_unchanged(self):
        self.assertEqual(agent._jh_final("Done, sir.", self.st(moving=False), True, "sir"),
                         "Front Door is waiting for your confirmation, sir. Nothing has been changed yet.")
        self.assertEqual(agent._jh_final("The garage door is opening, sir.", self.st(waiting=False), True, "sir"),
                         "The garage door is opening, sir.")
        self.assertEqual(agent._jh_final("The garage door is open, sir.", self.st(waiting=False), True, "sir"),
                         "Garage Door is still opening, sir; it has not confirmed yet.")


# ── 3 (medium + low): wake-up alarms by a time anywhere; arm, disarm and activate stay orders ────────

class WakeUpAlarmPhrasings(unittest.TestCase):
    WAKE = ("set a 7 am alarm", "set a 7am alarm", "set an alarm tomorrow at 7", "set an alarm for an hour from now",
            "set an alarm for in 20 minutes", "set my morning alarm", "turn off my 7 am alarm", "zet een alarm voor 7 uur",
            "zet mijn alarm op 7 uur", "set my wake-up alarm", "set an alarm for seven am",
            # round 2 cases stay wake-up alarms
            "set an alarm for 7", "set the alarm to 7 am", "set alarms for 7 and 8", "turn off my alarm clock", "zet een alarm om 7 uur")
    SYSTEM = ("switch on the alarm", "arm the alarm", "disarm the alarm", "turn off the alarm", "turn off the alarms",
              "switch off the alarms", "activate the alarm", "turn on the alarm system", "set the alarm to away",
              "arm the alarm at 11 pm", "arm the alarm in 5 minutes", "disarm the alarm at 7", "please arm the alarm at 11 pm",
              "activate the alarm at 10 pm")

    def cmd(self, h, text):
        return le.device_command(h, text, {})

    def homes(self):
        bare = H.Hass()
        panel = H.Hass()
        panel.states.set("alarm_control_panel.home", "disarmed", friendly_name="Home Security")
        return (("no panel", bare), ("panel", panel))

    def test_wake_up_alarms_are_no_device_command_with_or_without_a_panel(self):
        for where, h in self.homes():
            for text in self.WAKE:
                self.assertFalse(self.cmd(h, text), (where, text))

    def test_the_alarm_system_stays_a_device_command_with_or_without_a_panel(self):
        for where, h in self.homes():
            for text in self.SYSTEM:
                self.assertTrue(self.cmd(h, text), (where, text))

    def test_a_set_reminder_stands(self):
        h = H.Hass()
        st = agent._jh_new_state()
        agent._jh_note_tool(st, "schedule_followup", json.dumps({"success": True}))
        for text in ("set a 7 am alarm", "set an alarm tomorrow at 7", "set an alarm for an hour from now", "zet mijn alarm op 7 uur"):
            self.assertEqual(agent._jh_final("Your alarm is set, sir.", st, self.cmd(h, text), "sir"), "Your alarm is set, sir.", text)

    def test_a_false_armed_claim_with_a_time_is_replaced(self):
        h = H.Hass()
        for text in ("arm the alarm at 11 pm", "arm the alarm in 5 minutes"):
            self.assertEqual(agent._jh_final("The alarm is armed, sir.", agent._jh_new_state(), self.cmd(h, text), "sir"),
                             FAILED_LINE, text)

    def test_other_commands_with_a_time_are_unchanged(self):
        h = H.Hass()
        h.states.set("light.kitchen", "off", friendly_name="Kitchen Light")
        self.assertTrue(self.cmd(h, "turn on the kitchen light"))
        self.assertTrue(self.cmd(h, "dim the kitchen light to 30"))


# ── kit: an install that already has 1-95 gets only the new steps, and a second run changes nothing ─

def _run_source(src, base):
    head = src[:src.index("# 1 ---")]
    head = head.replace('BASE = sys.argv[1] if len(sys.argv) > 1 else "/config/custom_components/jarvis"', "BASE = %r" % base)
    head = head.replace("KIT = os.path.dirname(os.path.abspath(__file__))", "KIT = %r" % H.KIT)
    g = {"__name__": "tests"}
    exec(compile(head + src[src.index("# 1 ---"):src.index("\n# 10 ")], "apply", "exec"), g)
    return g["results"]


class UpgradePath(unittest.TestCase):
    def test_only_the_new_steps_apply_on_an_install_that_has_the_earlier_ones(self):
        src = open(APPLY, encoding="utf-8").read()
        start = src.index("# 97 ---")
        # Round 4: section 98 is built on 97's text, so an install without 97 has no 98 either. Both go, up to section 99.
        # up to section 101 (independent of 97-100, from the live test) or else 99
        stop = min(i for i in (src.find("\n# 101 ---", start), src.find("\n# 99 ---", start)) if i >= 0) + 1
        later = [s for s in re.findall(r'\],\s*"(\d+[a-z]+) ', src[start:stop]) if not s.startswith("97")]   # later steps there
        without = src[:start] + src[stop:]
        work = tempfile.mkdtemp(prefix="jh-round3-")
        self.addCleanup(shutil.rmtree, work, True)
        base = H.fresh_upstream(work)
        first = _run_source(without, base)
        self.assertFalse([r for r in first if r.startswith("[FAILED")], first)
        self.assertFalse([r for r in first if r.startswith("[applied] 97")])
        second = _run_source(src, base)
        self.assertFalse([r for r in second if r.startswith("[FAILED")], second)
        applied = [r for r in second if r.startswith("[applied]")]
        self.assertEqual(sorted(r.split()[1] for r in applied), sorted(["97a", "97b", "97c"] + later), applied)
        third = _run_source(src, base)
        self.assertFalse([r for r in third if r.startswith(("[applied]", "[FAILED"))], third)
