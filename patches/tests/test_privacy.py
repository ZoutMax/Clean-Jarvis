"""Per-person isolation: chat seeding, recall, curated facts and the websocket reads."""
import base64, os, pathlib, sqlite3, tempfile, types, unittest
from unittest import mock

import jh_harness as H

database = H.load("database")
memory_thread = H.load("memory_thread")
memory = H.load("memory")
identity = H.load("identity")
knowledge = H.load("knowledge")
websocket = H.load("websocket")
recognition = H.load("recognition")
intrusion = H.load("intrusion")
pg = H.load("memory_pgvector")


class TempDb(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jh-db-")
        p = mock.patch.object(database, "DB_PATH", pathlib.Path(self.dir, "c.db"))
        p.start()
        self.addCleanup(p.stop)
        self.h = H.Hass()


class Seeding(TempDb):
    def test_old_database_gets_the_column_and_stays_ownerless(self):
        conn = sqlite3.connect(database.DB_PATH)
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
                     "device_id TEXT NOT NULL DEFAULT 'unknown', role TEXT NOT NULL, content TEXT NOT NULL)")
        conn.execute("INSERT INTO conversations (timestamp, device_id, role, content) VALUES ('2999-01-01', 'x', 'user', 'old row')")
        conn.commit()
        conn.close()
        database._JH_USER_COLUMN.clear()
        self.assertEqual(database.get_thread_messages(48, 12, "alex"), [])
        self.assertEqual(len(database.get_recent_messages(48, None, 12)), 1)

    def test_one_person_never_sees_another(self):
        database.save_message("user", "my secret plan", "cid_a", "alex")
        database.save_message("assistant", "noted", "cid_a", "alex")
        database.save_message("user", "hello from robin", "cid_b", "robin")
        got = H.run(memory_thread.load_recent(self.h, 48, 12, user_id="robin", device_id="new"))
        self.assertEqual([m["content"] for m in got], ["hello from robin"])

    def test_briefings_are_not_seeded(self):
        database.save_message("assistant", "[Briefing] at home: Alex", "briefing")
        database.save_message("assistant", "[Sentinel] motion", device_id="sentinel")
        self.assertEqual(H.run(memory_thread.load_recent(self.h, 48, 12, user_id="", device_id="briefing")), [])

    def test_anonymous_voice_only_gets_its_own_device(self):
        database.save_message("user", "kitchen speaker turn", "sat_kitchen")
        database.save_message("user", "bedroom speaker turn", "sat_bedroom")
        database.save_message("user", "logged in turn", "sat_kitchen", "alex")
        got = H.run(memory_thread.load_recent(self.h, 48, 12, user_id="", device_id="sat_kitchen"))
        self.assertEqual([m["content"] for m in got], ["kitchen speaker turn"])

    def test_no_user_and_no_device_gets_nothing(self):
        database.save_message("user", "anything", "x")
        self.assertEqual(H.run(memory_thread.load_recent(self.h, 48, 12)), [])

    def test_the_newest_turns_are_kept(self):
        for i in range(20):
            database.save_message("user", f"turn {i}", "c", "alex")
        got = H.run(memory_thread.load_recent(self.h, 48, 3, user_id="alex"))
        self.assertEqual([m["content"] for m in got], ["turn 17", "turn 18", "turn 19"])


class Recall(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jh-mem-")
        self.db = os.path.join(self.dir, "m.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(content, metadata, timestamp)")
        conn.commit()
        conn.close()
        for name, value in (("_DB_PATH", self.db), ("_fts_available", True), ("_pgvector_available", False),
                            ("_chromadb_available", False), ("_collection", None)):
            p = mock.patch.object(memory, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_punctuation_does_not_break_search(self):
        memory.store_memory("my favourite food is pasta", user_id="alex")
        for q in ("what's my favourite food?", "remember: pasta", "I like pasta-salad", "turn on the lamp. pasta"):
            got = memory.search_memory(q, user_id="alex")
            self.assertEqual(len(got), 1, q)
        self.assertEqual(memory.search_memory("?!", user_id="alex"), [])

    def test_a_quote_in_the_question_is_safe(self):
        memory.store_memory("my favourite food is pasta", user_id="alex")
        self.assertEqual(len(memory.search_memory('say "pasta', user_id="alex")), 1)

    def test_recall_is_per_person(self):
        memory.store_memory("the pasta recipe of alex", user_id="alex")
        self.assertEqual(memory.search_memory("pasta", user_id="robin"), [])
        self.assertEqual(len(memory.search_memory("pasta")), 1)


class Backends(unittest.TestCase):
    def test_chroma_filters_inside_the_backend(self):
        col = mock.MagicMock()
        col.query.return_value = {"documents": [["hello"]], "metadatas": [[{"role": "user"}]], "distances": [[0.1]]}
        with mock.patch.object(memory, "_chromadb_available", True), mock.patch.object(memory, "_collection", col), \
                mock.patch.object(memory, "_pgvector_available", False), mock.patch.object(memory, "_ensure_initialized", lambda: None):
            self.assertEqual(len(memory.search_memory("hello", user_id="robin")), 1)
            self.assertEqual(col.query.call_args.kwargs["where"], {"user_id": "robin"})
            memory.search_memory("hello", hours=2, user_id="robin")
            self.assertEqual(col.query.call_args.kwargs["where"]["$and"][1], {"user_id": "robin"})
            memory.search_memory("hello")
            self.assertIsNone(col.query.call_args.kwargs["where"])

    def _pg(self):
        cur = mock.MagicMock()
        cur.fetchall.return_value = []
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        return cur, conn

    def test_pgvector_filters_inside_the_backend(self):
        cur, conn = self._pg()
        with mock.patch.object(pg, "_ready", True), mock.patch.object(pg, "_embed", lambda t: [0.1]), mock.patch.object(pg, "_connect", lambda: conn):
            pg.search("hello", user_id="robin")
            sql, params = cur.execute.call_args.args
            self.assertIn("COALESCE(user_id, '') = %s", sql)
            self.assertIn("robin", params)
            self.assertEqual(sql.count("%s"), len(params))
            pg.search("hello", hours=2)
            sql, params = cur.execute.call_args.args
            self.assertNotIn("user_id", sql)
            self.assertEqual(sql.count("%s"), len(params))

    def test_pgvector_parameters_stay_in_query_order(self):
        cur, conn = self._pg()
        with mock.patch.object(pg, "_ready", True), mock.patch.object(pg, "_embed", lambda t: [0.1]), mock.patch.object(pg, "_connect", lambda: conn):
            pg.search("hello", hours=2, user_id="robin", k=7)
            sql, params = cur.execute.call_args.args
            self.assertLess(sql.index("ts >= %s"), sql.index("COALESCE"))
            self.assertEqual(params[2], "robin")
            self.assertEqual(params[-1], 7)

    def test_pgvector_store_writes_the_user(self):
        cur, conn = self._pg()
        with mock.patch.object(pg, "_ready", True), mock.patch.object(pg, "_embed", lambda t: [0.1]), \
                mock.patch.object(pg, "_connect", lambda: conn), mock.patch.object(pg, "_dim", None):
            pg.store("id1", "text", {"role": "user", "user_id": "robin"})
            sql, params = cur.execute.call_args.args
            self.assertIn("robin", params)
            self.assertEqual(sql.count("%s"), len(params))


class Subjects(unittest.TestCase):
    def setUp(self):
        self.h = H.Hass()
        self.h.states.set("person.alex", "home", friendly_name="Alex", user_id="u1")
        self.h.states.set("person.robin", "not_home", friendly_name="Robin", user_id="u2")

    def test_login_maps_to_person(self):
        self.assertEqual(identity.subject_for_user(self.h, "u2"), "robin")

    def test_login_without_person_has_a_private_subject(self):
        self.assertEqual(identity.subject_for_user(self.h, "u9"), "user_u9")

    def test_no_login_falls_back(self):
        self.assertEqual(identity.subject_for_user(self.h, ""), "")


class Reads(unittest.TestCase):
    def setUp(self):
        self.h = H.Hass()
        self.h.states.set("person.robin", "home", friendly_name="Robin", user_id="u2")
        self.admin = H.Connection(H.User("u1", True))
        self.member = H.Connection(H.User("u2", False))

    def test_memory_search_scope(self):
        with mock.patch.object(memory, "search_memory", return_value=[]) as sm:
            H.run(websocket.ws_search_memory(self.h, self.member, {"id": 1, "query": "x", "k": 9999}))
            self.assertEqual(sm.call_args.kwargs, {"k": 50, "user_id": "u2"})
            H.run(websocket.ws_search_memory(self.h, self.admin, {"id": 1, "query": "x", "k": 3}))
            self.assertEqual(sm.call_args.kwargs, {"k": 3, "user_id": None})

    def test_knowledge_scope(self):
        with mock.patch.object(knowledge, "all_facts", return_value=[{"key": "k"}]) as af, \
                mock.patch.object(knowledge, "stats", return_value={"by_subject": {"alex": 3}}):
            H.run(websocket.ws_get_knowledge(self.h, self.member, {"id": 1}))
            self.assertEqual(af.call_args.kwargs, {"subjects": ["robin", "household"]})
            self.assertEqual(self.member.sent[-1]["stats"]["by_subject"], {})
            af.reset_mock()
            H.run(websocket.ws_get_knowledge(self.h, self.member, {"id": 1, "subject": "alex"}))
            af.assert_not_called()
            self.assertEqual(self.member.sent[-1]["facts"], [])
            H.run(websocket.ws_get_knowledge(self.h, self.admin, {"id": 1, "subject": "alex"}))
            self.assertEqual(af.call_args.kwargs, {"subject": "alex"})
            self.assertEqual(self.admin.sent[-1]["stats"], {"by_subject": {"alex": 3}})

    def test_debug_log(self):
        with mock.patch.object(websocket, "_DEBUG_LOG", [{"text": "what alex asked"}]):
            H.run(websocket.ws_get_debug_log(self.h, self.member, {"id": 1}))
            H.run(websocket.ws_get_debug_log(self.h, self.admin, {"id": 1}))
        self.assertEqual(self.member.sent, [{"entries": []}])
        self.assertEqual(len(self.admin.sent[0]["entries"]), 1)

    def test_face_reference(self):
        with mock.patch.object(websocket, "_read_reference_data_url", return_value="data:x"):
            H.run(websocket.ws_face_reference(self.h, self.member, {"id": 1, "name": "alex"}))
            H.run(websocket.ws_face_reference(self.h, self.admin, {"id": 1, "name": "alex"}))
        self.assertEqual(self.member.sent, [{"image": None}])
        self.assertEqual(self.admin.sent, [{"image": "data:x"}])

    def test_faces_list_is_empty_for_members(self):
        with mock.patch.object(recognition, "recent_faces", return_value=[{"name": "Alex"}]) as rf:
            H.run(websocket.ws_faces(self.h, self.member, {"id": 1}))
        rf.assert_not_called()
        self.assertEqual(self.member.sent[0]["recent"], [])
        self.assertEqual(self.member.errors, [])

    def test_intrusion_log_and_status(self):
        snap = {"last_snapshot": {"url": "/local/x.jpg"}, "called_off": False}
        with mock.patch.object(intrusion, "async_load", mock.AsyncMock()), \
                mock.patch.object(intrusion, "get_log", return_value=[{"snapshot_url": "x"}]), \
                mock.patch.object(intrusion, "learning_summary", return_value={}), \
                mock.patch.object(intrusion, "status", return_value=snap):
            H.run(websocket.ws_intrusion(self.h, self.member, {"id": 1, "action": "log"}))
            H.run(websocket.ws_intrusion(self.h, self.member, {"id": 1, "action": "status"}))
            H.run(websocket.ws_intrusion(self.h, self.admin, {"id": 1, "action": "log"}))
            H.run(websocket.ws_intrusion(self.h, self.admin, {"id": 1, "action": "status"}))
        self.assertEqual(self.member.sent[0]["events"], [])
        self.assertIsNone(self.member.sent[1]["last_snapshot"])
        self.assertEqual(len(self.admin.sent[0]["events"]), 1)
        self.assertTrue(self.admin.sent[1]["last_snapshot"])


class PanelData(unittest.TestCase):
    def test_connection_details_are_blanked_before_the_result_is_sent(self):
        src = open(os.path.join(H.built_dir(), "websocket.py"), encoding="utf-8").read()
        i = src.index('for _jh_key in ("imap_host"')
        self.assertLess(i, src.index('_LOGGER.exception("ws_get_panel_data failed'))
        for key in ("imap_user", "llm_base_url", "ollama_base_url", "hazard_lat", "hazard_lon"):
            self.assertIn(f'"{key}"', src[i:i + 300])


def camera_module():
    H.install_stubs()
    return __import__("homeassistant.components.camera", fromlist=["x"])


class Photos(unittest.TestCase):
    def test_face_snapshot_lands_outside_www_and_goes_inline(self):
        h = H.Hass()
        folder = tempfile.mkdtemp(prefix="jh-face-")
        image = types.SimpleNamespace(content=b"\xff\xd8jpeg")
        recognition._FACE_SNAPSHOTS.clear()
        with mock.patch.object(camera_module(), "async_get_image", mock.AsyncMock(return_value=image), create=True), \
                mock.patch("jc.paths.config_path_str", lambda *p, hass=None: os.path.join(folder, *p)), \
                mock.patch.object(recognition, "_persist_async", mock.AsyncMock()), \
                mock.patch.object(recognition, "_maybe_auto_enroll_reference", mock.AsyncMock()):
            rec = H.run(recognition.capture_face_snapshot(h, "camera.door", "Alex", 90.0))
        self.assertTrue(rec)
        self.assertNotIn("www", rec["path"])
        self.assertFalse(rec["url"].startswith("/local"))
        rows = recognition.inline_snapshots([{"name": "Alex", "snapshot_url": rec["url"]},
                                             {"name": "Old", "snapshot_url": "/local/jarvis/faces/old.jpg"}])
        self.assertTrue(rows[0]["snapshot_url"].startswith("data:image/jpeg;base64,"))
        self.assertIsNone(rows[1]["snapshot_url"])

    def test_old_public_pins_are_not_restored(self):
        src = open(os.path.join(H.built_dir(), "recognition.py"), encoding="utf-8").read()
        self.assertIn('str(snap.get("url", "")).startswith(FACE_SNAPSHOT_URL_BASE)', src)
        # Audit 10: www/jarvis/faces is named once, as the folder the startup move empties. Nothing writes there.
        self.assertEqual(src.count('"www", "jarvis", "faces"'), 1)
        start = src.index("def _jh_faces_out_of_www")
        self.assertIn('"www", "jarvis", "faces"', src[start:src.index("\ndef ", start + 10)])

    def test_intrusion_name_cannot_be_guessed(self):
        h = H.Hass()
        image = types.SimpleNamespace(content=b"jpeg")
        folder = tempfile.mkdtemp(prefix="jh-intr-")
        with mock.patch.object(camera_module(), "async_get_image", mock.AsyncMock(return_value=image), create=True), \
                mock.patch.object(intrusion, "SNAPSHOT_DIR", folder), \
                mock.patch.object(intrusion, "has_hass_config_path", lambda x: False):
            a = H.run(intrusion.capture_snapshot(h, "camera.door"))
            b = H.run(intrusion.capture_snapshot(h, "camera.door"))
        self.assertNotEqual(a["url"], b["url"])
        self.assertGreater(len(os.path.basename(a["url"])), len("intrusion_door_1234567890.jpg") + 16)


if __name__ == "__main__":
    unittest.main()
