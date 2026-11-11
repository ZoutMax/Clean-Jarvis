"""briefing.py and proactive_briefing.py: spoken text holds real facts only and honours the options."""
import types, unittest
from unittest import mock

import jh_harness as H

briefing = H.load("briefing")
proactive = H.load("proactive_briefing")


class Facts(unittest.TestCase):
    def setUp(self):
        self.h = H.Hass()

    def test_other_homes_heater_names(self):
        self.h.states.set("switch.radiator", "on", friendly_name="Radiator")
        self.assertIn("Heaters on: Radiator.", briefing._facts_home(self.h))

    def test_climate_heating_in_auto(self):
        self.h.states.set("climate.hall", "auto", friendly_name="Hall", hvac_action="heating")
        self.assertIn("Heaters on: Hall.", briefing._facts_home(self.h))

    def test_climate_idle_is_not_on(self):
        self.h.states.set("climate.hall", "heat", friendly_name="Hall", hvac_action="idle")
        self.assertIn("No heaters are on.", briefing._facts_home(self.h))

    def test_climate_without_action_keeps_the_honest_wording(self):
        self.h.states.set("climate.sun", "heat", friendly_name="Sun")
        self.assertIn("Heaters on: Sun (powered on, heat level unknown).", briefing._facts_home(self.h))

    def test_no_heater_data_says_nothing(self):
        self.assertEqual(briefing._facts_home(self.h), [])

    def test_dead_heater_is_not_reported_as_off(self):
        self.h.states.set("switch.heater", "unavailable", friendly_name="Heater")
        lines = briefing._facts_home(self.h)
        self.assertNotIn("No heaters are on.", lines)
        self.assertIn("Not reporting: Heater.", lines)

    def test_child_lock_is_no_heater(self):
        self.h.states.set("switch.heater_child_lock", "on", friendly_name="Heater Child Lock")
        self.h.registry_entries["switch.heater_child_lock"] = types.SimpleNamespace(entity_category="config", labels=set(), device_id=None)
        self.assertEqual(briefing._facts_home(self.h), [])

    def test_energy_is_read_by_unit_and_period(self):
        self.h.states.set("sensor.plug_energy_today", "1.5", friendly_name="Plug energy today", unit_of_measurement="kWh")
        self.h.states.set("sensor.meter", "2500", friendly_name="Boiler", unit_of_measurement="Wh", meter_period="daily")
        self.h.states.set("sensor.total", "900", friendly_name="Grid total", unit_of_measurement="kWh")
        self.h.states.set("sensor.temp", "21", friendly_name="Today temperature", unit_of_measurement="C")
        lines = briefing._facts_home(self.h)
        self.assertEqual(lines, ["Energy today: Plug 1.5 kilowatt hours, Boiler 2.5 kilowatt hours."])

    def test_energy_flag_off_leaves_heaters_and_kwh_out(self):
        self.h.states.set("switch.heater", "on", friendly_name="Heater")
        self.h.states.set("sensor.plug_energy_today", "1.5", friendly_name="Plug", unit_of_measurement="kWh")
        self.assertEqual(briefing._facts_home(self.h, energy=False), [])

    def test_people(self):
        self.h.states.set("person.a", "home", friendly_name="Alex")
        self.assertEqual(briefing._facts_home(self.h, people=True, energy=False), ["At home: Alex."])


class Service(unittest.TestCase):
    """The real jarvis.briefing service with a fake model."""
    def go(self, facts_only=True, model_text="", **data):
        h = H.Hass()
        h.states.set("switch.heater", "on", friendly_name="Heater")
        self.sent = []

        def chat(messages=None, **k):
            self.sent.append(messages)
            return {"text": model_text}
        client = types.SimpleNamespace(chat=chat)
        cfg = mock.MagicMock()
        cfg.runtime_get.side_effect = lambda hass, entry, key, default: facts_only if key == "jh_facts_only_briefings" else default
        clock = mock.MagicMock()   # the upstream date format only works on Linux, the tests also run on Windows
        clock.now.return_value.strftime.return_value = "Friday"
        clock.now.return_value.hour = 20
        with mock.patch.dict("sys.modules", {"jc.jarvis_config": cfg}), mock.patch.object(briefing, "save_message", lambda *a: None), \
                mock.patch("jc.jarvis_config", cfg, create=True), mock.patch.object(briefing, "datetime", clock), \
                mock.patch.object(briefing, "_gather_weather", lambda h: "12°C"), \
                mock.patch.object(briefing, "get_presence_summary", lambda h: {"people": []}), \
                mock.patch.object(briefing, "_gather_overnight_events", lambda h, n: []), \
                mock.patch.object(briefing, "_gather_open_things", lambda h, plain=False: []), \
                mock.patch.object(briefing, "_gather_calendar", lambda h: []), \
                mock.patch.object(briefing, "_gather_energy_anomalies", lambda h: []), \
                mock.patch.object(briefing, "build_system_prompt", lambda *a: "sys"), \
                mock.patch.object(briefing, "_greeting_instruction", lambda *a: ""), \
                mock.patch.object(proactive, "get_snapshot_summary", lambda hours=0: "Camera detections: AI words"), \
                mock.patch("jc.directive_helper.get_entry", lambda h: None):
            call = types.SimpleNamespace(data={"announce": False, "include_hazards": False, **data})
            return H.run(briefing.async_briefing(h, call, client, "sir", None, []))

    def test_facts_only_reads_no_ai_text(self):
        out = self.go()
        self.assertIn("Heaters on: Heater.", out["briefing"])
        self.assertNotIn("AI words", out["briefing"])
        self.assertEqual(self.sent, [])

    def test_switch_that_cannot_be_read_stays_with_facts(self):
        """The import or the settings read failing must not cost the briefing, and must not turn the model on."""
        with mock.patch("jc.directive_helper.get_entry", side_effect=ImportError("cannot import name")):
            self.assertTrue(briefing._jh_facts_only_on(H.Hass()))
        with mock.patch.dict("sys.modules", {"jc.directive_helper": types.ModuleType("jc.directive_helper")}):
            self.assertTrue(briefing._jh_facts_only_on(H.Hass()))

    def test_include_energy_false(self):
        self.assertNotIn("Heater", self.go(include_energy=False)["briefing"])

    def test_switch_off_gives_the_ai_all_facts_and_the_camera_line(self):
        out = self.go(facts_only=False, model_text="Good evening, sir.")
        self.assertEqual(out["briefing"], "Good evening, sir.")
        user = self.sent[0][1]["content"]
        self.assertIn("Heaters on: Heater.", user)
        self.assertIn("AI words", user)

    def test_empty_model_reply_is_cleaned_for_speech(self):
        out = self.go(facts_only=False, model_text="")
        self.assertIn("12 degrees", out["briefing"])
        self.assertNotIn("°C", out["briefing"])


class Proactive(unittest.TestCase):
    def test_counts_hold_no_ai_text(self):
        snap = types.SimpleNamespace(camera_name="Door", detection_type="person", analysis="A tall man in a hat")
        with mock.patch.object(proactive, "get_recent_snapshots", lambda hours: [snap, snap]):
            self.assertEqual(proactive.get_snapshot_counts(4), "Camera detections: Door 2.")

    def test_no_instruction_can_be_read_aloud(self):
        with open(proactive.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("Summarize what happened", src)
        self.assertNotIn("This is a scheduled briefing", src)
        self.assertIn("Multiple security events were detected", src)


if __name__ == "__main__":
    unittest.main()
