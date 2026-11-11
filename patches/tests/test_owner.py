"""Follow-ups and goals belong to the person who asked: they run with that person's limits and stay private."""
import json, os, pathlib, sqlite3, tempfile, types, unittest
from datetime import datetime, timedelta
from unittest import mock

import jh_harness as H

agent = H.load("agent")
tool_driver = H.load("tool_driver")
followups = H.load("followups")
goals = H.load("goals")
database = H.load("database")
websocket = H.load("websocket")
cognitive_core = H.load("cognitive_core")
identity = H.load("identity")
cognition = H.load("cognition")

CONFIG = {"honorific": "sir", "llm_provider": "x", "model": "m"}


class Script:
    name, model = "fake", "fake"

    def __init__(self, *turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, *a, **k):
        turn = self.turns.pop(0) if self.turns else "Done, sir."
        if isinstance(turn, list):
            return {"text": "", "tool_calls": [{"id": f"c{i}", "name": n, "args": a} for i, (n, a) in enumerate(turn)]}
        return {"text": turn, "tool_calls": []}


def turn(uid):
    return types.SimpleNamespace(text="x", language="en", device_id=None, conversation_id=None,
                                 context=types.SimpleNamespace(user_id=uid))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jh-owner-")
        self.h = H.Hass()
        self.h.auth.users.update(alex=H.User("alex", True), robin=H.User("robin", False))
        self.ran = []
        self.db = os.path.join(self.dir, "patterns.db")

        def read(name):
            async def run(hass, args):
                self.ran.append(name)
                return json.dumps({"success": True, "text": "newest mail"})
            return run

        fakes = {name: read(name) for name in ("read_email", "create_automation", "set_mode")}
        patches = [mock.patch.dict(agent._TOOL_MAP, fakes),
                   mock.patch.object(followups, "DB_PATH", self.db),
                   mock.patch.object(goals, "DB_PATH", self.db),
                   mock.patch.object(database, "DB_PATH", pathlib.Path(self.dir, "c.db")),
                   mock.patch.object(agent, "_jh_opt", lambda h, k: True),
                   mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=None)),
                   mock.patch.object(agent, "_floor_rooms", lambda h: []),
                   mock.patch.object(agent, "_load_learned", lambda: {}),
                   mock.patch.object(cognitive_core, "_provider_api_key", lambda c, p: "k")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        database._JH_USER_COLUMN.clear()
        self.addCleanup(agent._JH_CALLER.set, None)

    def tool(self, name, args, uid):
        agent._JH_CALLER.set(uid)
        return H.run(agent._execute_tool(self.h, name, args, None, turn(uid)))

    def due(self, script, module=followups, later=10):
        runner = cognitive_core._make_followup_runner(self.h, CONFIG)
        with mock.patch.object(agent, "_create_provider_with_fallback", mock.AsyncMock(return_value=script)):
            return H.run(module.async_process_due(self.h, CONFIG, runner=runner, now=datetime.now() + timedelta(minutes=later)))


class FollowUpLimits(Base):
    def test_non_admin_cannot_read_mail_through_a_follow_up(self):
        self.tool("schedule_followup", {"instruction": "read the newest email and report it", "delay_minutes": 1}, "robin")
        agent._JH_CALLER.set(None)
        out = self.due(Script([("read_email", {})], "That needs an administrator, sir."))
        self.assertEqual(self.ran, [])
        self.assertEqual([a["message"] for a in out], ["That needs an administrator, sir."])

    def test_admin_keeps_his_follow_ups(self):
        self.tool("schedule_followup", {"instruction": "read the newest email", "delay_minutes": 1}, "alex")
        agent._JH_CALLER.set(None)
        self.due(Script([("read_email", {})], "One new mail, sir."))
        self.assertEqual(self.ran, ["read_email"])

    def test_voice_follow_ups_work_as_voice_does(self):
        self.tool("schedule_followup", {"instruction": "read the newest email", "delay_minutes": 1}, None)
        self.due(Script([("read_email", {})], "One new mail, sir."))
        self.assertEqual(self.ran, ["read_email"])

    def test_a_row_from_before_the_owner_column_is_limited(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE followups (id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts TEXT NOT NULL, due_ts TEXT NOT NULL, "
                     "instruction TEXT NOT NULL, context TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', result TEXT DEFAULT '')")
        conn.execute("INSERT INTO followups (created_ts, due_ts, instruction) VALUES ('2026-01-01 00:00:00', '2026-01-01 00:00:00', 'read mail')")
        conn.commit()
        conn.close()
        self.due(Script([("read_email", {})], "That needs an administrator, sir."))
        self.assertEqual(self.ran, [])

    def test_a_follow_up_queued_by_a_follow_up_keeps_the_owner(self):
        self.tool("schedule_followup", {"instruction": "first", "delay_minutes": 1}, "robin")
        agent._JH_CALLER.set(None)
        self.due(Script([("schedule_followup", {"instruction": "second", "delay_minutes": 1})], "Queued, sir."))
        rows = followups.pending()
        self.assertEqual([(r["instruction"], r["user_id"]) for r in rows], [("second", "robin")])

    def test_one_owner_does_not_leak_into_the_next_row(self):
        self.tool("schedule_followup", {"instruction": "robin's", "delay_minutes": 1}, "robin")
        self.tool("schedule_followup", {"instruction": "alex's", "delay_minutes": 2}, "alex")
        agent._JH_CALLER.set(None)
        self.due(Script([("read_email", {})], "No, sir.", [("read_email", {})], "One mail, sir."))
        self.assertEqual(self.ran, ["read_email"])

    def test_a_deleted_login_is_limited(self):
        self.tool("schedule_followup", {"instruction": "read mail", "delay_minutes": 1}, "robin")
        del self.h.auth.users["robin"]
        agent._JH_CALLER.set(None)
        self.due(Script([("read_email", {})], "No, sir."))
        self.assertEqual(self.ran, [])


class GoalLimits(Base):
    def test_goal_runs_with_its_owners_limits_and_can_close_itself(self):
        self.tool("create_goal", {"title": "t", "outcome": "read the mail"}, "robin")
        gid = goals.active()[0]["id"]
        agent._JH_CALLER.set(None)
        self.due(Script([("read_email", {})], [("update_goal", {"goal_id": gid, "status": "failed", "result": "not allowed"})], "Closed, sir."),
                 goals)
        self.assertEqual(self.ran, [])
        self.assertEqual(goals.get(gid)["status"], "failed")

    def test_an_old_goal_with_no_owner_can_still_close_itself(self):
        goals.create("t", "o")
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE goals SET user_id=NULL")
        conn.commit()
        conn.close()
        gid = goals.active()[0]["id"]
        self.due(Script([("read_email", {})], [("update_goal", {"goal_id": gid, "status": "done", "result": "ok"})], "Closed, sir."), goals)
        self.assertEqual(self.ran, [])
        self.assertEqual(goals.get(gid)["status"], "done")


class Private(Base):
    def setUp(self):
        super().setUp()
        self.tool("schedule_followup", {"instruction": "Remind Alex about his doctor appointment", "delay_minutes": 5}, "alex")
        self.tool("schedule_followup", {"instruction": "Remind Robin about the keys", "delay_minutes": 5}, "robin")
        self.tool("schedule_followup", {"instruction": "voice reminder", "delay_minutes": 5}, None)

    def test_a_person_lists_only_their_own(self):
        out = self.tool("manage_followups", {}, "robin")
        self.assertIn("keys", out)
        self.assertNotIn("doctor", out)
        self.assertNotIn("voice reminder", out)

    def test_a_person_cannot_cancel_another_persons(self):
        other = [r for r in followups.pending() if r["user_id"] == "alex"][0]["id"]
        self.assertIn("No pending", self.tool("manage_followups", {"action": "cancel", "followup_id": other}, "robin"))
        self.assertEqual(len(followups.pending()), 3)

    def test_a_person_cancels_their_own(self):
        own = [r for r in followups.pending() if r["user_id"] == "robin"][0]["id"]
        self.assertIn("cancelled", self.tool("manage_followups", {"action": "cancel", "followup_id": own}, "robin"))

    def test_an_admin_keeps_the_full_list(self):
        out = self.tool("manage_followups", {}, "alex")
        for word in ("doctor", "keys", "voice reminder"):
            self.assertIn(word, out)
        other = [r for r in followups.pending() if r["user_id"] == "robin"][0]["id"]
        self.assertIn("cancelled", self.tool("manage_followups", {"action": "cancel", "followup_id": other}, "alex"))

    def log(self, user):
        c = H.Connection(user)
        H.run(websocket.ws_get_activity_log(self.h, c, {"id": 1, "hours": 24, "limit": 50}))
        return " | ".join(str(e) for e in c.sent[0]["entries"])

    def test_activity_log_keeps_each_persons_lines_private(self):
        agent._JH_CALLER.set(None)
        self.due(Script("Done for Alex, sir.", "Done for Robin, sir.", "Done by voice, sir."))
        database.save_activity(category="door", message="Front door opened")
        database.save_activity(category="followup", message="Follow-up #9 ran: an old line with no owner")
        database.save_activity(category="goal", message="Goal #9 engaged: an old line")
        member = self.log(H.User("robin", False))
        self.assertIn("keys", member)
        self.assertIn("Front door opened", member)
        for word in ("doctor", "voice reminder", "old line"):
            self.assertNotIn(word, member)
        admin = self.log(H.User("alex", True))
        for word in ("doctor", "keys", "voice reminder", "old line", "Front door"):
            self.assertIn(word, admin)

    def test_an_old_activity_table_gets_the_column(self):
        conn = sqlite3.connect(database.DB_PATH)
        conn.execute("DROP TABLE IF EXISTS activity_log")
        conn.execute("CREATE TABLE activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, entity_id TEXT, "
                     "category TEXT, urgency TEXT, message TEXT, was_spoken INTEGER DEFAULT 0, source TEXT)")
        conn.commit()
        conn.close()
        database._JH_USER_COLUMN.clear()
        database.save_activity(category="followup", message="new line", user_id="robin")
        self.assertEqual([r["user_id"] for r in database.get_recent_activity()], ["robin"])


class PrivateGoals(Base):
    def setUp(self):
        super().setUp()
        self.tool("create_goal", {"title": "Alex surprise party", "outcome": "plan it"}, "alex")
        self.tool("create_goal", {"title": "Robin keys", "outcome": "find them"}, "robin")
        self.alexs = [g for g in goals.active() if g["user_id"] == "alex"][0]["id"]

    def test_list_status_cancel_and_update_stay_private(self):
        self.assertNotIn("surprise", self.tool("manage_goals", {}, "robin"))
        self.assertNotIn("surprise", self.tool("manage_goals", {"action": "status", "goal_id": self.alexs}, "robin"))
        self.assertIn("No active goal", self.tool("manage_goals", {"action": "cancel", "goal_id": self.alexs}, "robin"))
        self.assertIn("no goal", self.tool("update_goal", {"goal_id": self.alexs, "status": "failed"}, "robin"))
        self.assertEqual(goals.get(self.alexs)["status"], "active")

    def test_admin_sees_both(self):
        out = self.tool("manage_goals", {}, "alex")
        self.assertIn("surprise", out)
        self.assertIn("keys", out)

    def test_panel_goal_rows_name_their_owner(self):
        rows = websocket._get_goals()
        self.assertEqual({r["owner"] for r in rows}, {"alex", "robin"})

    def test_panel_data_filter_is_in_the_handler(self):
        with open(os.path.join(H.built_dir(), "websocket.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('result["goals"] = [g for g in result.get("goals", []) if g.get("owner") == connection.user.id]', text)

    def test_status_tool_hides_goals_and_beliefs_from_a_member(self):
        status = {"self_model": {"commitments": ["goal: Alex surprise party", "situation: leak"]},
                  "beliefs": {"count": 3, "sample": [{"proposition": "Alex likes tea"}]}}
        analyzer = types.SimpleNamespace(get_stats=lambda: {})
        pa = H.load("pattern_analyzer")
        model = types.SimpleNamespace(report=lambda: "", to_dict=lambda: status["self_model"])
        with mock.patch.object(cognitive_core, "status", lambda: dict(status)), mock.patch.object(pa, "get_analyzer", lambda: analyzer), \
                mock.patch.object(agent, "_build_self_model_snapshot", lambda h: model):
            out = json.loads(self.tool("cognitive_status", {}, "robin"))
        self.assertEqual(out["self_model"]["commitments"], ["situation: leak"])
        self.assertEqual(out.get("beliefs", {}).get("sample", []), [])


class UsualTimes(Base):
    def setUp(self):
        super().setUp()
        self.h.states.set("person.robin", "home", friendly_name="Robin", user_id="robin", device_trackers=["device_tracker.robin_phone"])
        self.h.states.set("person.alex", "home", friendly_name="Alex", user_id="alex")
        self.rows = [{"entity_id": "person.robin", "name": "Robin", "usual_depart": "08:00"},
                     {"entity_id": "device_tracker.robin_phone", "name": "Phone", "usual_depart": "08:01"},
                     {"entity_id": "person.alex", "name": "Alex", "usual_depart": "07:00"}]
        p = mock.patch.object(cognition, "presence_schedule", lambda hass=None: list(self.rows))
        p.start()
        self.addCleanup(p.stop)

    def test_a_member_sees_only_their_own_routine(self):
        out = json.loads(self.tool("usual_times", {}, "robin"))
        self.assertEqual({r["entity_id"] for r in out["routines"]}, {"person.robin", "device_tracker.robin_phone"})

    def test_asking_for_someone_else_finds_nothing(self):
        out = json.loads(self.tool("usual_times", {"who": "alex"}, "robin"))
        self.assertFalse(out["learned"])

    def test_admin_and_voice_see_everyone(self):
        self.assertEqual(len(json.loads(self.tool("usual_times", {}, "alex"))["routines"]), 3)
        self.assertEqual(len(json.loads(self.tool("usual_times", {}, None))["routines"]), 3)


class FalseAlarm(Base):
    def test_a_household_login_can_call_off_a_false_alarm(self):
        ran = []

        async def fake(hass, args):
            ran.append(1)
            return "{}"
        with mock.patch.dict(agent._TOOL_MAP, {"dismiss_intrusion": fake, "acknowledge_alert": fake}):
            self.tool("dismiss_intrusion", {}, "robin")
            self.tool("acknowledge_alert", {}, "robin")
        self.assertEqual(len(ran), 2)


class Ollama(Base):
    def status(self, user):
        c = H.Connection(user)
        embeddings = H.load("embeddings")
        with mock.patch.object(embeddings, "_ollama_base", lambda: "http://192.0.2.1:11434"), \
                mock.patch.object(embeddings, "vector_count", lambda: 0), mock.patch.object(embeddings, "_model", lambda: "m"):
            H.run(websocket.ws_semantic_search(self.h, c, {"id": 1, "action": "status"}))
        return c.sent[0]

    def test_address_is_for_admins_only(self):
        member = self.status(H.User("robin", False))
        self.assertEqual(member["base"], "")
        self.assertTrue(member["ollama_configured"])
        self.assertEqual(self.status(H.User("alex", True))["base"], "http://192.0.2.1:11434")


class RootCause(Base):
    """The real rca.analyze on real sqlite, through the real tool and websocket handler."""
    def setUp(self):
        super().setUp()
        rca = H.load("rca")
        self.rca = rca
        self.tool("schedule_followup", {"instruction": "check my doctor appointment", "delay_minutes": 1}, "alex")
        self.due(Script("Your test result is back, sir."))
        con = sqlite3.connect(str(database.DB_PATH))
        self.stamp = con.execute("select timestamp from activity_log order by id desc").fetchone()[0]
        for category, owner, text in (("goal", "alex", "Goal closed: surprise party lamp"), ("followup", "robin", "Follow-up ran: robin own lamp"),
                                      ("followup", "", "Follow-up ran: voice secret lamp"), ("action", "", "Turned on the lamp")):
            con.execute("insert into activity_log (timestamp, entity_id, category, urgency, message, was_spoken, source, user_id) values (?,?,?,?,?,?,?,?)",
                        (self.stamp, "", category, "low", text, 0, "x", owner))
        con.commit()
        con.close()
        self.pdb = os.path.join(self.dir, "p2.db")
        con = sqlite3.connect(self.pdb)
        con.execute("create table state_changes(id integer primary key, timestamp text, entity_id text, domain text, old_state text, new_state text,"
                    " area_id text, triggered_by text, hour int)")
        con.execute("create table commands(id integer primary key, timestamp text, text text, person text)")
        later = (datetime.fromisoformat(self.stamp) + timedelta(seconds=5)).isoformat(sep=" ")
        con.execute("insert into state_changes(timestamp,entity_id,domain,old_state,new_state,hour) values(?,?,?,?,?,?)", (later, "light.lamp", "light", "off", "on", 3))
        con.execute("insert into commands(timestamp,text,person) values(?,?,?)", (self.stamp, "remind me about the lamp and my doctor", "Alex"))
        con.commit()
        con.close()
        p = mock.patch.object(rca.analyze, "__defaults__", (None, rca.DEFAULT_WINDOW_SECS, self.pdb, str(database.DB_PATH)))
        p.start()
        self.addCleanup(p.stop)

    SECRETS = ("doctor", "test result", "surprise party", "voice secret")

    def ws(self, user):
        cn = H.Connection(user)
        H.run(websocket.ws_root_cause(self.h, cn, {"id": 1, "entity_id": "light.lamp"}))
        return json.dumps(cn.sent[0])

    def test_the_tool_hides_other_peoples_lines_from_a_non_admin(self):
        out = self.tool("root_cause", {"entity_id": "light.lamp"}, "robin")
        for secret in self.SECRETS:
            self.assertNotIn(secret, out)
        self.assertIn("light.lamp", out)

    def test_the_websocket_hides_them_too_and_keeps_the_rest(self):
        out = self.ws(H.User("robin", False))
        for secret in self.SECRETS:
            self.assertNotIn(secret, out)
        self.assertIn("robin own lamp", out)
        self.assertIn("Turned on the lamp", out)
        self.assertIn("Alex", out)

    def test_an_admin_still_sees_everything(self):
        out = self.ws(H.User("alex", True))
        for secret in self.SECRETS:
            self.assertIn(secret, out)
        out = self.tool("root_cause", {"entity_id": "light.lamp"}, "alex")
        self.assertIn("surprise party", out)

    def test_an_unknown_login_is_limited(self):
        out = self.tool("root_cause", {"entity_id": "light.lamp"}, "ghost")
        for secret in self.SECRETS:
            self.assertNotIn(secret, out)

    def test_nothing_recorded_is_no_crash_for_a_limited_login(self):
        out = self.rca.analyze("light.none", viewer="robin")
        self.assertEqual(out["entity_id"], "light.none")


if __name__ == "__main__":
    unittest.main()
