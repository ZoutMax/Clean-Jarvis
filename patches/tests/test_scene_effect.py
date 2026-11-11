"""Patch 106: a lamp's own effect or scene by name ("set Ufo to Blue sky"), read from the lamp's effect_list."""
import types, unittest
from unittest import mock

import jh_harness as H

le = H.load("local_engine")


class Lamps(unittest.TestCase):
    def setUp(self):
        self.hass = H.Hass()
        self.hass.states.set("light.halo", "on", friendly_name="Ufo", effect="off", effect_list=["Scene", "Blue sky", "off"],
                             supported_color_modes=["color_temp", "hs"])
        self.hass.states.set("light.led", "on", friendly_name="Stick", effect="off", effect_list=["Scene", "off"],
                             supported_color_modes=["hs"])
        self.hass.states.set("light.desk", "on", friendly_name="Desk", supported_color_modes=["brightness"])
        self.sent = []

        async def seam(hass, domain, service, data, **kw):
            self.sent.append((domain, service, dict(data)))
            if self.lamp_obeys and "effect" in data:
                s = hass.states.get(data["entity_id"])
                hass.states.set(data["entity_id"], "on", **{**s.attributes, "effect": data["effect"]})
            return True
        self.lamp_obeys = True
        for p in (mock.patch.object(le, "_seam_execute", seam), mock.patch.object(le, "_needs_confirmation", lambda *a, **k: False),
                  mock.patch.object(le.asyncio if hasattr(le, "asyncio") else __import__("asyncio"), "sleep", mock.AsyncMock())):
            p.start()
            self.addCleanup(p.stop)

    def ask(self, text):
        return H.run(le._jh_try_effect(self.hass, text.lower(), "sir"))

    def test_set_lamp_to_its_scene(self):
        r = self.ask("Set Ufo to Blue sky")
        self.assertEqual(r.text, "Done, sir. Ufo is playing Blue sky.")
        self.assertTrue(r.success)
        self.assertEqual(self.sent, [("light", "turn_on", {"entity_id": "light.halo", "effect": "Blue sky"})])

    def test_other_wordings(self):
        for said in ("Jarvis, switch the Ufo to blue sky please", "start blue sky on ufo", "play the Blue sky scene on Ufo",
                     "put ufo on blue sky", "set ufo to blue sky effect"):
            self.sent.clear()
            self.assertIsNotNone(self.ask(said), said)
            self.assertEqual(self.sent[0][2]["effect"], "Blue sky", said)

    def test_effect_the_lamp_does_not_have_falls_through(self):
        self.assertIsNone(self.ask("set stick to blue sky"))      # Stick has no Blue sky: JARVIS answers, nothing sent
        self.assertIsNone(self.ask("set ufo to rainbow"))
        self.assertIsNone(self.ask("set desk to blue sky"))
        self.assertEqual(self.sent, [])

    def test_not_an_effect_command(self):
        for said in ("turn ufo on", "turn off the ufo", "set ufo to red", "what is blue sky", "set the heating to 21"):
            self.assertIsNone(self.ask(said), said)
        self.assertEqual(self.sent, [])

    def test_off_is_not_an_effect_name(self):
        self.assertIsNone(self.ask("set ufo to off"))

    def test_no_done_when_the_lamp_does_not_show_it(self):
        self.lamp_obeys = False
        r = self.ask("set ufo to blue sky")
        self.assertFalse(r.success)
        self.assertIn("has not confirmed", r.text)
        self.assertNotIn("Done", r.text)

    def test_offline_lamp_is_said_honestly(self):
        self.hass.states.set("light.halo", "unavailable", friendly_name="Ufo", effect_list=["Scene", "Blue sky", "off"])
        r = self.ask("set ufo to blue sky")
        self.assertEqual(r.text, "Ufo is offline, sir. Nothing was changed.")
        self.assertEqual(self.sent, [])

    def test_try_local_reaches_it_after_colours(self):
        r = H.run(le.try_local(self.hass, "Set Ufo to Blue sky", "sir"))
        self.assertIsNotNone(r)
        self.assertEqual(r.text, "Done, sir. Ufo is playing Blue sky.")

    def test_colours_still_win(self):
        with mock.patch.object(le, "_jh_try_effect", mock.AsyncMock(return_value=None)) as eff:
            H.run(le.try_local(self.hass, "Set Ufo to red", "sir"))
        eff.assert_not_called()


if __name__ == "__main__":
    unittest.main()
