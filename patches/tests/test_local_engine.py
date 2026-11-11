"""local_engine.py: the instant path picks the right device, keeps questions per conversation and tells the truth."""
import asyncio, types, unittest
from unittest import mock

import jh_harness as H

le = H.load("local_engine")


class Base(unittest.TestCase):
    def setUp(self):
        self.h = H.Hass()
        self.sent = []
        self.ok = True
        self.protected = set()

        async def seam(hass, domain, service, data, *, blocking=True, action=""):
            self.sent.append((domain, service, data.get("entity_id"), data.get("color_name")))
            if self.ok and service == "turn_on" and "color_name" in data and self.colour_lands:
                st = hass.states.get(data["entity_id"])
                st.state = "on"
                st.attributes["rgb_color"] = H._color_name_to_rgb(data["color_name"])
            return self.ok

        self.colour_lands = True
        patches = [mock.patch.object(le, "_seam_execute", seam),
                   mock.patch.object(le, "_needs_confirmation", lambda h, a, e="": e in self.protected),
                   mock.patch.object(le, "_load_learned_aliases", lambda h: {}),
                   mock.patch("asyncio.sleep", mock.AsyncMock())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        le._JH_PENDING.clear()

    def lamp(self, eid, name, state="off", **attrs):
        self.h.states.set(eid, state, friendly_name=name, **attrs)

    def setting(self, eid):
        """A settings entity, as Home Assistant marks it: entity category config (audit 10: no id words decide this)."""
        self.h.registry_entries[eid] = types.SimpleNamespace(entity_id=eid, domain=eid.split(".")[0], area_id=None, device_id=None,
                                                             entity_category="config", labels=set())

    def say(self, text, key="a", force=False):
        return H.run(le.try_local(self.h, text, "sir", force=force, pending_key=key))


class WrongDevice(Base):
    def test_kitchen_one_off_lamp_gets_the_command(self):
        self.lamp("light.ceiling", "Kitchen Ceiling")
        self.lamp("light.strip", "Kitchen Strip", "on")
        r = self.say("turn on kitchen")
        self.assertEqual(self.sent, [("light", "turn_on", "light.ceiling", None)])
        self.assertIn("Kitchen Ceiling", r.text)

    def test_three_heaters_only_the_running_one_is_switched_off(self):
        for room, state in (("bedroom", "off"), ("bathroom", "on"), ("living_room", "off")):
            self.lamp(f"switch.{room}_heater", room.replace("_", " ").title() + " Heater", state)
        r = self.say("switch off heater")
        self.assertEqual(self.sent, [("switch", "turn_off", "switch.bathroom_heater", None)])
        self.assertIn("Bathroom Heater", r.text)

    def test_two_on_asks_which_one(self):
        self.lamp("light.a", "Kitchen Ceiling", "on")
        self.lamp("light.b", "Kitchen Strip", "on")
        r = self.say("turn off kitchen")
        self.assertIn("Which one", r.text)
        self.assertEqual(self.sent, [])


class Pronouns(Base):
    def setUp(self):
        super().setUp()
        self.h.registry_areas = [types.SimpleNamespace(name="Kitchen", id="k")]
        self.h.registry_entries = {"light.ceiling": types.SimpleNamespace(
            entity_id="light.ceiling", domain="light", area_id="k", device_id=None, entity_category=None)}
        self.lamp("light.ceiling", "Ceiling", "on", supported_color_modes=["hs"])
        self.lamp("light.kitchen", "Kitchen", "on", supported_color_modes=["hs"])

    def test_turn_it_off_goes_to_the_ai(self):
        for text in ("turn it off", "turn that off", "turn off it", "turn it red"):
            self.assertIsNone(self.say(text), text)
        self.assertEqual(self.sent, [])

    def test_offline_mode_says_it_cannot_find_it(self):
        r = self.say("turn it off", force=True)
        self.assertFalse(r.success)
        self.assertEqual(self.sent, [])


class Pending(Base):
    def setUp(self):
        super().setUp()
        self.lamp("light.a", "Kitchen Strip", "on")
        self.lamp("light.b", "Kitchen Bed Lamp", "on")

    def test_answer_finishes_the_command(self):
        self.say("turn off kitchen", key="alex")
        r = self.say("the strip", key="alex")
        self.assertEqual(self.sent, [("light", "turn_off", "light.a", None)])
        self.assertTrue(r.success)
        self.assertEqual(le._CTX.last_entity, "light.a")

    def test_other_person_cannot_answer_or_wipe_it(self):
        self.say("turn off kitchen", key="alex")
        self.say("strip", key="robin")
        self.assertEqual(self.sent, [])
        self.say("strip", key="alex")
        self.assertEqual(self.sent, [("light", "turn_off", "light.a", None)])

    def test_part_of_a_word_is_no_answer(self):
        self.say("turn off kitchen", key="alex")
        self.say("it", key="alex")
        self.assertEqual(self.sent, [])

    def test_answer_expires(self):
        self.say("turn off kitchen", key="alex")
        le._JH_PENDING["alex"]["at"] -= 91
        self.say("strip", key="alex")
        self.assertEqual(self.sent, [])

    def test_protected_device_goes_to_the_agent_at_the_question(self):
        self.protected = {"light.a"}
        self.assertIsNone(self.say("turn off kitchen", key="alex"))
        self.assertEqual(self.sent, [])

    def test_protected_device_goes_to_the_agent_at_the_answer(self):
        self.say("turn off kitchen", key="alex")
        self.protected = {"light.a"}
        self.assertIsNone(self.say("strip", key="alex"))
        self.assertEqual(self.sent, [])


class Unavailable(Base):
    def test_all_unavailable_is_not_reported_as_off(self):
        self.lamp("light.one", "Bath One", "unavailable")
        self.lamp("light.two", "Bath Two", "unavailable")
        r = self.say("turn off bath")
        self.assertIn("unavailable", r.text)
        self.assertNotIn("already", r.text)
        self.assertFalse(r.success)
        r = self.say("turn on bath")
        self.assertNotIn("Which one", r.text)
        self.assertEqual(self.sent, [])

    def test_a_live_lamp_still_works_beside_a_dead_one(self):
        self.lamp("light.one", "Bath One", "unavailable")
        self.lamp("light.two", "Bath Two", "off")
        self.say("turn on bath")
        self.assertEqual(self.sent, [("light", "turn_on", "light.two", None)])


class RoomWords(Base):
    """Pull-switch lamps are unavailable as a matter of course, so a room word must not stop at them."""
    def test_room_word_reaches_the_heater_that_is_on(self):
        self.lamp("light.bathroom_light", "Bathroom Light", "unavailable")
        self.lamp("switch.bathroom_heater", "Bathroom Heater", "on")
        r = self.say("turn off the bathroom")
        self.assertEqual(self.sent, [("switch", "turn_off", "switch.bathroom_heater", None)])
        self.assertIn("Bathroom Heater", r.text)

    def test_room_word_with_a_lamp_that_is_off(self):
        self.lamp("light.bedroom_light", "Bedroom Light", "off")
        self.lamp("switch.bedroom_heater", "Bedroom Heater", "on")
        r = self.say("turn off the bedroom")
        self.assertEqual(self.sent, [("switch", "turn_off", "switch.bedroom_heater", None)])
        self.assertTrue(r.success)

    def test_the_full_name_still_wins(self):
        self.lamp("light.bedroom_light", "Bedroom Light", "on")
        self.lamp("switch.bedroom_heater", "Bedroom Heater", "on")
        self.say("turn off the bedroom light")
        self.assertEqual(self.sent, [("light", "turn_off", "light.bedroom_light", None)])

    def test_saying_light_leaves_the_heater_alone(self):
        self.lamp("light.bathroom", "Bathroom", "unavailable")
        self.lamp("switch.bathroom_heater", "Bathroom Heater", "on")
        r = self.say("turn off the bathroom light")
        self.assertEqual(self.sent, [])
        self.assertFalse(r.success)
        self.assertIn("unavailable", r.text)

    def test_dead_exact_room_name_does_not_hide_the_running_heater(self):
        self.lamp("light.bathroom", "Bathroom", "unavailable")
        self.lamp("switch.bathroom_heater", "Bathroom Heater", "on")
        self.say("turn off the bathroom")
        self.assertEqual(self.sent, [("switch", "turn_off", "switch.bathroom_heater", None)])

    def test_an_unavailable_lamp_is_never_reported_off(self):
        self.lamp("light.bathroom_light", "Bathroom Light", "unavailable")
        self.lamp("switch.bathroom_heater", "Bathroom Heater", "on")
        r = self.say("turn off the bathroom light")
        self.assertEqual(self.sent, [])
        self.assertFalse(r.success)
        self.assertEqual(r.text, "Bathroom Light is unavailable, sir. Nothing was changed.")

    def test_a_single_unavailable_lamp_is_reported_honestly(self):
        self.lamp("light.hall", "Hall Light", "unavailable")
        for text in ("turn off the hall light", "turn on the hall light"):
            r = self.say(text)
            self.assertFalse(r.success, text)
            self.assertIn("unavailable", r.text, text)
        self.assertEqual(self.sent, [])

    def test_switching_on_does_not_swap_a_dead_lamp_for_a_heater(self):
        self.lamp("light.bathroom_light", "Bathroom Light", "unavailable")
        self.lamp("switch.bathroom_heater", "Bathroom Heater", "off")
        r = self.say("turn on the bathroom", key="alex")
        self.assertEqual(self.sent, [])
        self.assertIn("Bathroom Light is unavailable", r.text)
        self.assertIn("Bathroom Heater", r.text)
        self.say("heater", key="alex")
        self.assertEqual(self.sent, [("switch", "turn_on", "switch.bathroom_heater", None)])

    def test_an_unknown_state_still_gets_the_command(self):
        self.lamp("light.ir", "Ir Lamp", "unknown")
        self.say("turn on the ir lamp")
        self.assertEqual(self.sent, [("light", "turn_on", "light.ir", None)])


class Vague(Base):
    def test_trailing_filler_still_asks(self):
        self.lamp("light.a", "Kitchen Ceiling")
        self.lamp("light.b", "Kitchen Strip")
        for text in ("turn on kitchen please", "turn on the kitchen lamp", "turn on the kitchen lamp please"):
            le._JH_PENDING.clear()
            self.assertIn("Which one", self.say(text).text, text)
        self.assertEqual(self.sent, [])

    def test_a_full_name_is_not_vague(self):
        self.lamp("light.a", "Kitchen Lamp")
        self.lamp("light.b", "Kitchen Strip")
        self.say("turn on the kitchen lamp")
        self.assertEqual(self.sent, [("light", "turn_on", "light.a", None)])


class Colour(Base):
    def setUp(self):
        super().setUp()
        self.lamp("light.desk", "Desk Lamp", "off", supported_color_modes=["hs"])

    def test_goes_through_the_seam_and_confirms(self):
        r = self.say("set the desk lamp to red")
        self.assertEqual(self.sent, [("light", "turn_on", "light.desk", "red")])
        self.assertEqual(self.h.calls, [])
        self.assertTrue(r.success)

    def test_failure_is_reported(self):
        self.ok = False
        r = self.say("set the desk lamp to red")
        self.assertFalse(r.success)
        self.assertIn("wasn't able", r.text)

    def test_unconfirmed_colour_is_not_claimed(self):
        self.colour_lands = False
        r = self.say("set the desk lamp to red")
        self.assertFalse(r.success)
        self.assertIn("has not confirmed", r.text)

    def test_offline_lamp(self):
        self.lamp("light.desk", "Desk Lamp", "unavailable", supported_color_modes=["hs"])
        r = self.say("set the desk lamp to red")
        self.assertIn("offline", r.text)
        self.assertEqual(self.sent, [])

    def test_lamp_without_colour(self):
        self.lamp("light.desk", "Desk Lamp", "on", supported_color_modes=["brightness"])
        self.assertIn("cannot show", self.say("set the desk lamp to red").text)

    def test_protected_lamp_goes_to_the_agent(self):
        self.protected = {"light.desk"}
        self.assertIsNone(self.say("set the desk lamp to red"))
        self.assertEqual(self.sent, [])


class Security(Base):
    def test_unavailable_lock_is_not_secured(self):
        self.h.states.set("lock.front", "unavailable", friendly_name="Front Lock")
        text = le._home_status(self.h)
        self.assertNotIn("All locks secured", text)
        self.assertIn("Front Lock", text)

    def test_jammed_lock_is_not_secured(self):
        self.h.states.set("lock.front", "jammed", friendly_name="Front Lock")
        self.assertNotIn("All locks secured", le._home_status(self.h))

    def test_locked_lock_is_secured(self):
        self.h.states.set("lock.front", "locked", friendly_name="Front Lock")
        self.assertIn("All locks secured", le._home_status(self.h))

    def test_dead_sensor_blocks_the_all_clear(self):
        self.h.states.set("binary_sensor.door", "unavailable", friendly_name="Hall Door", device_class="door")
        text = le._ctx_query(self.h, "what_open")
        self.assertNotIn("Everything is closed", text)
        self.assertIn("Hall Door", text)

    def test_all_clear_when_everything_reports(self):
        self.h.states.set("binary_sensor.door", "off", friendly_name="Hall Door", device_class="door")
        self.assertIn("Everything is closed", le._ctx_query(self.h, "what_open"))

    def test_open_door_list_names_dead_sensors_too(self):
        self.h.states.set("binary_sensor.a", "on", friendly_name="Hall Door", device_class="door")
        self.h.states.set("binary_sensor.b", "unknown", friendly_name="Back Door", device_class="door")
        text = le._ctx_query(self.h, "what_open")
        self.assertIn("Hall Door", text)
        self.assertIn("Not reporting: Back Door", text)


class Settings(Base):
    def setUp(self):
        super().setUp()
        self.lamp("switch.heater_child_lock", "Heater Child Lock", "on")
        self.lamp("light.tv_backlight", "TV Backlight", "on")
        self.lamp("switch.plug", "Plug", "on")
        self.lamp("light.lamp", "Lamp", "on")
        self.setting("switch.heater_child_lock")
        self.setting("light.tv_backlight")

    def test_everything_off_leaves_settings(self):
        self.say("turn off everything")
        self.assertEqual(sorted(x[2] for x in self.sent), ["light.lamp", "switch.plug"])

    def test_goodnight_leaves_settings(self):
        self.say("good night")
        self.assertEqual([x[2] for x in self.sent], ["light.lamp"])

    def test_all_lights_leaves_settings(self):
        self.say("turn off all lights")
        self.assertEqual([x[2] for x in self.sent], ["light.lamp"])

    def test_stt_retry_skips_settings(self):
        self.lamp("light.kitchen_backlight", "Kitchen", "on")
        self.setting("light.kitchen_backlight")
        with mock.patch.dict(le._STT_CORRECTIONS, {"kichen": "kitchen"}):
            self.assertIsNone(le._find_entity(self.h, "kichen", "light"))


class Heaters(Base):
    def test_label_name_and_settings(self):
        self.lamp("switch.radiator", "Radiator", "on")
        self.lamp("switch.warm_corner", "Warm Corner", "on")
        self.lamp("switch.heater_child_lock", "Heater Child Lock", "on")
        self.lamp("switch.fridge", "Fridge", "on")
        self.h.registry_entries = {"switch.warm_corner": types.SimpleNamespace(
            labels={"heater"}, device_id=None, entity_category=None, domain="switch", area_id=None, entity_id="switch.warm_corner")}
        self.setting("switch.heater_child_lock")
        switches, _ = le._jh_heaters(self.h)
        self.assertEqual(sorted(s.entity_id for s in switches), ["switch.radiator", "switch.warm_corner"])


class WholeNames(Base):
    """A word like fan or door can be what tells two real devices apart."""
    def test_bathroom_fan_reaches_the_fan_with_the_lamp_alive_or_dead(self):
        for lamp in ("on", "off", "unavailable"):
            for action, word, state in (("turn_on", "on", "off"), ("turn_off", "off", "on")):
                self.h.states.d.clear()
                self.sent.clear()
                self.lamp("light.bathroom", "Bathroom", lamp)
                self.lamp("switch.bf", "Bathroom Fan", state)
                self.say(f"turn {word} the bathroom fan")
                self.assertEqual(self.sent, [("switch", action, "switch.bf", None)], lamp)

    def test_other_word_orders_and_a_heater_beside_it(self):
        self.lamp("light.bathroom", "Bathroom", "unavailable")
        self.lamp("switch.bf", "Bathroom Fan")
        self.lamp("switch.bh", "Bathroom Heater")
        for words in ("turn the bathroom fan on", "switch on the bathroom fan please", "turn on bathroom fan"):
            self.sent.clear()
            self.say(words)
            self.assertEqual(self.sent, [("switch", "turn_on", "switch.bf", None)], words)

    def test_a_fan_entity_named_after_the_room(self):
        self.lamp("light.bathroom", "Bathroom")
        self.lamp("fan.bathroom", "Bathroom")
        self.say("turn on the bathroom fan")
        self.assertEqual(self.sent, [("fan", "turn_on", "fan.bathroom", None)])

    def test_door_and_lock_and_cover_tell_devices_apart(self):
        self.lamp("cover.garage", "Garage", "closed")
        self.lamp("cover.garage_door", "Garage Door", "closed")
        self.say("open the garage door")
        self.assertEqual(self.sent[-1][2], "cover.garage_door")
        self.lamp("lock.shed", "Shed Lock", "unlocked")
        self.lamp("light.shed", "Shed")
        self.say("lock the shed lock")
        self.assertEqual(self.sent[-1][2], "lock.shed")
        self.lamp("cover.bed", "Bedroom Cover", "open")
        self.lamp("light.bed", "Bedroom")
        self.say("close the bedroom cover")
        self.assertEqual(self.sent[-1][2], "cover.bed")

    def test_no_fan_means_the_lamp_is_left_alone(self):
        self.lamp("light.bathroom", "Bathroom")
        self.assertIsNone(self.say("turn on the bathroom fan"))
        self.assertEqual(self.sent, [])

    def test_the_lamp_word_still_goes(self):
        self.lamp("light.bedroom", "Bedroom")
        self.say("turn on the bedroom lamp")
        self.assertEqual(self.sent, [("light", "turn_on", "light.bedroom", None)])
        self.sent.clear()
        self.lamp("lock.front", "Front Door", "unlocked")
        self.say("lock the front door")
        self.assertEqual(self.sent[0][2], "lock.front")


class YesAnswers(Base):
    def ask_first(self):
        self.lamp("light.o", "Office", "unavailable")
        self.lamp("switch.oh", "Office Heater")
        self.assertIn("Do you mean Office Heater?", self.say("turn on the office").text)

    def test_yes_finishes_the_command_locally(self):
        for word in ("yes", "yes please", "Yes.", "ok", "ja", "sure"):
            self.sent.clear()
            le._JH_PENDING.clear()
            self.ask_first()
            r = self.say(word)
            self.assertEqual(self.sent, [("switch", "turn_on", "switch.oh", None)], word)
            self.assertTrue(r.success, word)

    def test_a_no_sends_nothing(self):
        self.ask_first()
        self.assertIsNone(self.say("no"))
        self.assertEqual(self.sent, [])

    def test_yes_to_a_which_one_question_does_not_guess(self):
        self.lamp("switch.a", "Office Heater")
        self.lamp("switch.b", "Office Fan")
        self.lamp("light.c", "Office Ceiling")
        self.assertIn("Which one", self.say("turn on the office").text)
        self.say("yes")
        self.assertEqual(self.sent, [])

    def test_a_late_yes_does_nothing(self):
        self.ask_first()
        le._JH_PENDING["a"]["at"] -= 100
        self.say("yes")
        self.assertEqual(self.sent, [])

    def test_a_protected_device_still_goes_to_the_gate(self):
        self.protected.add("switch.oh")
        self.ask_first()
        self.assertIsNone(self.say("yes"))
        self.assertEqual(self.sent, [])


class Command(Base):
    """What counts as a device command for the truth check."""
    def cmd(self, text):
        return le.device_command(self.h, text, {})

    def test_questions_are_never_commands(self):
        self.lamp("light.lamp", "Lamp")
        for text in ("is the lamp on?", "is the lamp on", "how do i turn on the lamp", "why is the door open", "should i lock the front door?",
                     "staat de lamp aan", "wat doet de lamp"):
            self.assertFalse(self.cmd(text), text)
            self.assertTrue(le.is_question(text), text)

    def test_a_polite_request_with_a_question_mark_is_a_command(self):
        self.assertTrue(self.cmd("could you turn off the lamp?"))
        self.assertTrue(self.cmd("hey jarvis, please switch on the tv"))
        self.assertTrue(self.cmd("kun je de lamp aan zetten?"))

    def test_a_sensor_name_is_no_target(self):
        self.lamp("sensor.conversation_count", "Conversation Count", "1")
        self.assertFalse(self.cmd("close the conversation"))

    def test_a_room_name_counts_and_follows_the_house(self):
        self.assertFalse(self.cmd("zet zolder uit"))
        self.h.registry_areas = [types.SimpleNamespace(id="z", name="Zolder", aliases=[])]
        self.assertTrue(self.cmd("zet zolder uit"))

    def test_a_setting_entity_is_no_device(self):
        self.lamp("switch.tv_backlight", "Glow")
        self.setting("switch.tv_backlight")
        self.assertFalse(self.cmd("turn glow off"))

    def test_only_the_opening_word_makes_an_order(self):
        self.lamp("light.lamp", "Lamp")
        self.lamp("switch.heater", "Heater")
        self.h.registry_areas = [types.SimpleNamespace(id="hall", name="Hall", aliases=[])]
        for text in ("the lamp in the hall is off again", "my heater went off", "the lamp is on", "lamp off", "heater off", "hall off",
                     "jarvis the lamp is off", "please, my heater went off", "sir the heater broke down", "can you believe the lamp went off",
                     "could you imagine the heater off"):
            self.assertFalse(self.cmd(text), text)
        for text in ("turn off the lamp", "please turn off the lamp", "jarvis, turn off the lamp", "hey jarvis please turn the heater off", "could you dim the lamp?",
                     "put the lamp on", "make the lamp brighter", "kun je de lamp aanzetten", "shut the blinds", "doe de lamp uit"):
            self.assertTrue(self.cmd(text), text)

    def test_scenes_count_only_when_they_exist(self):
        self.assertFalse(self.cmd("start a story"))
        self.lamp("scene.story", "Story Time")
        self.assertTrue(self.cmd("start story time"))

    def test_a_pronoun_with_on_or_off_is_a_command(self):
        self.assertTrue(self.cmd("turn it off"))

    def test_everyday_wordings(self):
        for text in ("turn up the heat", "turn up the volume", "turn off the lamp in 10 minutes", "play music", "stop the music"):
            self.assertTrue(self.cmd(text), text)
        # audit 10: a wake-up alarm is set with a reminder or timer, so it is no device command (see test_audit10.AlarmWord)
        self.assertFalse(self.cmd("set the alarm for 7"))

    def test_a_request_to_report_is_a_question(self):
        self.assertTrue(le.is_question("tell me if the door is locked"))
        self.assertFalse(self.cmd("tell me if the door is locked"))

    def test_nonsense_is_safe(self):
        for text in ("", "   ", "?", "please", None):
            self.assertFalse(self.cmd(text), text)


class Punctuation(Base):
    """Speech-to-text writes pauses as punctuation. It must never change what a command does, typed or spoken."""
    def setUp(self):
        super().setUp()
        self.lamp("light.stick", "Stick", "off", supported_color_modes=["rgb"])

    def test_the_sentence_from_the_live_system_runs_on_the_local_path(self):
        r = self.say("Switch, stick, green.")
        self.assertEqual(self.sent, [("light", "turn_on", "light.stick", "green")])
        self.assertEqual(r.text, "Done, sir. Stick is green.")
        self.assertTrue(r.success)

    def test_every_way_of_writing_it_does_the_same(self):
        for text in ("switch stick green", "Switch stick, green!", "Switch; stick: green?", "Switch - stick - green...", "“Switch, stick, green.”",
                     "Jarvis, switch stick green, please.", "Switch, stick, green, please."):
            self.sent.clear()
            le._JH_PENDING.clear()
            self.say(text)
            self.assertEqual(self.sent, [("light", "turn_on", "light.stick", "green")], text)

    def test_on_and_off_commands_with_pauses(self):
        for text in ("Turn, on, the stick.", "Switch on, the stick!", "Turn the stick on."):
            self.sent.clear()
            self.say(text)
            self.assertEqual(self.sent, [("light", "turn_on", "light.stick", None)], text)

    def test_the_truth_check_sees_the_same_command(self):
        cmd = lambda t: le.device_command(self.h, t, {})
        for text in ("Switch, stick, green.", "switch stick green", "Switch stick green?", "Set, the stick, to red!", "Turn, off, the stick."):
            self.assertTrue(cmd(text), text)
        self.assertFalse(cmd("Is, the stick, on?"))
        self.assertFalse(cmd("Is the stick on."))
        self.assertTrue(le.is_question("is the stick on?"))

    def test_numbers_and_times_keep_their_marks(self):
        for said, kept in (("Set the heater to 21.5.", "set the heater to 21.5"), ("Set it to 1,5, please.", "set it to 1,5 please"),
                           ("Wake me at 7:30.", "wake me at 7:30"), ("Dim the lamp to 50%, please.", "dim the lamp to 50% please"),
                           ("Set it to -5.", "set it to -5"), ("At 7 a.m., sir.", "at 7 am sir")):
            self.assertEqual(le._normalize(said), kept, said)

    def test_words_keep_their_apostrophes_and_hyphens(self):
        self.assertEqual(le._normalize("What’s the Wi-Fi, sir?"), "what's the wi-fi sir")
        self.assertEqual(le._normalize("Don't, turn it 'off'."), "don't turn it off")

    def test_a_value_survives_the_pauses(self):
        self.say("Dim, the stick, to 40.")
        self.assertEqual([x[:3] for x in self.sent], [("light", "turn_on", "light.stick")])

    def test_a_name_with_a_dot_in_it_is_still_found_and_shown_as_it_is(self):
        self.lamp("switch.coffee", "Mr. Coffee")
        r = self.say("Turn on Mr. Coffee.")
        self.assertEqual(self.sent, [("switch", "turn_on", "switch.coffee", None)])
        self.assertIn("Mr. Coffee", r.text)


class SoundAlike(Base):
    """A name said by ear, only after exact and normal matching found nothing."""
    def find(self, name, hint=None):
        return le._find_entity(self.h, name, hint)

    def test_disk_finds_disc_and_the_name_stays_as_it_is(self):
        self.lamp("light.disc", "Disc")
        r = self.say("turn on the disk")
        self.assertEqual(self.sent, [("light", "turn_on", "light.disc", None)])
        self.assertIn("Disc", r.text)
        self.assertNotIn("disk", r.text.lower())

    def test_the_rules(self):
        for said, name in (("disk", "Disc"), ("fone", "Phone"), ("rosy", "Rozy"), ("hal", "Hall"), ("stik", "Stick"), ("qube", "Cube"), ("kofee", "Coffee")):
            self.assertEqual(le._jh_sound(said), le._jh_sound(name), said)
            self.h = H.Hass()
            self.lamp("light.x", name)
            self.assertEqual(self.find(said), ("light.x", name), said)

    def test_it_follows_the_house(self):
        self.lamp("light.a", "Disc")
        self.assertEqual(self.find("disk"), ("light.a", "Disc"))
        self.h.states.set("light.a", "off", friendly_name="Shelf")
        self.assertIsNone(self.find("disk"))

    def test_unrelated_names_do_not_match(self):
        for name in ("Dusk", "Desk", "Lamp 1", "Bedroom", "Dish", "Sun"):
            self.lamp("light." + name.lower().replace(" ", "_"), name)
        for said in ("disk", "lamp 11", "bed", "son", "dis"):
            self.assertEqual(le._jh_sound_hits(self.h, said, ["light"]), [], said)
        self.assertEqual(le._jh_sound("Lamp 11"), "lamp 11")

    def test_same_words_in_another_order_beat_longer_names(self):
        self.lamp("switch.bedroom_heater", "Bedroom Heater")
        for n in ("Bedroom Heater Power", "Bedroom Heater cost today", "Bedroom Heater Total energy"):
            self.h.states.set("sensor." + n.lower().replace(" ", "_"), "1", friendly_name=n)
        hits = le._jh_sound_hits(self.h, "heater bedroom", ["switch", "sensor"])
        self.assertEqual(hits, [("switch.bedroom_heater", "Bedroom Heater")])

    def test_a_switching_command_never_asks_about_sensors(self):
        self.lamp("switch.bedroom_heater_1", "Bedroom Heater One")
        self.lamp("switch.bedroom_heater_2", "Bedroom Heater Two")
        self.h.states.set("sensor.bedroom_heater_power", "1", friendly_name="Bedroom Heater Power")
        many = le._jh_sound_many(self.h, "heater bedroom", None, list(le._JH_SOUND_CONTROL))
        self.assertEqual([x[1] for x in many], ["Bedroom Heater One", "Bedroom Heater Two"])
        self.assertEqual(len(le._jh_sound_many(self.h, "heater bedroom", None)), 3)   # a question may still name a sensor

    def test_two_letters_are_too_little(self):
        self.lamp("light.ac", "Ac")
        self.assertEqual(le._jh_sound_hits(self.h, "ak", ["light"]), [])

    def test_exact_and_normal_matches_come_first(self):
        self.lamp("light.disc", "Disc")
        self.lamp("light.disk", "Disk")
        self.assertEqual(self.find("disk"), ("light.disk", "Disk"))
        self.say("turn on the disc")
        self.assertEqual(self.sent, [("light", "turn_on", "light.disc", None)])
        self.h = H.Hass()
        self.lamp("light.disc", "Disc")
        self.lamp("light.player", "Disk Player")
        self.assertEqual(self.find("disk"), ("light.player", "Disk Player"))

    def test_two_sound_alikes_ask_which_one_and_the_answer_finishes_it(self):
        self.lamp("light.left", "Disc Left")
        self.lamp("light.right", "Disc Right")
        self.assertIsNone(self.find("disk"))
        r = self.say("turn on the disk")
        self.assertEqual(self.sent, [])
        self.assertEqual(r.text, "Which one, sir: Disc Left or Disc Right?")
        self.say("right")
        self.assertEqual(self.sent, [("light", "turn_on", "light.right", None)])

    def test_two_kinds_of_device_also_ask(self):
        self.lamp("light.disc", "Disc")
        self.lamp("switch.disq", "Disq")
        r = self.say("turn on the disk")
        self.assertIn("Which one", r.text)
        self.assertEqual(self.sent, [])

    def test_a_colour_for_two_sound_alikes_is_asked_about_and_kept(self):
        for eid, name in (("light.a", "Disc Left"), ("light.b", "Disc Right")):
            self.lamp(eid, name, "off", supported_color_modes=["rgb"])
        r = self.say("Switch, disk, green.")
        self.assertIn("Which one", r.text)
        self.assertEqual(self.sent, [])
        self.assertIn("Disc Left is green", self.say("left").text)
        self.assertEqual(self.sent, [("light", "turn_on", "light.a", "green")])

    def test_a_value_or_a_protected_device_goes_to_the_agent_not_a_guess(self):
        self.lamp("light.left", "Disc Left")
        self.lamp("light.right", "Disc Right")
        self.assertIsNone(self.say("dim the disk to 40"))
        self.protected.add("light.left")
        self.assertIsNone(self.say("turn on the disk"))
        self.assertEqual(self.sent, [])

    def test_a_setting_entity_never_sounds_like_anything(self):
        self.lamp("switch.tv_backlight", "Disc")
        self.setting("switch.tv_backlight")
        self.assertIsNone(self.find("disk"))

    def test_the_truth_check_hears_it_too(self):
        self.lamp("light.left", "Disc Left")
        self.assertTrue(le.device_command(self.h, "switch on the disk", {}))
        self.lamp("light.right", "Disc Right")
        self.assertTrue(le.device_command(self.h, "switch on the disk", {}))
        self.assertFalse(le.device_command(self.h, "the disk is broken", {}))

    def test_scenes_and_rooms(self):
        self.lamp("scene.focus", "Focus")
        self.assertEqual(le._find_scene_or_script(self.h, "fokus"), ("scene.focus", "Focus", "scene"))
        self.lamp("scene.fokuss", "Fokuss")
        self.assertIsNone(le._find_scene_or_script(self.h, "focuz"))
        self.h.registry_areas = [types.SimpleNamespace(name="Attic", id="a")]
        self.h.registry_entries = {"light.up": types.SimpleNamespace(entity_id="light.up", domain="light", area_id="a", device_id=None, entity_category=None)}
        self.lamp("light.up", "Up")
        self.assertEqual(le._find_entities_in_area(self.h, "atik", "light"), [("light.up", "Up")])
        self.assertEqual(le._find_entities_in_area(self.h, "dusk", "light"), [])


if __name__ == "__main__":
    unittest.main()
