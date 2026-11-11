"""__init__.py service gate, camera poll limit, and one real pass through conversation.py."""
import importlib.util, json, logging, os, sys, tempfile, types, unittest
from unittest import mock

import jh_harness as H

conversation = H.load("conversation")
camera = H.load("camera")
websocket = H.load("websocket")
local_engine = H.load("local_engine")
OPEN = {"analyze_camera", "briefing", "scene_by_intent", "routine", "add_reminder", "check_packages", "nap", "shush", "unshush", "observer_status"}


def load_init():
    spec = importlib.util.spec_from_file_location("jc.jh_real_init", os.path.join(H.built_dir(), "__init__.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["jc.jh_real_init"] = module
    spec.loader.exec_module(module)
    return module


class PanelParity(unittest.TestCase):
    """Everything in Core settings can also be set in the panel, and the panel shows the stored value."""
    KEYS = ("jh_strict_actions", "jh_mail_block", "jh_facts_only_briefings", "camera_watch_default")

    def read(self, name):
        with open(os.path.join(H.built_dir(), name), encoding="utf-8") as f:
            return f.read()

    def test_writable_only_through_the_admin_command(self):
        for key in self.KEYS:
            self.assertIn(key, websocket.PANEL_WRITABLE_KEYS)
        src = self.read("websocket.py")
        self.assertIn('@websocket_api.require_admin\n@websocket_api.websocket_command({\n    vol.Required("type"): "jarvis/update_config"', src)

    def test_the_stored_value_is_read_back(self):
        src = self.read("websocket.py")
        for key in self.KEYS[:3]:
            self.assertIn('"%s": bool(_runtime_opt(hass, entry, "%s", True))' % (key, key), src)
        self.assertIn('"camera_watch_default": _jh_cam_default(hass, entry)', src)   # audit 10: an empty panel choice counts
        self.assertIn('return str(_runtime_opt(hass, entry, "camera_watch_default", "") or "")', src)

    def test_the_panel_has_a_control_for_each(self):
        src = self.read("frontend/jarvis-panel.js")
        for key in self.KEYS[:3]:
            self.assertIn("tog('%s', c.%s !== false)" % (key, key), src)   # on unless switched off
        self.assertIn('data-cfg-key="camera_watch_default"', src)


class Services(unittest.TestCase):
    def setUp(self):
        self.init = load_init()
        self.open, self.admin = {}, {}
        hass = mock.MagicMock()
        hass.services.has_service.return_value = False
        hass.services.async_register.side_effect = lambda d, n, h, schema=None: self.open.__setitem__(n, schema)
        service_helpers = __import__("homeassistant.helpers.service", fromlist=["x"])
        service_helpers.async_register_admin_service = lambda h, d, n, f, schema=None: self.admin.__setitem__(n, schema)
        self.init._register_services(hass, mock.MagicMock(), lambda: None, mock.MagicMock())

    def test_only_the_listed_services_are_open(self):
        self.assertEqual(set(self.open), OPEN)

    def test_dangerous_services_are_admin_only(self):
        for name in ("create_automation", "lockdown", "forget", "remember", "sentinel_stop", "observer_stop", "restore", "database_purge"):
            self.assertIn(name, self.admin, name)

    def test_every_service_is_registered_once(self):
        self.assertEqual(set(self.open) | set(self.admin), set(self.init.JARVIS_SERVICE_NAMES))
        self.assertFalse(set(self.open) & set(self.admin))

    def test_a_service_without_schema_still_accepts_its_fields(self):
        self.assertIsNotNone(self.admin["remember"])


class Routines(unittest.TestCase):
    def test_person_routines_are_private(self):
        h = H.Hass()
        h.states.set("person.robin", "home", friendly_name="Robin", user_id="u2")
        rows = {"Robin": [1], "Alex": [2]}
        member, admin = H.Connection(H.User("u2", False)), H.Connection(H.User("u1", True))
        with mock.patch.object(websocket, "_get_person_routines", lambda: rows):
            H.run(websocket.ws_get_person_routines(h, member, {"id": 1}))
            H.run(websocket.ws_get_person_routines(h, admin, {"id": 1}))
        self.assertEqual(member.sent[0]["routines"], {"Robin": [1]})
        self.assertEqual(admin.sent[0]["routines"], rows)


class Camera(unittest.TestCase):
    def setUp(self):
        camera._JH_BLANK.clear()
        self.h = H.Hass()
        self.blank = types.SimpleNamespace(content=b"")
        self.wakes = mock.AsyncMock()
        for p in (mock.patch.object(camera, "find_backend", lambda h, e: None),
                  mock.patch.object(camera, "_prewarm_stream", self.wakes),
                  mock.patch.object(camera, "camera_get_image", mock.AsyncMock(return_value=self.blank), create=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_polling_a_blank_camera_wakes_it_once_and_warns_once(self):
        with self.assertLogs(camera._LOGGER, logging.WARNING) as logs:
            for _ in range(5):
                self.assertIsNone(H.run(camera._get_best_image(self.h, "camera.x", poll=True)))
        self.assertEqual(self.wakes.await_count, 1)
        self.assertEqual(len(logs.records), 1)

    def test_an_event_capture_always_tries_the_wake(self):
        H.run(camera._get_best_image(self.h, "camera.x", poll=True))
        H.run(camera._get_best_image(self.h, "camera.x"))
        self.assertEqual(self.wakes.await_count, 2)

    def test_the_hold_runs_out(self):
        H.run(camera._get_best_image(self.h, "camera.x", poll=True))
        camera._JH_BLANK["camera.x"] -= 61
        H.run(camera._get_best_image(self.h, "camera.x", poll=True))
        self.assertEqual(self.wakes.await_count, 2)


class Flow(unittest.TestCase):
    """A real turn through _handle_message_impl. The local engine answers, so no model is needed."""
    def setUp(self):
        conversation._JH_KEY_OWNERS.clear()
        self.h = H.Hass()
        self.h.states.set("person.alex", "home", friendly_name="Alex", user_id="alex")
        self.h.states.set("person.robin", "not_home", friendly_name="Robin", user_id="robin")
        self.h.auth.users.update(alex=H.User("alex", True), robin=H.User("robin", False))
        self.saved, self.subjects, self.pending = [], [], []
        self.seed = []

        async def try_local(hass, text, honorific="sir", force=False, pending_key=""):
            self.pending.append(pending_key)
            return local_engine.LocalResult(text="Done, sir.", success=True)

        async def block(hass, text, subjects=None):
            self.subjects.append(subjects)
            return ""

        async def load_recent(hass, hours, limit, user_id="", device_id=""):
            self.seed.append((user_id, device_id))
            return []

        knowledge = H.load("knowledge")
        memory_thread = H.load("memory_thread")
        memory = H.load("memory")
        cognitive_core = H.load("cognitive_core")
        for p in (mock.patch.object(local_engine, "try_local", try_local),
                  mock.patch.object(knowledge, "prompt_block_async", block),
                  mock.patch.object(memory_thread, "load_recent", load_recent),
                  mock.patch.object(memory_thread, "config", lambda: (True, 48, 12)),
                  mock.patch.object(conversation, "save_message", lambda *a, **k: self.saved.append((a, k))),
                  mock.patch.object(conversation, "_ensure_persona_loaded", mock.AsyncMock()),
                  mock.patch.object(conversation, "_check_and_claim_dedup", lambda *a: (False, None)),
                  mock.patch.object(memory, "store_memory", lambda *a, **k: True),
                  mock.patch.object(memory, "get_conversation_context", lambda *a: ""),
                  mock.patch.object(cognitive_core, "get_pending_offer", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def turn(self, uid):
        agent = mock.MagicMock()
        agent.hass = self.h
        agent._histories, agent._threaded = {}, set()
        agent._history = lambda cid: agent._histories.setdefault(cid, [])
        agent._honorific.return_value = "sir"
        agent._persona.return_value = "persona"
        agent._use_hass_api.return_value = False
        agent._opt.side_effect = lambda key, default=None: default
        agent._media_playing_near.return_value = False
        agent._maybe_seed_history = lambda *a: conversation.JarvisAgent._maybe_seed_history(agent, *a)
        ui = types.SimpleNamespace(text="turn on the lamp", device_id=None, conversation_id="conv1", language="en",
                                   context=types.SimpleNamespace(user_id=uid))
        return H.run(conversation.JarvisAgent._handle_message_impl(agent, ui))

    def test_a_login_flows_through_every_step(self):
        self.turn("robin")
        self.assertEqual(self.seed, [("robin", "conv1")])
        self.assertEqual(self.pending, ["conv1"])
        self.assertEqual(self.subjects, [["robin", "household"]])
        self.assertTrue(self.saved)
        self.assertEqual(self.saved[0][1].get("user_id"), "robin")
        self.assertEqual(self.saved[-1][0][-1], "robin")

    def test_an_admin_keeps_the_older_shared_facts(self):
        self.turn("alex")
        self.assertEqual(self.subjects, [["alex", "household", "primary"]])

    def test_presence_cannot_make_robin_read_alexs_facts(self):
        """Alex is alone at home, Robin chats from outside. This was the leak."""
        self.turn("robin")
        self.assertNotIn("alex", self.subjects[0])

    def test_voice_with_no_login_uses_the_presence_guess(self):
        identity = H.load("identity")
        with mock.patch.object(identity, "resolve", lambda hass, device_id=None: "x"), \
                mock.patch.object(identity, "subject_for", lambda i: "guess"):
            self.turn(None)
        self.assertEqual(self.subjects, [["guess", "household"]])

    def test_chat_words_are_not_logged_as_a_warning(self):
        with self.assertLogs(conversation._LOGGER, logging.DEBUG) as logs:
            self.turn("robin")
        for record in logs.records:
            if record.levelno >= logging.WARNING:
                self.assertNotIn("turn on the lamp", record.getMessage())


class FakeModel:
    name, model = "fake", "fake"

    def __init__(self):
        self.seen = []

    def chat(self, messages, tools=None, *a, **k):
        self.seen.append([dict(m) for m in messages])
        return {"text": "Noted, sir.", "tool_calls": []}


class RealTurn(unittest.TestCase):
    """The real _handle_message_impl and the real run_agent, so what reaches the model is what a person could read back."""
    PRIVATE = "my test result is back and it is private"

    def setUp(self):
        conversation._JH_KEY_OWNERS.clear()
        agent_m, tool_driver = H.load("agent"), H.load("tool_driver")
        knowledge, memory_thread, memory = H.load("knowledge"), H.load("memory_thread"), H.load("memory")
        self.core = H.load("cognitive_core")
        connectivity, ha_secrets, jc = H.load("connectivity"), H.load("ha_secrets"), H.load("jarvis_config")
        self.h = H.Hass()
        self.h.states.set("person.alex", "home", friendly_name="Alex", user_id="alex")
        self.h.states.set("person.robin", "home", friendly_name="Robin", user_id="robin")
        self.h.auth.users.update(alex=H.User("alex", True), robin=H.User("robin", False), sam=H.User("sam", False))
        self.model = FakeModel()

        async def nothing_local(*a, **k):
            return None

        async def no_block(*a, **k):
            return ""
        for p in (mock.patch.object(local_engine, "try_local", nothing_local),
                  mock.patch.object(knowledge, "prompt_block_async", no_block),
                  mock.patch.object(memory_thread, "config", lambda: (False, 48, 12)),
                  mock.patch.object(conversation, "save_message", lambda *a, **k: None),
                  mock.patch.object(conversation, "_ensure_persona_loaded", mock.AsyncMock()),
                  mock.patch.object(conversation, "_check_and_claim_dedup", lambda *a: (False, None)),
                  mock.patch.object(memory, "store_memory", lambda *a, **k: True),
                  mock.patch.object(memory, "get_conversation_context", lambda *a: ""),
                  mock.patch.object(connectivity, "allow_request", lambda: True),
                  mock.patch.object(ha_secrets, "async_get_provider_key", mock.AsyncMock(return_value="k")),
                  mock.patch.object(jc, "effective_config", lambda e: {"honorific": "sir"}),
                  mock.patch.object(agent_m, "_create_provider_with_fallback", mock.AsyncMock(return_value=self.model)),
                  mock.patch.object(agent_m, "_jh_opt", lambda h, k: True),
                  mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=None)),
                  mock.patch.object(agent_m, "_floor_rooms", lambda h: []),
                  mock.patch.object(agent_m, "_load_learned", lambda: {})):
            p.start()
            self.addCleanup(p.stop)
        a = mock.MagicMock()
        a.hass = self.h
        a._histories, a._threaded = {}, set()
        a._history = lambda cid: a._histories.setdefault(cid, [])
        a._honorific.return_value = "sir"
        a._persona.return_value = "persona"
        a._use_hass_api.return_value = False
        a._opt.side_effect = lambda k, d=None: d
        a._rt_opt.side_effect = lambda k, d=None: d
        a._media_playing_near.return_value = False
        a._maybe_seed_history = mock.AsyncMock()
        a.entry = types.SimpleNamespace(entry_id="e")
        self.agent = a

    def turn(self, uid, text, cid=None, device=None):
        ui = types.SimpleNamespace(text=text, device_id=device, conversation_id=cid, language="en", context=types.SimpleNamespace(user_id=uid))
        return H.run(conversation.JarvisAgent._handle_message_impl(self.agent, ui))

    def said(self, key):
        return " ".join(str(m.get("content")) for m in self.agent._histories.get(key, []))


class HistoryOwner(RealTurn):
    def assert_private(self, cid, owner_key):
        self.model.seen.clear()
        self.turn("robin", "what did I just say?", cid=cid)
        self.assertTrue(self.model.seen)
        self.assertNotIn(self.PRIVATE, str(self.model.seen))
        self.assertNotIn("what did I just say?", self.said(owner_key))
        self.assertIn("what did I just say?", self.said("user_robin"))

    def test_a_non_admin_cannot_read_the_chat_of_another_login_by_its_user_key(self):
        self.turn("alex", self.PRIVATE)
        self.assertIn(self.PRIVATE, self.said("user_alex"))
        self.assert_private("user_alex", "user_alex")

    def test_a_conversation_first_used_by_another_login_is_refused(self):
        self.turn("alex", self.PRIVATE, cid="01JABC")
        self.assert_private("01JABC", "01JABC")

    def test_the_key_of_a_login_that_has_not_chatted_yet_cannot_be_taken(self):
        self.turn("robin", "my own secret", cid="user_alex")
        self.assertEqual(list(self.agent._histories), ["user_robin"])
        self.turn("alex", "hello there")
        self.assertNotIn("my own secret", self.said("user_alex"))

    def test_voice_history_is_no_key_for_a_non_admin(self):
        self.turn(None, self.PRIVATE)
        self.assert_private("default", "default")
        self.turn(None, self.PRIVATE, device="sat2")
        self.assert_private("sat2", "sat2")

    def test_a_voice_device_that_has_not_spoken_cannot_be_taken_in_advance(self):
        self.h.registry_devices["sat1"] = types.SimpleNamespace(id="sat1")
        self.assert_private("sat1", "sat1")

    def test_a_login_that_cannot_be_read_counts_as_a_non_admin(self):
        self.turn("alex", self.PRIVATE)
        self.model.seen.clear()
        self.turn("gone", "what did I just say?", cid="user_alex")
        self.assertNotIn(self.PRIVATE, str(self.model.seen))

    def test_a_non_admin_keeps_her_own_conversation(self):
        self.turn("robin", "first", cid="01JROBIN")
        self.turn("robin", "second", cid="01JROBIN")
        self.assertIn("first", self.said("01JROBIN"))
        self.assertIn("first", str(self.model.seen[-1]))
        self.turn("robin", "third", cid="user_robin")
        self.assertIn("third", self.said("user_robin"))

    def test_two_non_admins_cannot_read_each_other(self):
        self.turn("sam", self.PRIVATE, cid="01JSAM")
        self.model.seen.clear()
        self.turn("robin", "go on", cid="01JSAM")
        self.assertNotIn(self.PRIVATE, str(self.model.seen))

    def test_an_admin_is_not_limited_and_voice_keeps_working(self):
        self.turn("robin", "from robin", cid="01JROBIN")
        self.turn("alex", "from alex", cid="01JROBIN")
        self.assertIn("from alex", self.said("01JROBIN"))
        self.turn(None, "hello", device="sat3")
        self.turn(None, "again", device="sat3")
        self.assertIn("hello", self.said("sat3"))
        self.assertIn("again", self.said("sat3"))


class OfferYes(RealTurn):
    OFFER = {"action_data": {"domain": "light", "service": "turn_off", "entity_ids": ["light.lamp"]}, "pattern_key": "light.lamp:off:22"}

    def setUp(self):
        super().setUp()
        self.ran = []

        async def run(hass, data):
            self.ran.append(data)
            return True
        core = self.core._CORE
        for name, value in (("hass", self.h), ("autonomy_mgr", self.core.AutonomyManager()), ("pending_offer", None)):
            p = mock.patch.object(core, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(self.core, "_execute_action_data", run)
        p.start()
        self.addCleanup(p.stop)

    def yes(self, uid, offer=None):
        self.core._CORE.pending_offer = dict(offer or self.OFFER)
        return self.turn(uid, "yes")

    def test_a_non_admin_yes_runs_the_action_and_builds_no_autonomy(self):
        for _ in range(4):
            self.yes("robin")
        self.assertEqual(len(self.ran), 4)
        self.assertEqual(self.core._CORE.autonomy_mgr.list_grants(), [])

    def test_a_login_that_cannot_be_read_builds_no_autonomy(self):
        for _ in range(3):
            self.yes("gone")
        self.assertEqual(self.core._CORE.autonomy_mgr.list_grants(), [])

    def test_an_admin_yes_still_counts(self):
        for _ in range(3):
            self.yes("alex")
        grants = self.core._CORE.autonomy_mgr.list_grants()
        self.assertEqual([(g["pattern_key"], g["granted"]) for g in grants], [("light.lamp:off:22", True)])

    def test_voice_with_no_login_still_counts(self):
        for _ in range(3):
            self.yes(None)
        self.assertTrue(self.core._CORE.autonomy_mgr.list_grants())

    def test_a_non_admin_yes_cannot_run_what_is_no_household_action(self):
        for data in ({"domain": "homeassistant", "service": "restart", "entity_ids": ["light.lamp"]},
                     {"domain": "automation", "service": "trigger", "entity_ids": ["light.lamp"]},
                     {"domain": "light", "service": "turn_off", "entity_ids": ["light.lamp", "lock.front"]},
                     {"domain": "light", "service": "turn_off", "entity_ids": []}):
            self.yes("robin", {"action_data": data, "pattern_key": "x"})
        self.assertEqual(self.ran, [])
        self.assertEqual(self.core._CORE.autonomy_mgr.list_grants(), [])

    def test_the_same_offer_stays_open_for_an_admin_after_a_refusal(self):
        data = {"action_data": {"domain": "automation", "service": "trigger", "entity_ids": ["automation.x"]}, "pattern_key": "x"}
        self.yes("robin", data)
        self.assertEqual(self.ran, [])
        self.assertIsNotNone(self.core._CORE.pending_offer)
        self.turn("alex", "yes")
        self.assertEqual(len(self.ran), 1)


if __name__ == "__main__":
    unittest.main()
