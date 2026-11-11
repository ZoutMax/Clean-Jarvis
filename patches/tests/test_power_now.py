"""Patch 105: the power right now without a whole-house meter, and no energy or power figure without a reading behind it."""
import importlib, json, types, unittest
from unittest import mock

import jh_harness as H

agent = H.load("agent")
tool_driver = H.load("tool_driver")
energy = H.load("energy")
le = H.load("local_engine")


def entry(entity_id, device_id):
    return types.SimpleNamespace(entity_id=entity_id, device_id=device_id)


class House:
    """An Energy dashboard with five devices, as Home Assistant keeps it."""
    def __init__(self, hass):
        self.prefs = {"energy_sources": [], "device_consumption": [
            {"stat_consumption": "sensor.heater_total_energy"},
            {"stat_consumption": "sensor.candle_estimate_energy"},
            {"stat_consumption": "sensor.kitchen_total_energy"},
            {"stat_consumption": "sensor.boiler_energy", "stat_rate": "sensor.boiler_kw", "name": "Boiler"},
            {"stat_consumption": "sensor.old_meter_energy"}]}
        prefs = self.prefs

        async def manager(h):
            return types.SimpleNamespace(data=prefs)
        importlib.import_module("homeassistant.components.energy.data").async_get_manager = manager
        hass.registry_entries = {
            "sensor.heater_total_energy": entry("sensor.heater_total_energy", "d1"),
            "sensor.heater_power": entry("sensor.heater_power", "d1"),
            "sensor.heater_voltage": entry("sensor.heater_voltage", "d1"),
            "sensor.candle_estimate_energy": entry("sensor.candle_estimate_energy", "d2"),
            "sensor.candle_estimate_power": entry("sensor.candle_estimate_power", "d2"),
            "sensor.kitchen_total_energy": entry("sensor.kitchen_total_energy", "d3"),
            "sensor.kitchen_power": entry("sensor.kitchen_power", "d3"),
            "sensor.old_meter_energy": entry("sensor.old_meter_energy", "d5"),
        }
        hass.registry_devices = {"d1": types.SimpleNamespace(name="Bedroom Heater", name_by_user=None),
                                 "d2": types.SimpleNamespace(name="candle bulb", name_by_user="Candle"),
                                 "d3": types.SimpleNamespace(name="Kitchen", name_by_user=None)}
        w = dict(device_class="power", unit_of_measurement="W")
        hass.states.set("sensor.heater_power", "2944.0", **w)
        hass.states.set("sensor.heater_voltage", "230", device_class="voltage", unit_of_measurement="V")
        hass.states.set("sensor.candle_estimate_power", "0.60", **w)
        hass.states.set("sensor.kitchen_power", "unavailable", **w)
        hass.states.set("sensor.boiler_kw", "1.5", device_class="power", unit_of_measurement="kW")


class PowerNow(unittest.TestCase):
    def setUp(self):
        self.hass = H.Hass()
        self.house = House(self.hass)

    def test_each_device_is_read_live_and_what_it_covers_is_said(self):
        now = H.run(energy.jh_devices_power_now(self.hass))
        self.assertEqual(now["devices"], [{"name": "Bedroom Heater", "watts": 2944.0}, {"name": "Boiler", "watts": 1500.0},
                                          {"name": "Candle", "watts": 0.6}])
        self.assertEqual(now["total_watts"], 4445)
        self.assertEqual(now["unreadable"], ["Kitchen"])          # unavailable is not 0 W
        self.assertIn("only the 4 monitored devices", now["covers"])
        self.assertIn("no whole-house meter", now["covers"])

    def test_a_device_with_two_power_sensors_is_left_out_rather_than_guessed(self):
        self.hass.registry_entries["sensor.heater_power_l2"] = entry("sensor.heater_power_l2", "d1")
        self.hass.states.set("sensor.heater_power_l2", "10", device_class="power", unit_of_measurement="W")
        names = [d["name"] for d in H.run(energy.jh_devices_power_now(self.hass))["devices"]]
        self.assertNotIn("Bedroom Heater", names)

    def test_no_dashboard_devices_is_nothing(self):
        self.house.prefs["device_consumption"] = []
        self.assertIsNone(H.run(energy.jh_devices_power_now(self.hass)))

    def test_a_broken_dashboard_never_raises(self):
        async def broken(h):
            raise RuntimeError("energy not loaded")
        importlib.import_module("homeassistant.components.energy.data").async_get_manager = broken
        self.assertIsNone(H.run(energy.jh_devices_power_now(self.hass)))


class InstantAnswer(unittest.TestCase):
    """'What is the power consumption?' without a whole-house meter: the devices, read live, at once."""
    def setUp(self):
        self.hass = H.Hass()
        House(self.hass)

    def test_the_line_names_the_total_the_biggest_and_what_is_missing(self):
        line = H.run(le._jh_power_now(self.hass, "sir"))
        self.assertEqual(line, "Right now the 4 monitored devices draw 4.4 kW, sir. Most of it: Bedroom Heater 2.9 kW, "
                               "Boiler 1.5 kW. Kitchen could not be read. There is no whole-house meter, "
                               "so the house uses more.")   # the 0.6 W candle is not worth naming

    def test_try_local_answers_it_without_jarvis(self):
        with mock.patch.object(le, "_jh_try_color", mock.AsyncMock(return_value=None)):
            r = H.run(le.try_local(self.hass, "What is the power consumption?", "sir"))
        self.assertIsNotNone(r)
        self.assertTrue(r.text.startswith("Right now the 4 monitored devices draw 4.4 kW"), r.text)

    def test_a_whole_house_meter_still_wins(self):
        self.hass.states.set("sensor.total_power", "3100", device_class="power", unit_of_measurement="W")
        self.assertIn("3100 W", le._ctx_query(self.hass, "energy", "sir"))

    def test_nothing_readable_leaves_it_to_jarvis(self):
        for e in ("sensor.heater_power", "sensor.candle_estimate_power", "sensor.boiler_kw"):
            self.hass.states.set(e, "unknown", device_class="power", unit_of_measurement="W")
        self.assertIsNone(H.run(le._jh_power_now(self.hass, "sir")))


class EnergyStatusTool(unittest.TestCase):
    def setUp(self):
        self.hass = H.Hass()
        House(self.hass)

    def test_without_a_meter_the_tool_gives_devices_now(self):
        r = json.loads(H.run(agent._exec_energy_status(self.hass, {})))
        self.assertIsNone(r["watts"])          # the 2944 W heater plug is not taken for the whole house
        self.assertEqual(r["meter"], "")
        self.assertEqual(r["devices_now"]["total_watts"], 4445)

    def test_a_named_meter_is_still_the_house(self):
        self.hass.states.set("sensor.mains_power", "3500", device_class="power", unit_of_measurement="W",
                             friendly_name="Mains power")
        r = json.loads(H.run(agent._exec_energy_status(self.hass, {})))
        self.assertEqual((r["watts"], r["meter"]), (3500, "sensor.mains_power"))
        self.assertNotIn("devices_now", r)

    def test_a_single_power_sensor_is_still_taken_as_the_meter(self):
        states = {"sensor.meter": H.State("sensor.meter", "900", device_class="power", unit_of_measurement="W")}
        self.assertEqual(energy._fallback_meter(None, states), "sensor.meter")
        states["sensor.plug"] = H.State("sensor.plug", "2000", device_class="power", unit_of_measurement="W")
        self.assertIsNone(energy._fallback_meter(None, states))

    def test_the_tool_description_says_so(self):
        desc = next(t["function"]["description"] for t in agent.JARVIS_TOOLS if t["function"]["name"] == "energy_status")
        self.assertIn("devices_now", desc)


class Figure(unittest.TestCase):
    def test_figures_the_person_did_not_say(self):
        self.assertTrue(agent._jh_live_figure("This month we've used 64.01 kWh from our monitored devices, sir.",
                                              "What is the power consumption?"))
        self.assertTrue(agent._jh_live_figure("The heater draws 2,944 W.", "how much is the heater using"))
        self.assertTrue(agent._jh_live_figure("About 3 kilowatts right now.", "power?"))

    def test_no_figure_or_their_own_figure_is_fine(self):
        self.assertFalse(agent._jh_live_figure("A kettle uses a lot of power, sir.", "does a kettle use much power?"))
        self.assertFalse(agent._jh_live_figure("Yes, 2000 W is a lot for a heater.", "is 2000 W a lot for a heater?"))
        self.assertFalse(agent._jh_live_figure("Give it 2 weeks.", "how long?"))


class Script:
    """A fake model: each entry is reply text, or a list of tool calls as (name, args)."""
    name, model = "fake", "fake"

    def __init__(self, *turns):
        self.turns, self.seen = list(turns), []

    def chat(self, messages, tools=None, *a, **k):
        self.seen.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else "out of script"
        if isinstance(turn, list):
            return {"text": "", "tool_calls": [{"id": f"c{i}", "name": n, "args": a} for i, (n, a) in enumerate(turn)]}
        return {"text": turn, "tool_calls": []}


class Loop(unittest.TestCase):
    """The real run_agent loop: a figure with no reading behind it is sent back once."""
    def setUp(self):
        self.h = H.Hass()
        self.ran = []

        def tool(name):
            async def run(hass, args):
                self.ran.append(name)
                return json.dumps({"watts": None, "devices_now": {"total_watts": 12}})
            return run

        for p in (mock.patch.dict(agent._TOOL_MAP, {name: tool(name) for name in agent._TOOL_MAP}),
                  mock.patch.object(agent, "_jh_opt", lambda h, k: True),
                  mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=None)),
                  mock.patch.object(agent, "_floor_rooms", lambda h: []),
                  mock.patch.object(agent, "_load_learned", lambda: {})):
            p.start()
            self.addCleanup(p.stop)

    def ask(self, script, text):
        ui = types.SimpleNamespace(text=text, language="en", device_id=None, conversation_id=None,
                                   context=types.SimpleNamespace(user_id=None))
        with mock.patch.object(agent, "_create_provider_with_fallback", mock.AsyncMock(return_value=script)):
            return H.run(agent.run_agent(self.h, messages=[{"role": "user", "content": text}], persona="p", provider_name="x",
                                         api_key="k", model="m", user_input=ui, config={"honorific": "sir"}))

    def sent_back(self, script):
        return sum(1 for m in script.seen[-1] if m["role"] == "user" and "without reading it in this request" in m["content"])

    def test_a_stale_figure_is_sent_back_and_the_reading_is_used(self):
        s = Script("This month we've used 64.01 kWh, sir.", [("energy_status", {})],
                   "Right now the monitored devices draw 12 W, sir.")
        self.assertEqual(self.ask(s, "What is the power consumption?"), "Right now the monitored devices draw 12 W, sir.")
        self.assertEqual(self.ran, ["energy_status"])
        self.assertEqual(self.sent_back(s), 1)

    def test_only_once(self):
        s = Script("We used 64.01 kWh, sir.", "We used 64.01 kWh, sir.")
        self.assertEqual(self.ask(s, "What is the power consumption?"), "We used 64.01 kWh, sir.")
        self.assertEqual(len(s.seen), 2)

    def test_a_figure_from_a_reading_passes(self):
        s = Script([("energy_status", {})], "Right now the monitored devices draw 12 W, sir.")
        self.assertEqual(self.ask(s, "What is the power consumption?"), "Right now the monitored devices draw 12 W, sir.")
        self.assertEqual(len(s.seen), 2)

    def test_plain_chat_without_figures_is_untouched(self):
        s = Script("A thermostat holds the room temperature steady, sir.")
        self.assertEqual(self.ask(s, "what does a thermostat do?"), "A thermostat holds the room temperature steady, sir.")
        self.assertEqual(len(s.seen), 1)


if __name__ == "__main__":
    unittest.main()
