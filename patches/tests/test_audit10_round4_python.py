"""Audit 10 round 4 (2026-11-07): the review-0 findings on section 97, fixed in section 98 of apply.py.
Offline: a fake hass and temp folders, no Home Assistant and no network into the home."""
import json, os, re, shutil, tempfile, unittest

import jh_harness as H

le = H.load("local_engine")
agent = H.load("agent")

APPLY = os.path.join(H.KIT, "apply.py")
FAILED_LINE = "That did not go through, sir. Nothing was changed."
NOT_SENT = "That did not go through, sir. Nothing was changed."


def problem(text, st=None):
    """The truth check on a device command: no tool call at all, or the tools in st."""
    return agent._jh_problem(text, st or agent._jh_new_state(), True)


def failed_tool():
    """A device command whose control_device call came back with an error."""
    st = agent._jh_new_state()
    h = H.Hass()
    h.states.set("switch.heater", "unavailable", friendly_name="Heater")
    agent._jh_note_tool(st, "control_device", json.dumps({"success": False, "error": "Entity is unavailable"}),
                        {"entity_id": "switch.heater", "action": "turn_on"}, h)
    return st


# ── 1 (medium): an -ing device word claims, unless the word just before it says otherwise ────────────

class IngClaimsByDenyList(unittest.TestCase):
    CLAIMS = ("Sure thing, the heater's turning on, shall I set it to 21?",
              "Sure thing, the heater’s turning on, shall I set it to 21?",
              "Okay sir switching on the heater, shall I set it to 21?",
              "Certainly, I'll be switching on the heater, shall I set it to 21?",
              "Heater turning on now, sir, shall I set it to 21?",
              "Very well, sir, I have begun turning on the heater, shall I set it to 21?",
              # round 3 claims stay claims
              "Turning the heater on now, sir, shall I set it to 21?",
              "Sir, I'm turning the heater on, shall I set it to 21?",
              "Right away, sir, now switching on the lamp, would you like it dimmer?",
              "Locking the front door, sir, shall I arm the alarm too?")
    HONEST = ("Before turning anything on, sir, which room did you mean?",
              "Before switching anything on, sir, which room did you mean?",
              "To avoid switching the wrong one, sir, which lamp do you mean?",
              "Without changing anything yet, sir, do you mean the heater or the lamp?",
              "At the current setting of 20, sir, shall I raise it to 22?",
              "Instead of switching on the heater, sir, shall I turn on the lamp?",
              "After turning it on, sir, shall I set it to 21?",
              # these stood in round 3 and still do: a not, an offer, a noun
              "I am not changing anything yet, sir, which lamp do you mean?",
              "I won’t be switching anything on until you say, sir, which lamp do you mean?",
              "I can try turning it on again, sir, shall I?",
              "Your setting is 20, sir, shall I raise it?")
    # an attempt that did not happen is still a claim
    ATTEMPT = ("I tried switching on the heater, sir, shall I try again?",)

    def test_ing_claims_are_caught_with_no_tool(self):
        for text in self.CLAIMS + self.ATTEMPT:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)

    def test_ing_claims_are_caught_after_a_failed_tool(self):
        for text in self.CLAIMS:
            st = failed_tool()
            self.assertEqual(problem(text, st), "failed", text)
            self.assertNotEqual(agent._jh_final(text, st, True, "sir"), text, text)
            self.assertTrue(agent._jh_nudge(text, failed_tool(), True), text)   # sent back once

    def test_honest_questions_stand(self):
        for text in self.HONEST:
            self.assertIsNone(problem(text), text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), text)


# ── 4 (low): choices or please after the question, a condition, "het is aan u" ─────────────────────

class HonestQuestionsThatStand(unittest.TestCase):
    QUESTIONS = ("Set to which temperature, sir, 20 or 22?",
                 "Set to which temperature, sir, please?",
                 "Set to which temperature, sir, 20, 21 or 22?",
                 "Set to which position, sir, open or closed?",
                 "Op welke stand, meneer, 20 of 22?",
                 "When it is on, sir, shall I set it to 21?",
                 "If the front door is open, sir, shall I close it?",
                 "Het is aan u, meneer, welke lamp wilt u aan?",
                 "Dat is aan jou, meneer, welke lamp wilt u aan?")
    CLAIMS = ("I turned on the heater, sir, 20 or 22?",
              "The heater is on, sir, 20 or 22?",
              "Done, which temperature, sir, 20 or 22?",
              "Set to which temperature, sir, the heater is on, 20 or 22?",
              "Once I switched it on, sir, shall I set it to 21?",
              "Since the heater is on, sir, shall I set it to 21?",
              "Now that it is on, sir, shall I set it to 21?",
              "When you said bedroom, I switched on the heater, shall I set it to 21?",
              "De verwarming staat aan, u kunt hem hoger zetten, meneer, wilt u dat?",
              "De verwarming is nu aan, meneer, zal ik hem hoger zetten?",
              "Het licht is aan, meneer, wilt u het dimmen?")

    def test_these_questions_stand(self):
        for text in self.QUESTIONS:
            self.assertIsNone(problem(text), text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), text)

    def test_a_claim_before_choices_or_inside_a_condition_still_claims(self):
        for text in self.CLAIMS:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)


# ── 2 + 3 (low): wake-up alarms always go; the alarm system keeps its words with a time ─────────────

class AlarmWords(unittest.TestCase):
    WAKE = ("activate my alarm for 7 am", "deactivate my alarm clock", "activate my wake-up alarm", "deactivate my weekday alarm",
            "arm my alarm clock", "disarm my weekend alarm", "activate the alarm clock",
            # round 3 wake-up phrasings stay no command
            "set a 7 am alarm", "set an alarm tomorrow at 7", "set my morning alarm", "turn off my 7 am alarm",
            "zet mijn alarm op 7 uur", "set the alarm for 7 am", "set the alarm to 7 am", "set the alarm to go off at 7",
            "set an alarm for 10 at night", "set the alarm on monday at 7", "zet het alarm op 7 uur",
            # unchanged from rounds 1-3 (owner decision still open): the time straight after 'the alarm'
            "turn off the alarm at 7", "turn on the alarm at 11")
    SYSTEM = ("set the alarm to away mode at 11 pm", "set the alarm to away in 10 minutes", "turn the alarm off at 7",
              "zet het alarm aan om 23 uur", "schakel het alarm in om 23 uur", "turn off alarm 1", "turn off the alarm in zone 2",
              "zet het alarm op afwezig om 23 uur", "set the alarm system to night at 11 pm",
              # decided in round 4: a delay with 'the alarm' switched off is the alarm system
              "turn off the alarm in 10 minutes",
              # round 3 alarm-system orders stay commands
              "arm the alarm at 11 pm", "arm the alarm in 5 minutes", "disarm the alarm at 7", "activate the alarm at 10 pm",
              "switch on the alarm", "turn off the alarm", "set the alarm to away", "activate the alarm")

    def homes(self):
        bare = H.Hass()
        panel = H.Hass()
        panel.states.set("alarm_control_panel.home", "disarmed", friendly_name="Home Security")
        return (("no panel", bare), ("panel", panel))

    def test_wake_up_alarms_are_no_device_command(self):
        for where, h in self.homes():
            for text in self.WAKE:
                self.assertFalse(le.device_command(h, text, {}), (where, text))

    def test_alarm_system_orders_with_a_time_stay_device_commands(self):
        for where, h in self.homes():
            for text in self.SYSTEM:
                self.assertTrue(le.device_command(h, text, {}), (where, text))

    def test_a_set_reminder_stands_after_activate_my_alarm(self):
        h = H.Hass()
        for text in ("activate my alarm for 7 am", "deactivate my alarm clock", "activate my wake-up alarm"):
            st = agent._jh_new_state()
            agent._jh_note_tool(st, "schedule_followup", json.dumps({"success": True}))
            self.assertEqual(agent._jh_final("Your alarm is set, sir.", st, le.device_command(h, text, {}), "sir"),
                             "Your alarm is set, sir.", text)

    def test_a_false_alarm_system_claim_with_a_time_is_replaced(self):
        h = H.Hass()
        for text, reply in (("set the alarm to away mode at 11 pm", "The alarm is set to away, sir."),
                            ("zet het alarm aan om 23 uur", "Het alarm staat aan, meneer."),
                            ("turn off the alarm in zone 2", "The alarm in zone 2 is off, sir.")):
            self.assertEqual(agent._jh_final(reply, agent._jh_new_state(), le.device_command(h, text, {}), "sir"), NOT_SENT, text)

    def test_other_commands_are_unchanged(self):
        h = H.Hass()
        h.states.set("light.kitchen", "off", friendly_name="Kitchen Light")
        self.assertTrue(le.device_command(h, "turn on the kitchen light", {}))
        self.assertTrue(le.device_command(h, "dim the kitchen light to 30", {}))
        self.assertFalse(le.device_command(h, "tell me a story about an alarm clock", {}))


# ── kit: an install that already has 1-97 gets only the new steps, and a second run changes nothing ─

def _run_source(src, base):
    head = src[:src.index("# 1 ---")]
    head = head.replace('BASE = sys.argv[1] if len(sys.argv) > 1 else "/config/custom_components/jarvis"', "BASE = %r" % base)
    head = head.replace("KIT = os.path.dirname(os.path.abspath(__file__))", "KIT = %r" % H.KIT)
    g = {"__name__": "tests"}
    exec(compile(head + src[src.index("# 1 ---"):src.index("\n# 10 ")], "apply", "exec"), g)
    return g["results"]


class UpgradePath98(unittest.TestCase):
    def test_only_the_new_steps_apply_on_an_install_that_has_1_to_97(self):
        src = open(APPLY, encoding="utf-8").read()
        start = src.index("# 98 ---")
        # Round 5: section 100 is built on 98's text, so an install without 98 has no 100 either. Both go, up to section 99.
        # up to section 101 (independent of 97-100, from the live test) or else 99
        stop = min(i for i in (src.find("\n# 101 ---", start), src.find("\n# 99 ---", start)) if i >= 0) + 1
        later = [s for s in re.findall(r'\],\s*"(\d+[a-z]+) ', src[start:stop]) if not s.startswith("98")]   # later steps there
        without = src[:start] + src[stop:]
        work = tempfile.mkdtemp(prefix="jh-round4py-")
        self.addCleanup(shutil.rmtree, work, True)
        base = H.fresh_upstream(work)
        first = _run_source(without, base)
        self.assertFalse([r for r in first if r.startswith("[FAILED")], first)
        self.assertFalse([r for r in first if r.startswith("[applied] 98")])
        second = _run_source(src, base)
        self.assertFalse([r for r in second if r.startswith("[FAILED")], second)
        applied = [r for r in second if r.startswith("[applied]")]
        self.assertEqual(sorted(r.split()[1] for r in applied), sorted(["98a", "98b"] + later), applied)
        self.assertTrue(later and all(s.startswith("100") for s in later), later)   # only section 100 is built on 98
        self.assertTrue([r for r in second if r.startswith("[already] 97a")] and [r for r in second if r.startswith("[already] 97b")])
        third = _run_source(src, base)
        self.assertFalse([r for r in third if r.startswith(("[applied]", "[FAILED"))], third)
