"""Audit 10 round 5 (2026-11-08): the review-0 findings on section 98, fixed in section 100 of apply.py.
Offline: a fake hass and temp folders, no Home Assistant and no network into the home."""
import json, os, re, shutil, tempfile, time, unittest

import jh_harness as H
import test_audit10_round3 as r3          # module imports only: their test classes are not collected twice here
import test_audit10_round4_python as r4

le = H.load("local_engine")
agent = H.load("agent")

APPLY = os.path.join(H.KIT, "apply.py")
FAILED_LINE = "That did not go through, sir. Nothing was changed."


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


def worked_tool():
    """A device command whose control_device call worked and was confirmed."""
    st = agent._jh_new_state()
    h = H.Hass()
    h.states.set("switch.heater", "on", friendly_name="Heater")
    agent._jh_note_tool(st, "control_device", json.dumps({"success": True, "entity_id": "switch.heater", "new_state": "on",
                                                          "confirmed": True}), {"entity_id": "switch.heater", "action": "turn_on"}, h)
    return st


# ── 1: a condition lets off only its own subject's state, not "once again", and only when no tool failed ──

class ConditionRule(unittest.TestCase):
    # the reviewer's four replies (probe_rv4a2.py): after a failed control_device, replaced and sent back once, as in round 3
    REVIEW = ("Once again the heater is on, sir, shall I set it to 21?",
              "Once more the lights are on, sir, shall I dim them?",
              "So when you asked the heater is now on, sir, shall I set it to 21?",
              "As soon as you asked the heater is on, sir, shall I set it to 21?")
    # a main clause with no comma after the condition still claims
    NO_COMMA = ("If you like the heater is on, sir, shall I set it to 21?",
                "If you asked it is on, sir, shall I set it to 21?",
                "If so the heater is on, sir, shall I set it to 21?",
                "When the heater is on the lamp is on too, sir, shall I dim it?",
                "Once again, the heater is on, sir, shall I set it to 21?",
                "When you asked, the heater is on, sir, shall I set it to 21?")
    # real conditions: they stand while no tool failed
    CONDITIONS = ("When it is on, sir, shall I set it to 21?",
                  "If the front door is open, sir, shall I close it?",
                  "Once the heater is on, sir, shall I set it to 21?",
                  "When the living room lights are on, sir, shall I dim them?",
                  "If Living Room Heater is on, sir, shall I set it to 21?",
                  "If the heater is on and the window is open, sir, shall I close the window?",
                  "And if they are off, sir, shall I turn them on?",
                  "When TV is on, sir, shall I lower the volume?")

    def test_the_review_replies_after_a_failed_tool_are_replaced_and_sent_back(self):
        for text in self.REVIEW:
            self.assertEqual(problem(text, failed_tool()), "failed", text)
            self.assertEqual(agent._jh_final(text, failed_tool(), True, "sir"), FAILED_LINE, text)
            self.assertTrue(agent._jh_nudge(text, failed_tool(), True), text)

    def test_the_review_replies_with_no_tool_are_claims(self):
        for text in self.REVIEW + self.NO_COMMA:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)

    def test_conditions_stand_when_no_tool_failed(self):
        for text in self.CONDITIONS:
            self.assertIsNone(problem(text), text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), text)
            self.assertEqual(agent._jh_final(text, worked_tool(), True, "sir"), text)
            self.assertIsNone(agent._jh_nudge(text, agent._jh_new_state(), True), text)

    def test_conditions_after_a_failed_tool_say_it_failed(self):
        for text in self.CONDITIONS:
            self.assertEqual(problem(text, failed_tool()), "failed", text)
            self.assertEqual(agent._jh_final(text, failed_tool(), True, "sir"), FAILED_LINE, text)
            self.assertTrue(agent._jh_nudge(text, failed_tool(), True), text)

    def test_other_questions_after_a_failed_tool_are_unchanged(self):
        # only the condition depends on the failure; a plain question and choices still stand after one
        for text in ("Which heater do you mean, sir?", "Set to which temperature, sir, 20 or 22?",
                     "Het is aan u, meneer, welke lamp wilt u aan?"):
            self.assertIsNone(problem(text, failed_tool()), text)


# ── 2: "the house / home / burglar / intruder / security alarm" is the alarm system, also with a time ──

class AlarmSystemWords(unittest.TestCase):
    SYSTEM = ("activate the burglar alarm at 11 pm", "activate the house alarm at 11 pm", "deactivate the home alarm at 7",
              "activate the intruder alarm in 10 minutes", "activate the security alarm at 11 pm", "arm the house alarm at 11 pm",
              "disarm the burglar alarm at 7", "activate the house alarm", "turn off the burglar alarm")
    # the round 3 and 4 wake-up phrasings, the owner's 'turn on/off the alarm at <time>' among them (a wake-up alarm, as in
    # rounds 1-4), and the reviewer's wake-up phrasings that were no command
    WAKE = r3.WakeUpAlarmPhrasings.WAKE + r4.AlarmWords.WAKE + (
        "turn off the alarm at 7", "turn on the alarm at 11", "turn off my alarm in 10 minutes", "zet het alarm om 7 uur",
        "set an alarm for my night shift at 6", "cancel my alarm for tomorrow", "set my alarm for six thirty", "wake me up at 7",
        "remind me at 7 to turn off the alarm", "put the alarm on snooze for 10 minutes")

    def homes(self):
        bare = H.Hass()
        panel = H.Hass()
        panel.states.set("alarm_control_panel.house", "disarmed", friendly_name="House Alarm")
        return (("no panel", bare), ("panel", panel))

    def test_the_alarm_system_by_its_kind_is_a_device_command_with_or_without_a_panel(self):
        for where, h in self.homes():
            for text in self.SYSTEM:
                self.assertTrue(le.device_command(h, text, {}), (where, text))

    def test_a_false_alarm_claim_with_no_tool_is_replaced(self):
        for where, h in self.homes():
            for text, reply in (("activate the burglar alarm at 11 pm", "The burglar alarm is activated, sir."),
                                ("activate the house alarm at 11 pm", "The house alarm is activated, sir."),
                                ("deactivate the home alarm at 7", "The home alarm is deactivated, sir."),
                                ("activate the intruder alarm in 10 minutes", "The intruder alarm is on, sir.")):
                cmd = le.device_command(h, text, {})
                self.assertEqual(agent._jh_final(reply, agent._jh_new_state(), cmd, "sir"), FAILED_LINE, (where, text))

    def test_wake_up_alarms_stay_no_device_command(self):
        for where, h in self.homes():
            for text in self.WAKE:
                self.assertFalse(le.device_command(h, text, {}), (where, text))

    def test_the_round_4_alarm_system_orders_stay_commands(self):
        for where, h in self.homes():
            for text in r4.AlarmWords.SYSTEM + r3.WakeUpAlarmPhrasings.SYSTEM:
                self.assertTrue(le.device_command(h, text, {}), (where, text))


# ── 3: the choices test is linear; long runs of quotes, dots or hyphens take no time ──────────────────

class ChoicesTestIsLinear(unittest.TestCase):
    @staticmethod
    def replies(n):
        return ("Set to which temperature, sir, " + "'" * n + " 20?",        # the reviewer's perf_rv4a3.py
                "Set to which temperature, sir," + "-" * n + " 20?",
                "Set to which temperature, sir, " + "." * n + "20 or 22?",
                "Set, " + "'" * n + " switching?",                            # the reviewer's perf_rv4a.py
                "Which way, sir, " + "-" * n + " 20?",                        # the reviewer's perf_rv4a2.py
                "Set to which temperature, sir, " + "." * n + " a b c d e, 20 or 22?")   # the list items before choices

    @staticmethod
    def best(fn):
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t0)
        return min(times)

    def test_the_worst_cases_finish_well_under_a_tenth_of_a_second(self):
        # growing sizes, so a build that backtracks fails at a small size instead of hanging the suite
        for n in (300, 3000, 30000):
            for text in self.replies(n):
                for cmd in (True, False):
                    st = failed_tool()
                    for fn in (lambda: agent._jh_final(text, agent._jh_new_state(), cmd, "sir"),
                               lambda: agent._jh_problem(text, st, cmd)):
                        dt = self.best(fn)
                        self.assertLess(dt, 0.1, (n, text[:34], cmd, round(dt, 3)))

    def test_the_regexes_alone_are_linear(self):
        for n in (300, 3000, 30000):
            for ch in ("'", "-", ".", "’"):
                for s in (ch * n, ch * n + " 20", ch * n + " a b c d e", "x" + ch * n + " or", ch * n + "20 or 22" + ch * n + " x"):
                    for rx in (agent._JH_CHOICES_RE, agent._JH_ITEM_RE):
                        dt = self.best(lambda: rx.fullmatch(s))
                        self.assertLess(dt, 0.05, (n, repr(ch), rx.pattern[:20], round(dt, 3)))

    def test_choices_still_count(self):
        for text in ("Set to which temperature, sir, 20 or 22?", "Set to which temperature, sir, 20, 21 or 22?",
                     "Set to which position, sir, open or closed?", "Op welke stand, meneer, 20 of 22?",
                     "Set to which temperature, sir, 20°C or 22°C?", "Which one, sir, -5% or 10%?"):
            self.assertIsNone(problem(text), text)
        for text in ("I turned on the heater, sir, 20 or 22?", "The heater is on, sir, 20 or 22?",
                     "Set to which temperature, sir, the heater is on, 20 or 22?"):
            self.assertEqual(problem(text), "empty", text)


# ── 4: with, than, me and "I start" let an -ing word off; "setting" is a noun unless it acts ─────────────

class IngDenyList(unittest.TestCase):
    # the reviewer's eleven honest questions (probe_rv4a.py): they stood in round 3 and stand again
    HONEST = ("I'd be happy to help with switching on the heater, sir, which one do you mean?",
              "Happy to help with turning on the lights, sir, which room do you mean?",
              "Rather than switching on the heater, sir, shall I turn on the lamp?",
              "The thermostat setting is 20, sir, shall I raise it to 22?",
              "The heater's setting is 20, sir, shall I raise it to 22?",
              "The temperature setting is 20, sir, shall I raise it to 22?",
              "That is the highest setting, sir, shall I leave it there?",
              "It is at its lowest setting, sir, shall I raise it?",
              "Before I start changing anything, sir, which lamp do you mean?",
              "Instead of me switching it on, sir, shall I set a timer?")
    # "Before going ahead with turning it on, ...?" and "Setting is 20, ...?" are asked back once since the round 5 follow-up:
    # "going ahead with" and a clipped "Setting" at the start of a clause claim (see FollowUp5b), the safe side
    MORE_HONEST = ("Before we start switching things, sir, which room do you mean?",
                   "Until I start changing it, sir, which heater do you mean?",
                   "That setting, sir, shall I keep it?")
    # claims of rounds 3 and 4 (the five of round-4 item 1 among them) and "setting" used as a verb
    CLAIMS = r3.IngWordWhereItClaims.CLAIMS + r4.IngClaimsByDenyList.CLAIMS + r4.IngClaimsByDenyList.ATTEMPT + (
        "I am setting the heater to 21, sir, shall I dim the lights too?",
        "I'm setting the heater to 21, sir, shall I dim the lights too?",
        "I’m setting it to 21, sir, shall I dim the lights too?",
        "Setting the heater to 21 now, sir, shall I dim the lights too?",
        "Setting it to 21, sir, shall I dim the lights too?",
        "Now setting the thermostat to 21, sir, shall I dim the lights?",
        "Okay sir setting the heater to 21, shall I dim the lights?",
        "Setting Living Room Heater to 21, sir, shall I dim the lights?",
        "The heater is setting itself to 21, sir, shall I dim the lights?",
        "I'll be setting the heater to 21, sir, shall I dim the lights?",
        "We are setting up the scene, sir, shall I dim the lights?",
        "Sure, setting your heater to 21, shall I dim the lights?",
        "Setting now, sir, shall I also dim the lights?",
        "I'll start switching on the heater, sir, shall I set it to 21?",
        "We start turning things on, sir, shall I set it to 21?",
        "I begin switching it on, sir, shall I set it to 21?",
        "Very well, sir, I have begun turning on the heater, shall I set it to 21?")

    def test_the_honest_questions_stand(self):
        for text in self.HONEST + self.MORE_HONEST + r4.IngClaimsByDenyList.HONEST + r3.IngWordWhereItClaims.HONEST:
            self.assertIsNone(problem(text), text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), text)
            self.assertIsNone(agent._jh_nudge(text, agent._jh_new_state(), True), text)

    def test_the_claims_stay_claims(self):
        for text in self.CLAIMS:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(agent._jh_final(text, agent._jh_new_state(), True, "sir"), FAILED_LINE, text)
            st = failed_tool()
            self.assertEqual(problem(text, st), "failed", text)
            self.assertNotEqual(agent._jh_final(text, st, True, "sir"), text, text)


# ── kit: an install that already has 1-98 gets only section 100, and a second run changes nothing ────

class FollowUp5b(unittest.TestCase):
    """The review findings on section 100 (round 5 follow-up, fixed in 100a, 100b and 100d)."""
    # "with" and "than" let an -ing word off only after help / assist and rather: these still claim (probe_rv5a.py section A)
    GOING_AHEAD = ("Going ahead with switching on the heater, sir, shall I set it to 21?",
                   "I'm going ahead with turning on the lights, sir, shall I dim them?",
                   "Proceeding with switching on the heater now, sir, shall I set it to 21?",
                   "I'll proceed with turning off the lights, sir, shall I lock the door too?",
                   "Carrying on with switching it on, sir, shall I set it to 21?",
                   "Pressing on with turning the heater on, sir, shall I set it to 21?",
                   "No sooner said than switching it on, sir, shall I set it to 21?",
                   "Before going ahead with turning it on, sir, which heater do you mean?")   # asked back once, the safe side
    # a clipped "setting" at the start of a clause, or with no noun ending after it, is a verb (section B)
    CLIPPED = ("Setting heater at 21, sir, shall I dim the lights?",
               "Okay sir, setting heater at 21 degrees, shall I dim the lights?",
               "Setting 21 degrees, sir, shall I dim the lights?",
               "Setting thermostat for 21, sir, shall I dim the lights?",
               "Sure, setting thermostat at 21, shall I dim the lights?",
               "Setting things up, sir, shall I dim the lights?",
               "Setting scene Movie, sir, shall I dim the lights?",
               "Setting mood lighting, sir, shall I dim the lights?",
               "Okay setting living room heater at 21, sir, shall I dim the lights?",
               "Setting is 20, sir, shall I change it?")   # asked back once, the safe side

    def test_going_ahead_with_and_clipped_setting_claim_also_after_a_failed_tool(self):
        for text in self.GOING_AHEAD + self.CLIPPED:
            self.assertEqual(problem(text), "empty", text)
            self.assertEqual(problem(text, failed_tool()), "failed", text)
            self.assertEqual(agent._jh_final(text, failed_tool(), True, "sir"), FAILED_LINE, text)
            self.assertTrue(agent._jh_nudge(text, failed_tool(), True), text)

    @staticmethod
    def sent_not_confirmed(moving):
        h = H.Hass()
        st = agent._jh_new_state()
        if moving:
            h.states.set("cover.garage", "opening", friendly_name="Garage Door")
            agent._jh_note_tool(st, "control_device", json.dumps({"success": True, "sent": True, "confirmed": False, "in_progress": True,
                                                                  "entity_id": "cover.garage", "new_state": "opening"}),
                                {"entity_id": "cover.garage", "action": "open"}, h)
        else:
            h.states.set("light.hall", "on", friendly_name="Hall Light")
            agent._jh_note_tool(st, "control_device", json.dumps({"success": True, "sent": True, "confirmed": False,
                                                                  "entity_id": "light.hall", "new_state": "on"}),
                                {"entity_id": "light.hall", "action": "set_brightness"}, h)
        return st

    def test_a_condition_stands_while_a_device_is_still_moving_or_unconfirmed(self):
        # only a failed tool refuses the condition (probe_rv5a_moving.py); a door that is opening is no failure
        for moving in (True, False):
            for text in ("When the garage door is open, sir, shall I turn on the driveway light?",
                         "Once it is open, sir, shall I turn on the light?",
                         "If the hall light is on, sir, shall I dim it further?"):
                self.assertIsNone(problem(text, self.sent_not_confirmed(moving)), (moving, text))
                self.assertEqual(agent._jh_final(text, self.sent_not_confirmed(moving), True, "sir"), text, (moving, text))

    def test_a_google_or_nest_home_alarm_is_a_wake_up_alarm(self):
        for h in (H.Hass(), AlarmSystemWords().homes()[1][1]):
            for text in ("set a google home alarm for 7 am", "set a Google Home alarm for 6:30 tomorrow",
                         "turn off the google home alarm at 7", "set a nest home alarm for 7"):
                self.assertFalse(le.device_command(h, text, {}), text)
            for text in ("activate the home alarm at 11 pm", "deactivate the house alarm at 7", "set my home alarm to away"):
                self.assertTrue(le.device_command(h, text, {}), text)


class TemperatureOfAHeater(unittest.TestCase):
    """Section 101 (live test of 1.0.0): a heater's state is never read out as a temperature ("off°")."""

    def say(self, entity_id, state, **attrs):
        h = H.Hass()
        h.states.set(entity_id, state, **attrs)
        return le._query_resp(h, "query_temp", entity_id, attrs.get("friendly_name", entity_id), "sir")

    def test_a_heater_that_reports_no_room_temperature_says_so(self):
        self.assertEqual(self.say("climate.living_room_heater", "off", friendly_name="Living Room Heater"),
                         "Living Room Heater does not report a temperature at the moment, sir.")

    def test_a_heater_with_a_room_temperature_reads_that(self):
        self.assertEqual(self.say("climate.living_room_heater", "heat", friendly_name="Living Room Heater", current_temperature=19.5),
                         "The temperature in Living Room Heater is currently 19.5°, sir.")

    def test_a_sensor_reads_its_value_and_unit_and_an_unavailable_one_none(self):
        self.assertEqual(self.say("sensor.temperature", "6.0", friendly_name="Temperature", unit_of_measurement="°C"),
                         "The temperature in Temperature is currently 6.0°C, sir.")
        self.assertEqual(self.say("sensor.temperature", "unavailable", friendly_name="Temperature", unit_of_measurement="°C"),
                         "Temperature does not report a temperature at the moment, sir.")


class UpgradePath100(unittest.TestCase):
    def test_only_the_new_steps_apply_on_an_install_that_has_1_to_98(self):
        src = open(APPLY, encoding="utf-8").read()
        start = src.index("# 100 ---")
        # up to section 101 (independent of 97-100, from the live test) or else 99
        stop = min(i for i in (src.find("\n# 101 ---", start), src.find("\n# 99 ---", start)) if i >= 0) + 1
        without = src[:start] + src[stop:]
        work = tempfile.mkdtemp(prefix="jh-round5py-")
        self.addCleanup(shutil.rmtree, work, True)
        base = H.fresh_upstream(work)
        first = r4._run_source(without, base)
        self.assertFalse([r for r in first if r.startswith("[FAILED")], first)
        self.assertFalse([r for r in first if r.startswith("[applied] 100")])
        second = r4._run_source(src, base)
        self.assertFalse([r for r in second if r.startswith("[FAILED")], second)
        applied = [r for r in second if r.startswith("[applied]")]
        self.assertEqual(sorted(r.split()[1] for r in applied), ["100a", "100b", "100c", "100d"], applied)
        for step in ("98a", "98b", "97a", "97b", "85b"):
            self.assertTrue([r for r in second if r.startswith("[already] %s " % step)], step)
        third = r4._run_source(src, base)
        self.assertFalse([r for r in third if r.startswith(("[applied]", "[FAILED"))], third)
