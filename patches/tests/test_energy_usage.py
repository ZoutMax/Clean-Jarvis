"""energy_usage (patch 102): energy used over a period from the Energy dashboard, and an honest note of what it covers."""
import datetime, importlib, json, unittest

import jh_harness as H


class _Clock:
    """homeassistant.util.dt for the test: a fixed 'now' in local time."""
    NOW = datetime.datetime(2026, 11, 8, 23, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))

    @staticmethod
    def now():
        return _Clock.NOW

    @staticmethod
    def as_utc(dt):
        return dt.astimezone(datetime.timezone.utc)


class EnergyUsage(unittest.TestCase):
    def setUp(self):
        self.agent = H.load("agent")
        self.asked = []
        self.prefs = {}
        self.stats = {}
        test = self
        util = importlib.import_module("homeassistant.util")
        util.dt = _Clock
        data = importlib.import_module("homeassistant.components.energy.data")

        async def manager(hass):
            class M:
                pass
            m = M()
            m.data = test.prefs
            return m
        data.async_get_manager = manager
        recorder = importlib.import_module("homeassistant.components.recorder")

        class Instance:
            async def async_add_executor_job(self, fn, *args):
                return fn(*args)
        recorder.get_instance = lambda hass: Instance()
        statistics = importlib.import_module("homeassistant.components.recorder.statistics")

        def during(hass, start, end, ids, period, units, types):
            test.asked.append((start, end, set(ids), period, set(types)))
            return {sid: rows for sid, rows in test.stats.items() if sid in ids}
        statistics.statistics_during_period = during
        self.hass = H.Hass()

    def ask(self, **args):
        return json.loads(H.run(self.agent._exec_energy_usage(self.hass, args)))

    def test_the_tool_is_offered_and_allowed_for_the_household(self):
        names = [t["function"]["name"] for t in self.agent.JARVIS_TOOLS]
        self.assertIn("energy_usage", names)
        self.assertIs(self.agent._TOOL_MAP["energy_usage"], self.agent._exec_energy_usage)
        self.assertIn("energy_usage", self.agent._JH_HOUSEHOLD_TOOLS)
        self.assertIn("energy_usage", self.agent._MAIL_SAFE_TOOLS)

    def test_devices_only_says_it_is_not_the_whole_house(self):
        self.prefs = {"energy_sources": [], "device_consumption": [
            {"stat_consumption": "sensor.heater_total_energy"}, {"stat_consumption": "sensor.lamp_energy", "name": "Desk lamp"}]}
        self.stats = {"sensor.heater_total_energy": [{"change": 30.0}, {"change": 28.49}],
                      "sensor.lamp_energy": [{"change": 0.2}, {"change": None}]}
        self.hass.states.set("sensor.heater_total_energy", "120", friendly_name="Bedroom Heater Total energy")
        r = self.ask()
        self.assertEqual(r["period"], "this_month")
        self.assertEqual(r["total_kwh"], 58.69)
        self.assertIn("only the 2 monitored devices", r["covers"])
        self.assertIn("no whole-house meter", r["covers"])
        self.assertEqual(r["biggest_users"][0], {"name": "Bedroom Heater Total energy", "kwh": 58.49})
        self.assertEqual(r["biggest_users"][1]["name"], "Desk lamp")
        start, end, ids, period, types = self.asked[0]
        self.assertEqual(start, datetime.datetime(2026, 11, 1, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=1))).astimezone(datetime.timezone.utc))
        self.assertEqual((period, types), ("hour", {"change"}))

    def test_with_a_grid_meter_the_total_is_the_house(self):
        self.prefs = {"energy_sources": [{"type": "grid", "flow_from": [{"stat_energy_from": "sensor.grid_in"}]},
                                         {"type": "solar", "stat_energy_from": "sensor.solar"}],
                      "device_consumption": [{"stat_consumption": "sensor.heater_total_energy"}]}
        self.stats = {"sensor.grid_in": [{"change": 200.0}], "sensor.heater_total_energy": [{"change": 58.0}],
                      "sensor.solar": [{"change": 999.0}]}
        r = self.ask(period="last_month")
        self.assertEqual(r["total_kwh"], 200.0)
        self.assertEqual(r["monitored_devices_kwh"], 58.0)
        self.assertIn("whole house", r["covers"])
        start, end, *_ = self.asked[0]
        local = _Clock.NOW.tzinfo
        self.assertEqual((start.astimezone(local).month, end.astimezone(local).month), (10, 11))   # October, up to 1 November
        self.assertNotIn("sensor.solar", self.asked[0][2])

    def test_periods(self):
        self.prefs = {"device_consumption": [{"stat_consumption": "sensor.x"}]}
        for period, days in (("today", 0), ("yesterday", 1), ("this_week", 6), ("this_year", 311)):
            self.asked.clear()
            r = self.ask(period=period)
            self.assertEqual(r["period"], period)
            start = self.asked[0][0].astimezone(_Clock.NOW.tzinfo)
            self.assertEqual((_Clock.NOW.replace(hour=0, minute=0) - start.replace(second=0, microsecond=0)).days, days, period)
        self.assertEqual(self.ask(period="whenever")["period"], "this_month")

    def test_the_prompt_sends_period_questions_to_energy_usage(self):
        # live test: without this rule the local model answered from the power-now figure and never called the tool
        import inspect
        src = inspect.getsource(self.agent)
        self.assertIn("Energy USED is not the power right now", src)
        self.assertIn("never say it only tracks the current power", src)

    def test_no_energy_sensors_is_said_plainly(self):
        self.prefs = {"energy_sources": [], "device_consumption": []}
        self.assertIn("no energy sensors", self.ask()["error"])


class InstantPath(unittest.TestCase):
    """Patch 103: the instant path leaves period questions to JARVIS and never says 'no total power sensor'."""
    def setUp(self):
        self.le = H.load("local_engine")
        self.hass = H.Hass()

    def qtype(self, text):
        import re
        for pattern, qtype in self.le._QUERY_PATTERNS:
            if re.search(pattern, text):
                return qtype
        return None

    def test_period_questions_go_to_jarvis(self):
        for text in ("what is our power consumption until now for this month", "power consumption today",
                     "what is the energy usage this week", "energy usage so far"):
            self.assertNotEqual(self.qtype(text), "energy", text)
        self.assertEqual(self.qtype("what is the power consumption"), "energy")

    def test_no_whole_house_sensor_is_not_a_dead_end(self):
        self.hass.states.set("sensor.heater_power", "2900", device_class="power", unit_of_measurement="W")
        self.assertIsNone(self.le._ctx_query(self.hass, "energy", "sir"))
        self.hass.states.set("sensor.total_power", "3100", device_class="power", unit_of_measurement="W")
        self.assertIn("3100 W", self.le._ctx_query(self.hass, "energy", "sir"))


class LoneFiller(unittest.TestCase):
    """Patch 104: a lone filler word that answers no question of JARVIS is not a command."""
    def test_fillers(self):
        import re, inspect
        src = open(H.built_dir() + "/conversation.py", encoding="utf-8").read()
        start = src.index('_jh_re.fullmatch(r"') + len('_jh_re.fullmatch(r"')
        pattern = src[start:src.index('",', start)]
        for text in ("Okay.", "ok", "Thanks!", "thank you", "Bye.", "  Hmm  ", "oké"):
            self.assertTrue(re.fullmatch(pattern, text.lower()), text)
        for text in ("Okay, turn on the lamp", "yes", "no", "lights off", "thanks for nothing, open the door"):
            self.assertFalse(re.fullmatch(pattern, text.lower()), text)
        self.assertIn('endswith("?")', src)   # after a question of JARVIS, "Okay." is the answer


if __name__ == "__main__":
    unittest.main()
