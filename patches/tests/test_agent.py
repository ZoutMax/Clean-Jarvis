"""agent.py: replies are checked against what tools really did, and admin tools need an admin login."""
import json, types, unittest
from unittest import mock

import jh_harness as H

agent = H.load("agent")
tool_driver = H.load("tool_driver")

ADMIN_ONLY = ("read_email", "create_automation", "set_mode", "manage_autonomy",
              "approve_suggestion", "dismiss_suggestion", "ignore_entity", "unignore_entity", "ingest_documents",
              "security_audit", "system_diagnostics", "wellbeing_context")


# Ordinary questions and the kind of answer a model gives. None of these is a claim about this house.
ORDINARY = (
    ("what does a thermostat do?", "A thermostat senses the room temperature and switches the heating on or off to hold it steady."),
    ("what should i do if i am locked out?", "If you are locked out, call a locksmith, sir. I'll be here if you need anything."),
    ("good evening jarvis", "Good evening, sir. I'll be here if you need anything."),
    ("tell me about the alarm clock history", "The alarm clock goes back to ancient Greece, and Levi Hutchins built a mechanical one in 1787."),
    ("how do smart locks work?", "A smart lock turns the bolt with a small motor. The door is locked when the bolt is thrown."),
    ("how do i arm an alarm system?", "You enter your code on the keypad. The alarm is armed after the exit delay."),
    ("what does armed away mean?", "Armed away means every sensor is watched while nobody is home."),
    ("should i lock the front door at night?", "Yes, sir, the front door should be locked at night."),
    ("why do front doors open inward?", "Front doors usually open inward so the hinges stay inside, sir."),
    ("what temperature should a thermostat be set to at night?", "The thermostat is best set to about 17 degrees at night."),
    ("how does a garage door opener work?", "Let me explain. A motor pulls the door along a rail, and a spring carries most of the weight."),
    ("tell me a story", "I'll start with a knight. The castle gate was closed for the winter, and nobody came."),
    ("what is a perimeter?", "A perimeter is the outer boundary of an area."),
    ("why is my smoke alarm chirping?", "A smoke alarm chirps when the battery is low, sir."),
    ("thanks jarvis", "You are welcome, sir. I'll be here."),
    ("can you look after the doors?", "I can check the doors whenever you like, sir."),
    ("can you help me", "Of course, sir. I will do my best."),
    ("who invented the modern lock?", "Linus Yale patented the pin tumbler lock in 1861."),
    ("is it safe to leave the back door unlocked?", "It is not wise, sir. An unlocked back door invites trouble."),
    ("what do you do at night?", "I keep watch, sir, and I'll be here if you need anything."),
    ("is https secure?", "Yes, it is secure, sir."),
    ("what would a butler say?", "\"I'll check the door at once, madam,\" is the classic line."),
    ("what is the best alarm for a flat?", "A wired alarm is the most reliable, and I'll happily compare models with you."),
    ("how is your day?", "Quite well, sir. I'm looking forward to the evening."),
    ("is a thermostat a heater?", "I'll set the record straight: a thermostat is not a heater."),
    ("what is the meaning of life?", "I have turned this over in my mind and the answer is 42."),
    ("where do thermostats go?", "The thermostat is on the wall in most flats."),
    ("what happens in the film?", "In the film, the door is locked and the hero is trapped."),
    ("how do i stop a draught?", "The gate of the castle is open to visitors on Sundays, sir, but a door strip is cheaper."),
    ("tell me a joke right away", "Right away, sir. Why did the thermostat go to therapy? It had too many issues with temperature."),
)
# The review's ordinary pairs, round 2. Many of these read like a claim about a house, and none is a device command.
REVIEW_ORDINARY = (
    ("are the police in the uk armed?", "No, the police in the UK are not armed, sir."),
    ("are swiss guards armed?", "Yes, the Swiss Guards are armed, sir."),
    ("is the transfer window open?", "Yes, the transfer window is open, sir."),
    ("is the white house closed today?", "The White House is closed, sir."),
    ("what's the wedding date?", "The wedding date has been set for June."),
    ("when is the election?", "The date has been set for 12 May."),
    ("write me a haiku", "I've created a haiku for you:\nAutumn moonlight,\na worm digs silently\ninto the chestnut."),
    ("what is 17 times 23?", "Let me check: 17 times 23 is 391."),
    ("what is a 12 percent tip on 50 euro?", "I've run the numbers: 6 euro, sir."),
    ("how old are you?", "I turned one this year, sir."),
    ("what's the capital of portugal?", "Let me check my notes. Lisbon is the capital of Portugal."),
    ("tell me a story", "I'll start the story with a dragon. It slept all winter."),
    ("recommend a film", "I've checked my favourites: try Casablanca, sir."),
    ("how are you", "I am checking my diary, sir, and all seems well."),
    ("what do you do all day?", "I monitor the house, sir."),
    ("what can you do?", "I can dim lights, lock doors and run scenes."),
    ("do you sleep?", "I'll monitor the night as ever, sir."),
    ("explain the tides", "I'll check my physics: the moon pulls the sea, sir."),
    ("translate gate to dutch", "The gate is closed translates to de poort is dicht."),
    ("how do you say door in dutch", "Door in Dutch is deur, sir."),
    ("what time do i get up?", "Your alarm is at 7, sir."),
    ("is my iphone locked to a carrier?", "Yes, it's locked, sir."),
    ("is the file read only?", "Yes, it's locked, sir."),
    ("is the pharmacy open?", "Yes, the pharmacy is open, sir."),
    ("is that bank safe?", "Yes, the bank is secure, sir."),
    ("are you sure?", "Yes, sir, that's locked in."),
    ("what's the mood?", "The stage has been set, sir."),
    ("did you enjoy that?", "I've enjoyed it, sir."),
    ("good morning", "Good morning, sir. Your home is secure."),
    ("who won?", "The record has been set, sir."),
    ("how was the match?", "The home side's perimeter was closed well, sir."),
    ("do you remember my name?", "Yes, sir. I'll remember it."),
    ("remind me what a kettle does", "I'll remind you: a kettle boils water, sir."),
    ("hello", "I'll be right here, sir."),
    ("what's your favourite colour?", "I've set my heart on blue, sir."),
    ("can you sing?", "I'll start a song, sir: la la la."),
    ("thanks", "Done, sir."),
    ("what's that word", "Done means finished, sir."),
    ("tell me about yourself", "I started in a lab, sir."),
    ("what does stand by mean", "Stand by means wait, sir."),
    ("what do you say when i ask?", "At once, sir."),
    ("what do you say at the door?", "On it, sir."),
)
# Chat that begins like a command and names no device. It stands, whatever the reply says.
CHAT_LIKE_COMMANDS = (
    ("start a story", "Once upon a time, a door closed."),
    ("start a story", "Once upon a time there was a princess who lived on a hill above the sea."),
    ("stop", "As you wish, sir."),
    ("stop talking", "As you wish, sir."),
    ("stop it jarvis", "Right away, sir."),
    ("stop that", "Very well, sir."),
    ("set me a riddle", "What is opened but never closed?"),
    ("set me a riddle", "A riddle: the more you take, the more you leave behind."),
    ("set the scene for a dinner party", "Candles lit, music low, sir."),
    ("close your eyes", "They are closed, sir."),
    ("turn this into dutch: good night", "Goedenacht, sir."),
    ("can you turn that into a poem", "Roses are red, the door is closed."),
    ("can you open up about your day", "It started well, sir."),
    ("can you set a good example", "Consider it done, sir."),
    ("could you start thinking about dinner", "Right away, sir."),
    ("start over", "Certainly, sir. Where shall we start?"),
    ("enable dark humour", "Consider it done, sir."),
    ("disable the jokes", "As you wish, sir. No more jokes."),
    ("turn around", "I have no feet, sir."),
    ("lock in the plan", "Consider it done, sir."),
    ("close the conversation", "Good night, sir."),
    ("stop being so formal", "Very well, I shall be plain. Done."),
    ("switch to dutch", "Natuurlijk, ik spreek nu Nederlands."),
    ("switch to english please", "As you wish, sir."),
    ("set the tone: be funny", "Done, sir. I am now hilarious."),
)
# The review's false claims: a device command, no tool, and a reply that says it happened.
FALSE_CLAIMS = (
    ("turn on the lamp", "The lamp is on, sir."),
    ("turn on the lamp", "It's on, sir."),
    ("turn on the kitchen light", "The kitchen light is on."),
    ("turn off the heating", "The heating is off, sir."),
    ("switch off the tv", "The TV is off, sir."),
    ("turn off all the lights", "All the lights are off, sir."),
    ("close the blinds", "The blinds are down, sir."),
    ("kill the lights", "The lights are off, sir."),
    ("shut the front door", "Done, sir."),
    ("doe de lamp aan", "Klaar, de lamp is aan."),
    ("zet de verwarming uit", "De verwarming staat uit, sir."),
    ("put the lamp on", "The lamp is on, sir."),
    ("make it warmer", "Warmer it is, sir."),
    ("ok jarvis, turn on the lamp", "The lamp is on, sir."),
    ("can you turn on the lamp?", "Of course, sir."),
    ("turn on the lamp", "Certainly, sir, the lamp should now be glowing warmly in the corner where you left it."),
    ("turn on the lamp", "The lamp is on, sir. Anything else?"),
)
# The review's statements, round 4: a report about a device, never an order to it, plus two from the brief.
STATEMENTS = (
    "the alarm went off", "the alarm just went off", "the alarm woke me up", "the music woke me up", "the lights went off",
    "the lights just went off", "the lights came on", "the heating broke down", "the heater broke down", "the fan fell off",
    "the door slammed shut", "the garage door got stuck open", "someone left the lights on", "somebody left the door open",
    "the kids left the tv on", "my speakers blew up", "my dad's door got kicked open", "the tv keeps turning off",
    "the heating keeps turning on", "the lights keep turning on", "the alarm keeps going off", "the fan keeps slowing down",
    "the door keeps blowing open", "the music was too loud but it calmed down", "love how the lights came on",
    "nice that the heating came on", "heat wave coming up", "big music festival coming up", "sorry the lights went off",
    "oops left the lights on", "just got home and the heating was off", "just got back, door was open", "windows open", "door open",
    "front door closed", "heating off", "music up", "de deur viel dicht", "de lamp ging uit", "de verwarming sloeg af", "het alarm ging af",
    "de muziek stond aan", "mijn lamp viel om", "de stekker ging eruit", "ons licht ging uit", "gisteren ging de lamp uit",
    "die Tür ging auf", "das Licht ging aus",
    "the lamp in the hall is off again", "my heater went off",
)
# The review's replies that open with a claim and close with a question or a word like can't.
CLAIM_THEN_MORE = (
    ("turn on the lamp", "Done, sir. Want me to dim it as well?"),
    ("turn on the lamp", "The lamp is on, sir. Should I also close the blinds?"),
    ("turn on the lamp", "The lamp is on, sir. Need anything else?"),
    ("turn on the lamp", "Done. Want anything else?"),
    ("turn on the lamp", "The lamp is on, sir. What next?"),
    ("turn off the kitchen light", "The kitchen light is off, sir. Shall we call it a night?"),
    ("lock the front door", "The front door is locked, sir. Is that all?"),
    ("turn on the lamp", "The lamp is on, sir. There's no need to get up."),
    ("turn on the lamp", "The lamp is on, sir, I can't wait to see it."),
    ("lock the front door", "The front door is locked, sir. You cannot be too careful."),
    ("turn off the kitchen light", "The kitchen light is off. I don't have anything else to report, sir."),
    ("turn on the lamp", "Lamp is on now, sir; it was offline earlier."),
    ("zet de lamp aan", "De lamp staat aan. Wil je nog iets?"),
)


class Script:
    """A fake model: each entry is reply text, or a list of tool calls as (name, args)."""
    name, model = "fake", "fake"

    def __init__(self, *turns):
        self.turns, self.seen = list(turns), []

    def chat(self, messages, tools=None, *a, **k):
        self.seen.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else "out of script"
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, list):
            return {"text": "", "tool_calls": [{"id": f"c{i}", "name": n, "args": a} for i, (n, a) in enumerate(turn)]}
        return {"text": turn, "tool_calls": []}


def user_input(text, uid=None):
    return types.SimpleNamespace(text=text, language="en", device_id=None, conversation_id=None,
                                 context=types.SimpleNamespace(user_id=uid))


class Loop(unittest.TestCase):
    """Runs the real run_agent loop. Only the model and the tools themselves are fake."""
    def setUp(self):
        self.h = H.Hass()
        self.ran = []
        self.results = {}

        def tool(name):
            async def run(hass, args):
                self.ran.append(name)
                return self.results.get(name, json.dumps({"success": True}))
            return run

        fake_map = {name: tool(name) for name in agent._TOOL_MAP}
        for p in (mock.patch.dict(agent._TOOL_MAP, fake_map),
                  mock.patch.object(agent, "_jh_opt", lambda h, k: True),
                  mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=None)),
                  mock.patch.object(agent, "_floor_rooms", lambda h: []),
                  mock.patch.object(agent, "_load_learned", lambda: {})):
            p.start()
            self.addCleanup(p.stop)

    def ask(self, script, text, uid=None, **extra):
        with mock.patch.object(agent, "_create_provider_with_fallback", mock.AsyncMock(return_value=script)):
            return H.run(agent.run_agent(self.h, messages=[{"role": "user", "content": text}], persona="p", provider_name="x",
                                         api_key="k", model="m", user_input=user_input(text, uid),
                                         config={"honorific": "sir"}, **extra))

    def nudges(self, script):
        return sum(1 for m in script.seen[-1] if m["role"] == "user" and "you called no tool" in m["content"])

    def test_after_a_real_action_a_promise_is_not_sent_back(self):
        s = Script([("schedule_followup", {})], "Reminder set, I'll tell you at 8.")
        self.assertEqual(self.ask(s, "set a reminder for 8"), "Reminder set, I'll tell you at 8.")
        self.assertEqual(self.ran, ["schedule_followup"])
        self.assertEqual(len(s.seen), 2)

    def test_after_tools_the_nudge_never_says_no_tool_was_called(self):
        s = Script([("get_entity_state", {})], "Done, sir.", "That lamp cannot be reached, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "That lamp cannot be reached, sir.")
        self.assertEqual(self.nudges(s), 0)

    def test_read_only_tool_is_no_action(self):
        s = Script([("web_research", {})], "Done, sir.", "Done, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "That did not go through, sir. Nothing was changed.")

    def test_failed_tool_is_no_action(self):
        self.results["control_device"] = json.dumps({"error": "Failed: offline"})
        s = Script([("control_device", {})], "Done, sir.", "Done, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "That did not go through, sir. Nothing was changed.")

    def test_awaiting_confirmation_keeps_the_question(self):
        self.results["control_device"] = json.dumps({"status": "awaiting_confirmation"})
        s = Script([("control_device", {})], "Unlocking needs confirmation. Shall I proceed?")
        self.assertEqual(self.ask(s, "unlock the door"), "Unlocking needs confirmation. Shall I proceed?")
        self.assertEqual(len(s.seen), 2)

    def test_unconfirmed_device_is_not_done(self):
        self.results["control_device"] = json.dumps({"success": False, "confirmed": False, "sent": True})
        s = Script([("control_device", {})], "Done, sir.", "Done, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "The command was sent, sir, but the device has not confirmed it.")

    def test_real_control_passes_straight_through(self):
        s = Script([("control_device", {})], "Done, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "Done, sir.")
        self.assertEqual(len(s.seen), 2)

    def test_honest_refusal_survives(self):
        s = Script([("search_entities", {})], "No such device, sir.")
        self.assertEqual(self.ask(s, "turn on the sauna"), "No such device, sir.")

    def test_clarifying_question_survives(self):
        s = Script("Which lamp do you mean, sir?")
        self.assertEqual(self.ask(s, "turn on the lamp"), "Which lamp do you mean, sir?")
        self.assertEqual(len(s.seen), 1)

    def test_chat_that_looks_like_a_command_survives(self):
        s = Script("My apologies, sir.", "My apologies, sir.")
        self.assertEqual(self.ask(s, "stop repeating yourself"), "My apologies, sir.")

    def test_state_read_backs_a_state_answer(self):
        s = Script([("get_entity_state", {})], "The front door is locked.")
        self.assertEqual(self.ask(s, "is the front door locked?"), "The front door is locked.")

    def test_already_needs_a_state_read(self):
        s = Script("I have already turned on the lamp.", "I have already turned on the lamp.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "That did not go through, sir. Nothing was changed.")
        s = Script([("get_entity_state", {})], "It is already on, sir.", "It is already on, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "It is already on, sir.")

    def test_let_me_know_is_no_promise(self):
        s = Script("It is a fine day. Let me know.")
        self.assertEqual(self.ask(s, "what is the capital of Spain"), "It is a fine day.")
        self.assertEqual(len(s.seen), 1)

    def test_ordinary_replies_are_never_replaced_or_sent_back(self):
        for question, reply in ORDINARY:
            s = Script(reply, reply)
            self.assertEqual(self.ask(s, question), reply, question)
            self.assertEqual(len(s.seen), 1, question)
            self.assertEqual(self.ran, [], question)

    def test_ordinary_replies_survive_a_model_without_tools(self):
        for question, reply in ORDINARY:
            async def loop(hass, client, **kw):
                return {"text": reply}
            with mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=False)), \
                    mock.patch.object(tool_driver, "run_tool_loop", loop):
                self.assertEqual(self.ask(Script(), question), reply, question)

    def test_chat_that_starts_like_a_command_keeps_a_long_answer(self):
        text = "A thermostat measures the room temperature, and the heating is turned on when it drops below the target you set."
        self.assertEqual(self.ask(Script(text, text), "start by explaining what a thermostat does"), text)

    def test_a_long_answer_with_a_promise_in_it_keeps_its_content(self):
        text = ("Let me check my memory. The first electronic thermostat came from Warren Johnson in 1883, and his company "
                "grew into one of the largest makers of building controls in the world, which is still trading today, sir.")
        s = Script(text, text)
        self.assertEqual(self.ask(s, "who made the first thermostat?"), text)
        self.assertEqual(len(s.seen), 1)

    def test_a_short_done_for_a_command_is_still_replaced(self):
        self.assertEqual(self.ask(Script("Turned on, sir.", "Turned on, sir."), "turn on the lamp"),
                         "That did not go through, sir. Nothing was changed.")

    def test_honest_words_after_a_failed_tool_stand(self):
        self.results["control_device"] = json.dumps({"error": "Failed: offline"})
        text = "The bulb cannot be reached, sir."
        self.assertEqual(self.ask(Script([("control_device", {})], text, text), "turn on the lamp"), text)

    def test_the_word_already_in_a_sentence_is_no_claim(self):
        text = "As you already know, sir, a thermostat holds a steady temperature."
        self.assertEqual(self.ask(Script(text, text), "start with what a thermostat is"), text)

    def test_switch_off_restores_old_behaviour(self):
        with mock.patch.object(agent, "_jh_opt", lambda h, k: k != "jh_strict_actions"):
            self.assertEqual(self.ask(Script("Done, sir."), "turn on the lamp"), "Done, sir.")

    def test_summary_after_the_last_step_is_checked(self):
        s = Script([("web_research", {})], "All done, the lamp is on.")
        self.assertEqual(self.ask(s, "turn on the lamp", max_iterations=1), "That did not go through, sir. Nothing was changed.")

    def test_failed_summary_makes_no_false_claim(self):
        s = Script([("web_research", {})], RuntimeError("model gone"))
        self.assertEqual(self.ask(s, "turn on the lamp", max_iterations=1), "That did not go through, sir. Nothing was changed.")
        s = Script([("control_device", {})], RuntimeError("model gone"))
        self.assertIn("could not confirm", self.ask(s, "turn on the lamp", max_iterations=1))

    def test_non_tool_model_gets_the_same_check(self):
        async def loop(hass, client, **kw):
            return {"text": "I turned on the lamp."}
        with mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tool_driver, "run_tool_loop", loop):
            self.assertEqual(self.ask(Script(), "turn on the lamp"), "That did not go through, sir. Nothing was changed.")

    def test_non_tool_model_with_a_real_action_passes(self):
        async def loop(hass, client, handlers, **kw):
            await handlers["control_device"]({})
            return {"text": "Done, sir. Let me know."}
        with mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tool_driver, "run_tool_loop", loop):
            self.assertEqual(self.ask(Script(), "turn on the lamp"), "Done, sir.")

    def test_non_admin_can_chat_and_control(self):
        self.h.auth.users["robin"] = H.User("robin", False)
        s = Script([("control_device", {})], "Done, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp", uid="robin"), "Done, sir.")
        self.assertEqual(self.ran, ["control_device"])

    def test_non_admin_is_refused_admin_tools_and_told_the_truth(self):
        self.h.auth.users["robin"] = H.User("robin", False)
        s = Script([("read_email", {})], "That needs an administrator, sir, and I cannot do it.")
        self.ask(s, "read my email", uid="robin")
        self.assertEqual(self.ran, [])
        self.assertIn("administrator", s.seen[-1][-1]["content"])

    def test_delegated_work_keeps_the_login(self):
        self.h.auth.users["robin"] = H.User("robin", False)
        agent._JH_CALLER.set("robin")
        out = H.run(agent._execute_tool(self.h, "create_automation", {}, None, None))
        agent._JH_CALLER.set(None)
        self.assertIn("Not allowed", out)
        self.assertEqual(self.ran, [])


class TruthRules(Loop):
    """The round 3 rule set, through the real run_agent. The fake model repeats itself after the retry, the worst case."""
    OK = json.dumps({"success": True})
    FAIL = json.dumps({"error": "Failed: offline"})
    NOT_DONE = "That did not go through, sir. Nothing was changed."

    def house(self):
        """A real looking house, so names can count."""
        for eid, state, name in (("light.lamp", "off", "Lamp"), ("lock.front", "locked", "Front Door"), ("climate.hall", "heat", "Thermostat"),
                                 ("cover.blinds", "open", "Blinds"), ("switch.heater", "off", "Office Heater"), ("media_player.tv", "off", "TV"),
                                 ("light.kitchen", "off", "Kitchen"), ("sensor.conversation_count", "1", "Conversation Count")):
            self.h.states.set(eid, state, friendly_name=name)
        self.h.registry_areas = [types.SimpleNamespace(id="kitchen", name="Kitchen", aliases=[])]

    def stands(self, pairs):
        for question, reply in pairs:
            s = Script(reply, reply)
            self.assertEqual(self.ask(s, question), reply, question)
            self.assertEqual(len(s.seen), 1, question)

    def test_ordinary_pairs_stand_in_an_empty_house(self):
        self.stands(ORDINARY + REVIEW_ORDINARY + CHAT_LIKE_COMMANDS)

    def test_ordinary_pairs_stand_in_a_house_with_those_devices(self):
        self.house()
        self.stands(ORDINARY + REVIEW_ORDINARY + CHAT_LIKE_COMMANDS)

    def test_ordinary_pairs_stand_on_a_model_without_tools(self):
        for question, reply in REVIEW_ORDINARY + CHAT_LIKE_COMMANDS:
            async def loop(hass, client, **kw):
                return {"text": reply}
            with mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=False)), \
                    mock.patch.object(tool_driver, "run_tool_loop", loop):
                self.assertEqual(self.ask(Script(), question), reply, question)

    def test_the_sentence_from_the_live_system_cannot_claim_without_a_tool(self):
        self.h.states.set("light.stick", "off", friendly_name="Stick")
        for text in ("Switch, stick, green.", "switch stick green", "Switch stick green?"):
            reply = "Done, sir. Stick is green."
            s = Script(reply, reply)
            self.assertEqual(self.ask(s, text), self.NOT_DONE, text)
            self.assertEqual(self.nudges(s), 1, text)

    def test_the_same_sentence_with_a_real_tool_stands(self):
        self.h.states.set("light.stick", "off", friendly_name="Stick")
        s = Script([("control_device", {"entity_id": "light.stick", "action": "turn_on"})], "Done, sir. Stick is green.")
        self.assertEqual(self.ask(s, "Switch, stick, green."), "Done, sir. Stick is green.")

    def test_a_device_said_by_ear_cannot_claim_without_a_tool(self):
        self.h.states.set("light.disc", "off", friendly_name="Disc")
        s = Script("Done, sir.", "Done, sir.")
        self.assertEqual(self.ask(s, "Switch on the disk."), self.NOT_DONE)

    def test_search_entities_puts_a_sound_alike_above_a_vague_match(self):
        self.h.states.set("light.dusk", "off", friendly_name="Dusk")
        self.h.states.set("light.disc", "off", friendly_name="Disc")
        rows = json.loads(H.run(agent._exec_search_entities(self.h, {"query": "disk"})))
        self.assertEqual(rows[0]["friendly_name"], "Disc")
        self.assertEqual(rows[0]["score"], 64)

    def test_every_false_claim_gets_one_retry_then_the_honest_line(self):
        for house in (False, True):
            if house:
                self.house()
            for command, reply in FALSE_CLAIMS:
                s = Script(reply, reply)
                self.assertEqual(self.ask(s, command), self.NOT_DONE, (command, reply, house))
                self.assertEqual(len(s.seen), 2, command)
                self.assertEqual(self.nudges(s), 1, command)

    def test_false_claims_on_a_model_without_tools(self):
        for command, reply in FALSE_CLAIMS:
            async def loop(hass, client, **kw):
                return {"text": reply}
            with mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=False)), \
                    mock.patch.object(tool_driver, "run_tool_loop", loop):
                self.assertEqual(self.ask(Script(), command), self.NOT_DONE, command)

    def test_a_retry_that_calls_the_tool_ends_well(self):
        s = Script("The lamp is on, sir.", [("control_device", {})], "The lamp is on, sir.")
        self.assertEqual(self.ask(s, "turn on the lamp"), "The lamp is on, sir.")
        self.assertEqual(self.ran, ["control_device"])

    def test_a_question_or_a_stated_failure_stands_on_a_command(self):
        for reply in ("Which lamp, sir?", "Do you mean the hall lamp, sir?", "There is no lamp by that name, sir.", "I cannot reach it, sir.",
                      "Sorry sir, that failed.", "Dat is niet gelukt, sir."):
            s = Script(reply, reply)
            self.assertEqual(self.ask(s, "turn on the lamp"), reply, reply)
            self.assertEqual(len(s.seen), 1, reply)

    def test_a_device_name_in_any_language_makes_it_a_command(self):
        self.h.states.set("light.x", "off", friendly_name="Schemerlamp")
        self.h.states.set("switch.y", "off", friendly_name="Ampli")
        for command in ("doe de schemerlamp aan", "zet ampli uit", "allume ampli", "accendi la schemerlamp", "schalte schemerlamp an", "kun je de ampli aan zetten"):
            self.assertEqual(self.ask(Script("Done.", "Done."), command), self.NOT_DONE, command)

    def test_a_device_named_in_chat_is_no_command(self):
        self.house()
        self.stands((("what a lovely lamp", "Thank you, sir."), ("i love the kitchen", "A fine room, sir."), ("the lamp is on", "Noted, sir."),
                     ("in the kitchen", "Very good, sir."), ("tell me a story about the kitchen", "Once upon a time, sir."),
                     ("stop talking about the kitchen", "As you wish, sir."), ("i left the lamp on", "So you did, sir."),
                     ("don't turn on the lamp", "Very well, sir."), ("what is on the tv tonight", "A film, sir."),
                     ("is de deur dicht", "Ja, sir."), ("staat de lamp aan", "Ja, sir."), ("write a poem about the blinds", "They fall, sir."),
                     ("close the conversation", "Good night, sir."), ("set the mood in the kitchen", "Candles, sir."),
                     ("turn left at the kitchen", "If you say so, sir."), ("start with the kitchen", "Very well, sir.")))

    def test_a_name_with_a_possessive_s_is_not_that_device(self):
        self.h.states.set("climate.sun", "off", friendly_name="Sun")
        self.stands((("sun's up", "So it is, sir."),))
        self.assertEqual(self.ask(Script("Done.", "Done."), "turn sun off"), self.NOT_DONE)

    def test_mixed_tools_cannot_hide_a_failure(self):
        cases = (
            ("turn on the lamp and the heater", [("control_device", {"entity_id": "light.lamp"}), ("bulk_control", {})],
             {"bulk_control": json.dumps({"count": 0})}, "Both are on, sir.", "The group command did not go through"),
            ("turn on the lamp and the heater", [("execute_plan", {})],
             {"execute_plan": json.dumps({"succeeded": 1, "failed": 1, "results": [{"step": 1, "ok": True, "description": "Lamp on"},
                                                                                 {"step": 2, "ok": False, "description": "Heater on", "error": "x"}]})},
             "Done, sir. Both are on.", "Heater on did not go through"),
            ("lock the door and turn on the lamp", [("remember", {}), ("control_device", {"entity_id": "lock.front"})],
             {"control_device": json.dumps({"success": False, "confirmed": False, "sent": True})}, "The door is locked, sir.",
             "Front Door has not confirmed it"),
            ("turn on the lamp", [("schedule_followup", {}), ("control_device", {"entity_id": "light.lamp"})], {"control_device": self.FAIL},
             "Done, sir.", "Lamp did not go through"),
            ("turn off the lights", [("bulk_control", {})], {"bulk_control": json.dumps({"count": 2, "total": 3})}, "All off, sir.",
             "1 of the 3 devices did not go through"),
        )
        self.house()
        self.h.states.set("lock.front", "unlocked", friendly_name="Front Door")
        for command, calls, results, reply, named in cases:
            self.results = results
            s = Script(calls, reply, reply)
            out = self.ask(s, command)
            self.assertTrue(out.startswith("Only part of that went through, sir."), out)
            self.assertIn(named, out)
            self.assertNotIn(reply, out)
            self.assertEqual(len(s.seen), 3, command)

    def test_a_reply_that_names_the_failure_stands(self):
        self.results = {"bulk_control": json.dumps({"count": 0})}
        reply = "The lamp is on, sir, but the rest could not be reached."
        s = Script([("control_device", {}), ("bulk_control", {})], reply)
        self.assertEqual(self.ask(s, "turn on the lamp and the heater"), reply)

    def test_a_failure_the_model_put_right_is_no_failure(self):
        calls = iter((json.dumps({"error": "Entity 'light.lmp' not found"}), self.OK))

        async def control(hass, args):
            return next(calls)
        with mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            s = Script([("control_device", {"entity_id": "light.lmp"})], [("control_device", {"entity_id": "light.lamp"})], "Done, sir.")
            self.assertEqual(self.ask(s, "turn on the lamp"), "Done, sir.")

    def test_a_second_try_on_the_same_device_clears_it(self):
        calls = iter((self.FAIL, self.OK))

        async def control(hass, args):
            return next(calls)
        with mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            s = Script([("control_device", {"entity_id": "light.lamp"})], [("control_device", {"entity_id": "light.lamp"})], "Done, sir.")
            self.assertEqual(self.ask(s, "turn on the lamp"), "Done, sir.")

    def test_only_unconfirmed_and_failed_with_no_success_says_both(self):
        results = iter((self.FAIL, json.dumps({"success": False, "confirmed": False, "sent": True})))

        async def control(hass, args):
            return next(results)
        self.house()
        with mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            s = Script([("control_device", {"entity_id": "light.lamp"}), ("control_device", {"entity_id": "cover.blinds"})], "Done.", "Done.")
            self.assertEqual(self.ask(s, "turn on the lamp and open the blinds"), "Lamp did not go through and Blinds has not confirmed it, sir.")

    def test_on_ordinary_chat_a_failed_acting_tool_is_added_and_nothing_is_replaced(self):
        self.results = {"remember": self.FAIL}
        s = Script([("remember", {})], "Noted, sir.")
        self.assertEqual(self.ask(s, "green tea is my favourite"), "Noted, sir. The note did not go through, sir.")
        self.assertEqual(len(s.seen), 2)

    def test_a_failed_read_on_a_question_is_not_answered(self):
        for question, tool, reply in (("is the front door locked?", "get_entity_state", "Yes, it's locked, sir."),
                                      ("what's the temperature", "weather_forecast", "It is 18 degrees."),
                                      ("is de deur op slot", "get_entity_state", "Ja, sir.")):
            self.results = {tool: self.FAIL}
            s = Script([(tool, {})], reply)
            self.assertEqual(self.ask(s, question), "I could not read that, sir.", question)
            self.assertEqual(len(s.seen), 2, question)

    def test_a_failed_read_with_an_honest_reply_or_a_later_good_read_stands(self):
        self.results = {"get_entity_state": self.FAIL}
        self.assertEqual(self.ask(Script([("get_entity_state", {})], "I could not reach the lock, sir."), "is the door locked?"), "I could not reach the lock, sir.")
        reads = iter((self.FAIL, json.dumps({"state": "locked"})))

        async def read(hass, args):
            return next(reads)
        with mock.patch.dict(agent._TOOL_MAP, {"get_entity_state": read}):
            s = Script([("get_entity_state", {})], [("get_entity_state", {})], "Locked, sir.")
            self.assertEqual(self.ask(s, "is the door locked?"), "Locked, sir.")

    def test_a_failed_world_search_leaves_a_general_answer_alone(self):
        self.results = {"web_research": self.FAIL}
        self.assertEqual(self.ask(Script([("web_research", {})], "Lisbon, sir."), "what's the capital of portugal?"), "Lisbon, sir.")

    def test_a_failed_read_on_a_statement_is_left_alone(self):
        self.results = {"get_entity_state": self.FAIL}
        self.assertEqual(self.ask(Script([("get_entity_state", {})], "Noted, sir."), "good evening"), "Noted, sir.")

    def test_statements_about_devices_stand(self):
        self.stands((text, "Noted, sir. That sounds irritating.") for text in STATEMENTS)
        self.house()
        self.h.states.set("light.hall", "off", friendly_name="Hall")
        self.h.states.set("switch.door", "off", friendly_name="Door")
        self.stands((text, "Noted, sir. That sounds irritating.") for text in STATEMENTS)

    def test_a_bare_device_word_is_no_order_any_more(self):
        """The known limit of the strict rule: "lights off" reads like "heating off", so a claim there is not caught."""
        self.stands((("lights off", "Lights are off, sir."),))

    def test_a_claim_is_judged_by_its_first_sentence(self):
        self.house()
        for command, reply in CLAIM_THEN_MORE:
            s = Script(reply, reply)
            self.assertEqual(self.ask(s, command), self.NOT_DONE, reply)
            self.assertEqual(self.nudges(s), 1, reply)

    def test_a_closing_question_hides_no_failed_tool(self):
        unconfirmed = json.dumps({"success": False, "confirmed": False, "sent": True, "new_state": "off"})
        for result, line in ((self.FAIL, self.NOT_DONE), (unconfirmed, "The command was sent, sir, but the device has not confirmed it.")):
            self.results = {"control_device": result}
            for command, reply in CLAIM_THEN_MORE:
                s = Script([("control_device", {"entity_id": "light.lamp", "action": "turn_on"})], reply, reply)
                self.assertEqual(self.ask(s, command), line, reply)

    def test_a_failure_stated_first_stands_with_more_after_it(self):
        for reply in ("I cannot reach the lamp, sir. Is it plugged in?", "The lamp is offline, sir. The rest of the house is fine.", "Sorry sir, that failed. The fan is on."):
            s = Script(reply, reply)
            self.assertEqual(self.ask(s, "turn on the lamp"), reply)
            self.assertEqual(len(s.seen), 1)

    def wait_for(self, replies, command="unlock the front door"):
        self.house()
        self.results = {"control_device": json.dumps({"status": "awaiting_confirmation", "entity_id": "lock.front"})}
        return [self.ask(Script([("control_device", {"entity_id": "lock.front", "action": "unlock"})], r), command) for r in replies]

    def test_a_claim_while_a_tool_waits_is_replaced_by_the_waiting_line(self):
        line = "Front Door is waiting for your confirmation, sir. Nothing has been changed yet."
        claims = ("The front door is unlocked, sir.", "Done, sir.", "The door is unlocked, sir. Shall I proceed?", "Confirmed, the door is unlocked.")
        self.assertEqual(self.wait_for(claims), [line] * len(claims))

    def test_an_honest_reply_while_a_tool_waits_stands(self):
        honest = ("Unlocking needs confirmation, sir. Shall I proceed?", "Shall I unlock the front door, sir?", "Please confirm, sir.", "Waiting for your word, sir.")
        self.assertEqual(self.wait_for(honest), list(honest))

    def test_a_claim_with_one_device_done_and_one_waiting(self):
        self.house()
        results = iter((self.OK, json.dumps({"status": "awaiting_confirmation"})))

        async def control(hass, args):
            return next(results)
        with mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            s = Script([("control_device", {"entity_id": "light.lamp"}), ("control_device", {"entity_id": "lock.front"})], "All done, sir.")
            self.assertEqual(self.ask(s, "turn on the lamp and unlock the front door"),
                             "Only part of that went through, sir. Front Door is waiting for your confirmation, sir.")

    def test_only_a_device_tool_backs_a_device_claim(self):
        for tool in ("remember", "schedule_followup", "create_goal", "set_mode", "acknowledge_alert", "dismiss_intrusion", "manage_followups"):
            s = Script([(tool, {})], "The lamp is on, sir.", "The lamp is on, sir.")
            self.assertEqual(self.ask(s, "turn on the lamp"), "That did not reach any device, sir.", tool)
            self.assertEqual(self.nudges(s), 0, tool)
            self.assertEqual(len(s.seen), 3, tool)

    def test_device_tools_back_a_device_claim(self):
        for tool in ("control_device", "bulk_control", "execute_plan", "run_scene_or_script"):
            self.results = {"bulk_control": json.dumps({"count": 1}), "execute_plan": json.dumps({"succeeded": 1})}
            self.assertEqual(self.ask(Script([(tool, {})], "The lamp is on, sir."), "turn on the lamp"), "The lamp is on, sir.", tool)

    def test_home_assistant_tools_back_a_claim_unless_they_keep_a_timer_or_a_list(self):
        for name, device in (("HassTurnOn", True), ("HassLightSet", True), ("movie_night", True), ("HassStartTimer", False), ("HassListAddItem", False),
                             ("HassBroadcast", False), ("GetLiveContext", False)):
            st = agent._jh_new_state()
            agent._jh_note_tool(st, name, json.dumps({"response_type": "action_done", "data": {"success": [{"name": "x"}], "failed": []}}))
            self.assertEqual(st["device"], device, name)

    def delegated(self, *child, result=None, reply="The lamp is on, sir."):
        """The real delegate_task path: the parent calls it, a real sub-agent run happens, then the parent replies twice at most."""
        if result:
            self.results = {"control_device": result}
        s = Script([("delegate_task", {"objective": "turn on the lamp", "profile": "friday"})], *child, reply, reply)
        with mock.patch.object(agent, "_resolve_profile", lambda p: ({"control_device", "get_entity_state"}, 3, None)):
            return self.ask(s, "turn on the lamp"), s

    def test_a_delegate_that_touched_no_device_backs_nothing(self):
        out, s = self.delegated("I could not do it from here.")
        self.assertEqual(out, self.NOT_DONE)
        out, s = self.delegated([("get_entity_state", {})], "It is off.")
        self.assertEqual(out, self.NOT_DONE)

    def test_a_delegate_passes_on_what_its_tools_did(self):
        ctl = [("control_device", {"entity_id": "light.lamp", "action": "turn_on"})]
        out, s = self.delegated(ctl, "Done.")
        self.assertEqual(out, "The lamp is on, sir.")
        self.assertEqual(self.ran, ["control_device"])
        self.house()
        out, s = self.delegated(ctl, "Done.", "Done.", result=self.FAIL)
        self.assertEqual(out, self.NOT_DONE)
        out, s = self.delegated(ctl, "Done.", result=json.dumps({"status": "awaiting_confirmation"}))
        self.assertEqual(out, "Lamp is waiting for your confirmation, sir. Nothing has been changed yet.")

    def two_devices(self, *rounds, reply="The lamp and the fan are on, sir."):
        self.house()

        async def control(hass, args):
            if args["entity_id"] in ("fan.lamp", "light.lmp"):
                return json.dumps({"error": f"Entity '{args['entity_id']}' not found"})
            return json.dumps({"success": True, "confirmed": True, "entity_id": args["entity_id"]})
        calls = [[("control_device", {"entity_id": e, "action": "turn_on"}) for e in r] for r in rounds]
        with mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            return self.ask(Script(*calls, reply, reply), "turn on the lamp and the fan")

    def test_a_wrong_id_counts_unless_a_later_call_put_that_request_right(self):
        part = "Only part of that went through, sir. Fan.lamp did not go through, sir."
        self.assertEqual(self.two_devices(["light.lamp", "fan.lamp"]), part)            # the review's case
        self.assertEqual(self.two_devices(["fan.lamp", "light.lamp"]), part)            # the same round cannot be a correction
        self.assertEqual(self.two_devices(["light.lamp"], ["fan.lamp"]), part)          # no call after it
        self.assertEqual(self.two_devices(["fan.lamp"], ["cover.blinds"]), part)        # another device, not a correction
        ok = "The lamp and the fan are on, sir."
        self.assertEqual(self.two_devices(["light.lamp", "fan.lamp"], ["fan.bathroom"]), ok)
        self.assertEqual(self.two_devices(["light.lmp"], ["light.lamp"]), ok)

    def test_a_correction_counts_on_a_model_without_tools_too(self):
        async def control(hass, args):
            return json.dumps({"error": "Entity not found"} if args["entity_id"] == "light.lmp" else {"success": True})

        async def loop(hass, client, handlers, **kw):
            await handlers["control_device"]({"entity_id": "light.lmp"})
            await handlers["control_device"]({"entity_id": "light.lamp"})
            return {"text": "The lamp is on, sir."}
        with mock.patch.object(tool_driver, "supports_tools", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tool_driver, "run_tool_loop", loop), mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            self.assertEqual(self.ask(Script(), "turn on the lamp"), "The lamp is on, sir.")

    def test_a_correction_needs_the_same_action(self):
        self.house()

        async def control(hass, args):
            return json.dumps({"error": "Entity not found"} if args["entity_id"] == "light.lmp" else {"success": True})
        with mock.patch.dict(agent._TOOL_MAP, {"control_device": control}):
            s = Script([("control_device", {"entity_id": "light.lmp", "action": "turn_on"})], [("control_device", {"entity_id": "light.lamp", "action": "turn_off"})],
                       "Done, sir.", "Done, sir.")
            self.assertIn("Light.lmp did not go through", self.ask(s, "turn on the lamp"))

    def test_a_home_assistant_result_with_failed_targets_is_partly_failed(self):
        partly = json.dumps({"response_type": "action_done", "data": {"success": [{"name": "Lamp"}], "failed": [{"name": "Fan"}, {"name": "Spot"}], "targets": []}})
        with mock.patch.object(agent, "_execute_tool", mock.AsyncMock(return_value=partly)):
            s = Script([("HassTurnOn", {"area": "kitchen"})], "All kitchen lights are on, sir.", "All kitchen lights are on, sir.")
            self.assertEqual(self.ask(s, "turn on the kitchen lights"), "Only part of that went through, sir. Fan and Spot did not go through, sir.")
        st = agent._jh_new_state()
        agent._jh_note_tool(st, "HassTurnOn", json.dumps({"response_type": "action_done", "data": {"success": [], "failed": [{"name": "Fan"}]}}))
        self.assertFalse(st["device"])
        self.assertTrue(st["bad"])

    def test_the_old_state_words_are_gone(self):
        for name in ("_COMMAND_RE", "_JH_PROMISE_RE", "_JH_STATE_RES", "_JH_DONE_RE", "_JH_LEAD_RE", "_JH_SHORT_RE", "_JH_NOTE_RE", "_jh_promises", "_jh_states", "_jh_claims"):
            self.assertFalse(hasattr(agent, name), name)


class Gate(unittest.TestCase):
    def setUp(self):
        self.h = H.Hass()
        self.h.auth.users.update(admin=H.User("admin", True), robin=H.User("robin", False))
        self.ran = []

        async def fake(hass, args):
            self.ran.append("ran")
            return "{}"
        p = mock.patch.dict(agent._TOOL_MAP, {name: fake for name in agent._TOOL_MAP})
        p.start()
        self.addCleanup(p.stop)
        agent._JH_CALLER.set(None)

    def call(self, name, uid):
        return H.run(agent._execute_tool(self.h, name, {}, None, user_input("x", uid)))

    def test_admin_only_tools(self):
        for name in ADMIN_ONLY:
            self.ran.clear()
            self.assertIn("Not allowed", self.call(name, "robin"), name)
            self.assertEqual(self.ran, [], name)
            self.call(name, "admin")
            self.assertEqual(self.ran, ["ran"], name)

    def test_household_tools_work_for_a_member(self):
        for name in agent._JH_HOUSEHOLD_TOOLS - {"delegate_task"}:
            self.ran.clear()
            self.call(name, "robin")
            self.assertEqual(self.ran, ["ran"], name)

    def test_voice_with_no_login_is_not_limited(self):
        self.call("read_email", None)
        self.assertEqual(self.ran, ["ran"])

    def test_unknown_user_is_refused(self):
        self.assertIn("Not allowed", self.call("read_email", "ghost"))

    def test_every_tool_is_sorted_into_a_list(self):
        new = set(agent._TOOL_MAP) - set(agent._JH_HOUSEHOLD_TOOLS) - set(ADMIN_ONLY)
        self.assertEqual(new, set())


class Plan(unittest.TestCase):
    """The real execute_plan, through the real tool gate. The fake hass records every service call."""
    def setUp(self):
        self.h = H.Hass()
        self.h.auth.users.update(admin=H.User("admin", True), robin=H.User("robin", False))
        self.h.states.set("light.lamp", "off", friendly_name="Lamp")
        self.h.states.set("lock.front", "locked", friendly_name="Front")
        self.h.states.set("update.core", "on", friendly_name="Core")
        self.seen = []
        real_call = self.h.services.async_call

        async def call(domain, service, data=None, **kw):
            self.seen.append(kw.get("context"))
            await real_call(domain, service, data, **kw)
        self.h.services.async_call = call
        policy = H.load("policy")
        p = mock.patch.object(policy, "confirm_gate", mock.AsyncMock(return_value=(True, "")))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(agent._JH_CALLER.set, None)

    def plan(self, uid, *steps):
        agent._JH_CALLER.set(uid)
        out = H.run(agent._execute_tool(self.h, "execute_plan", {"goal": "g", "steps": list(steps)}, None, None))
        return json.loads(out)

    def step(self, domain, service, entity_id="light.lamp", **extra):
        return {"domain": domain, "service": service, "entity_id": entity_id, "service_data": extra}

    def test_voice_with_no_login_needs_no_context_class(self):
        """Context is only imported for a request with a login, so voice runs even where it cannot be imported."""
        core = __import__("homeassistant.core", fromlist=["x"])
        with mock.patch.object(core, "Context", side_effect=AssertionError("not for voice"), create=True):
            out = self.plan(None, self.step("light", "turn_on"))
        self.assertEqual(out["succeeded"], 1)
        self.assertEqual(self.seen, [None])

    def test_a_login_travels_in_the_context(self):
        core = __import__("homeassistant.core", fromlist=["x"])
        marks = []
        with mock.patch.object(core, "Context", lambda user_id=None: marks.append(user_id) or user_id, create=True):
            out = self.plan("admin", self.step("light", "turn_on"))
        self.assertEqual(out["succeeded"], 1)
        self.assertEqual(marks, ["admin"])
        self.assertEqual(self.seen, ["admin"])

    def test_non_admin_cannot_reach_admin_services(self):
        for domain, service in (("jarvis", "create_automation"), ("jarvis", "forget"), ("jarvis", "backup"),
                                ("homeassistant", "restart"), ("hassio", "host_shutdown"), ("shell_command", "x"),
                                ("notify", "mobile_app"), ("recorder", "purge")):
            out = self.plan("robin", self.step(domain, service))
            self.assertEqual(out["succeeded"], 0, domain)
            self.assertIn("Not allowed", out["results"][0]["error"], domain)
        self.assertEqual(self.h.calls, [])

    def test_non_admin_cannot_use_other_services_of_a_device(self):
        out = self.plan("robin", self.step("update", "install", "update.core"),
                        self.step("lock", "turn_off", "light.lamp"), self.step("homeassistant", "turn_off", "lock.front"))
        self.assertEqual(out["succeeded"], 0)
        self.assertEqual(self.h.calls, [])

    def test_non_admin_keeps_normal_device_plans_and_the_call_carries_the_login(self):
        out = self.plan("robin", self.step("light", "turn_on", brightness_pct=40), self.step("homeassistant", "toggle"),
                        self.step("lock", "lock", "lock.front"))
        self.assertEqual(out["succeeded"], 3)
        self.assertEqual([c[:2] for c in self.h.calls], [("light", "turn_on"), ("homeassistant", "toggle"), ("lock", "lock")])
        self.assertEqual(self.h.calls[0][2], {"brightness_pct": 40, "entity_id": "light.lamp"})
        self.assertEqual(len(self.seen), 3)
        self.assertTrue(all(c is not None for c in self.seen))

    def test_a_step_cannot_widen_its_target(self):
        for uid in ("robin", "admin"):
            self.h.calls.clear()
            self.plan(uid, {"domain": "light", "service": "turn_on", "entity_id": "light.lamp",
                            "service_data": {"entity_id": "light.other", "area_id": "kitchen", "brightness_pct": 40}})
            self.assertEqual(self.h.calls[0][2], {"brightness_pct": 40, "entity_id": "light.lamp"}, uid)

    def test_admin_and_voice_are_not_limited(self):
        self.assertEqual(self.plan("admin", self.step("jarvis", "backup"))["succeeded"], 1)
        self.assertEqual(self.plan(None, self.step("jarvis", "backup"))["succeeded"], 1)
        self.assertIsNone(self.seen[-1])

    def test_unknown_login_is_limited(self):
        self.assertEqual(self.plan("ghost", self.step("jarvis", "backup"))["succeeded"], 0)

    def test_refused_plan_is_a_failure_for_the_reply_check(self):
        st = agent._jh_new_state()
        agent._jh_note_tool(st, "execute_plan", json.dumps(self.plan("robin", self.step("jarvis", "backup"))))
        self.assertTrue(agent._jh_failures(st))
        self.assertFalse(st["changed"])

    def test_non_admin_keeps_ordinary_household_services(self):
        for eid, state in (("button.bell", "unknown"), ("input_button.b", "unknown"), ("select.mode", "a"), ("input_select.m", "a"),
                           ("number.n", "1"), ("input_number.n", "1"), ("media_player.tv", "on"), ("remote.tv", "on"),
                           ("script.go", "off"), ("climate.heat", "heat"), ("scene.movie", "unknown"), ("group.all", "on")):
            self.h.states.set(eid, state, friendly_name=eid)
        steps = (("button", "press", "button.bell"), ("input_button", "press", "input_button.b"), ("select", "select_option", "select.mode"),
                 ("input_select", "select_option", "input_select.m"), ("number", "set_value", "number.n"),
                 ("input_number", "set_value", "input_number.n"), ("media_player", "select_source", "media_player.tv"),
                 ("media_player", "play_media", "media_player.tv"), ("remote", "send_command", "remote.tv"), ("script", "go", "script.go"),
                 ("script", "turn_on", "script.go"), ("homeassistant", "turn_on", "media_player.tv"), ("homeassistant", "turn_off", "climate.heat"),
                 ("homeassistant", "turn_on", "scene.movie"), ("homeassistant", "turn_on", "script.go"), ("homeassistant", "turn_off", "group.all"))
        for domain, service, eid in steps:
            self.h.calls.clear()
            out = self.plan("robin", self.step(domain, service, eid))
            self.assertEqual(out["succeeded"], 1, (domain, service, out))
            self.assertEqual(self.h.calls[0][:2], (domain, service))

    def test_a_service_of_another_domain_or_an_admin_verb_stays_refused(self):
        self.h.states.set("button.bell", "unknown", friendly_name="Bell")
        self.h.states.set("script.go", "off", friendly_name="Go")
        self.h.states.set("script.reload", "off", friendly_name="Reload")
        self.h.states.set("automation.a", "on", friendly_name="A")
        self.h.states.set("alarm_control_panel.a", "disarmed", friendly_name="Alarm")
        for step in (self.step("button", "press", "light.lamp"), self.step("script", "reload", "script.go"), self.step("script", "reload", "script.reload"),
                     self.step("script", "other", "script.go"), self.step("automation", "trigger", "automation.a"),
                     self.step("homeassistant", "reload_all", "light.lamp"), self.step("homeassistant", "turn_on", "lock.front"),
                     self.step("alarm_control_panel", "alarm_disarm", "alarm_control_panel.a")):
            self.assertEqual(self.plan("robin", step)["succeeded"], 0, step)
        self.assertEqual(self.h.calls, [])

    def test_the_refusal_says_what_is_true(self):
        error = self.plan("robin", self.step("jarvis", "backup"))["results"][0]["error"]
        self.assertIn("Not allowed", error)
        self.assertIn("Nothing was changed", error)
        self.assertNotIn("administrator", error)


class Remember(unittest.TestCase):
    def setUp(self):
        self.h = H.Hass()
        self.h.states.set("person.robin", "home", friendly_name="Robin", user_id="robin")
        self.h.states.set("person.alex", "home", friendly_name="Alex", user_id="alex")
        self.saved = {}
        self.facts = []
        knowledge = H.load("knowledge")
        identity = H.load("identity")
        for p in (mock.patch.object(agent, "_load_learned", lambda: {}),
                  mock.patch.object(agent, "_save_learned", lambda d: self.saved.update(d)),
                  mock.patch.object(knowledge, "remember", lambda n, v, **k: self.facts.append(k["subject"])),
                  mock.patch.object(identity, "resolve_subject", lambda hass: "alex")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(agent._JH_CALLER.set, None)

    def test_logged_in_preference_is_private(self):
        agent._JH_CALLER.set("robin")
        H.run(agent._exec_remember(self.h, {"key": "preference", "name": "tea", "value": "green"}))
        self.assertEqual(self.facts, ["robin"])
        self.assertEqual(self.saved, {})

    def test_voice_keeps_the_presence_guess(self):
        agent._JH_CALLER.set(None)
        H.run(agent._exec_remember(self.h, {"key": "preference", "name": "tea", "value": "green"}))
        self.assertEqual(self.facts, ["alex"])
        self.assertIn("preference", self.saved)

    def test_aliases_stay_shared(self):
        """An admin's alias is for the whole house. A limited login cannot write one (audit 10): it reaches every prompt."""
        self.h.auth.users.update(alex=H.User("alex", True), robin=H.User("robin", False))
        agent._JH_CALLER.set("alex")
        H.run(agent._exec_remember(self.h, {"key": "alias", "name": "big lamp", "value": "light.x"}))
        self.assertEqual(self.saved["alias"], {"big lamp": "light.x"})
        self.saved.clear()
        agent._JH_CALLER.set("robin")
        out = json.loads(H.run(agent._exec_remember(self.h, {"key": "alias", "name": "big lamp", "value": "light.x"})))
        self.assertIn("error", out)
        self.assertEqual(self.saved, {})


class Trim(unittest.TestCase):
    def test_pure_offer_goes(self):
        self.assertEqual(agent._jh_trim_offer("Done, sir. Would you like anything else?"), "Done, sir.")
        self.assertEqual(agent._jh_trim_offer("Done. Shall I also dim it? Let me know."), "Done.")

    def test_sentence_with_a_fact_stays(self):
        text = "Done. The heater is on, let me know if you want it off."
        self.assertEqual(agent._jh_trim_offer(text), text)

    def test_confirmation_question_stays(self):
        text = "Unlocking needs confirmation. Shall I proceed?"
        self.assertEqual(agent._jh_trim_offer(text, True), text)

    def test_line_breaks_stay(self):
        text = "First line.\n\nSecond line."
        self.assertEqual(agent._jh_trim_offer(text), text)
        self.assertEqual(agent._jh_trim_offer(text + "\nAnything else?"), text)

    def test_only_sentence_stays(self):
        self.assertEqual(agent._jh_trim_offer("How can I help?"), "How can I help?")


class Reached(unittest.TestCase):
    def test_climate_and_media_have_their_own_on(self):
        self.assertTrue(agent._jh_reached("climate.x", ("on",), "heat"))
        self.assertTrue(agent._jh_reached("media_player.x", ("on",), "idle"))
        self.assertFalse(agent._jh_reached("media_player.x", ("on",), "standby"))
        self.assertFalse(agent._jh_reached("climate.x", ("on",), "unavailable"))
        self.assertTrue(agent._jh_reached("media_player.x", ("off",), "standby"))
        self.assertTrue(agent._jh_reached("light.x", ("on",), "on"))
        self.assertFalse(agent._jh_reached("light.x", ("on",), "off"))

    def test_a_group_counts_as_on_or_off(self):
        self.assertTrue(agent._jh_reached("group.all", ("on",), "on"))
        self.assertFalse(agent._jh_reached("group.all", ("off",), "on"))
        self.assertFalse(agent._jh_reached("group.all", ("on",), "off"))
        self.assertTrue(agent._jh_reached("group.covers", ("off",), "closed"), "a group of covers has its own words")
        self.assertFalse(agent._jh_reached("group.covers", ("off",), "unavailable"))

    def test_verify_uses_it(self):
        h = H.Hass()
        h.states.set("climate.x", "heat")
        self.assertTrue(agent._state_ok(h, "climate.x", ("on",)))


class Control(unittest.TestCase):
    def run_control(self, eid, before, after, action="turn_on"):
        h = H.Hass()
        h.states.set(eid, before, friendly_name="Thing")
        actuation = H.load("actuation")
        policy = H.load("policy")

        async def execute(hass, **kw):
            hass.states.set(eid, after, friendly_name="Thing")
            return True, ""
        with mock.patch.object(actuation, "execute_actuator", execute), \
                mock.patch.object(policy, "confirm_gate", mock.AsyncMock(return_value=(True, ""))), \
                mock.patch("asyncio.sleep", mock.AsyncMock()):
            return json.loads(H.run(agent._exec_control_device(h, {"entity_id": eid, "action": action})))

    def test_confirmed(self):
        out = self.run_control("light.x", "off", "on")
        self.assertTrue(out["success"], out)
        self.assertNotIn("warning", out)

    def test_silent_device_is_not_success(self):
        out = self.run_control("light.x", "off", "off")
        self.assertFalse(out["success"], out)
        self.assertTrue(out["sent"])
        self.assertIn("not confirmed", out["warning"])

    def test_climate_heat_is_on(self):
        self.assertTrue(self.run_control("climate.x", "off", "heat")["success"])

    def test_stateless_and_other_domains_are_not_unconfirmed(self):
        for eid, before, after, action in (("scene.movie", "2026-11-05T20:00:00", "2026-11-06T20:00:00", "turn_on"),
                                           ("script.bedtime", "off", "off", "turn_on"),
                                           ("button.x", "unknown", "2026-11-06T20:00:00", "turn_on"),
                                           ("input_button.x", "unknown", "2026-11-06T20:00:00", "turn_on"),
                                           ("vacuum.robo", "docked", "cleaning", "turn_on"),
                                           ("vacuum.robo", "cleaning", "docked", "turn_off"),
                                           ("valve.v", "closed", "open", "turn_on"),
                                           ("lawn_mower.m", "docked", "mowing", "turn_on"),
                                           ("alarm_control_panel.a", "disarmed", "armed_home", "turn_on")):
            out = self.run_control(eid, before, after, action)
            self.assertTrue(out["success"], eid)
            self.assertNotIn("warning", out, eid)

    def test_a_scene_reply_passes_the_final_check(self):
        out = self.run_control("scene.movie", "2026-11-05T20:00:00", "2026-11-06T20:00:00")
        st = agent._jh_new_state()
        agent._jh_note_tool(st, "control_device", json.dumps(out))
        self.assertEqual(agent._jh_final("The movie scene is on, sir.", st, True, "sir"), "The movie scene is on, sir.")

    def test_a_group_that_did_not_change_is_not_confirmed(self):
        self.assertFalse(self.run_control("group.all_lights", "on", "on", "turn_off")["success"])
        self.assertTrue(self.run_control("group.all_lights", "on", "off", "turn_off")["success"])

    def test_on_off_devices_and_locks_are_still_checked(self):
        self.assertFalse(self.run_control("switch.x", "off", "off")["success"])
        self.assertFalse(self.run_control("fan.x", "off", "off")["success"])
        self.assertFalse(self.run_control("lock.x", "unlocked", "unlocked", "lock")["success"])
        self.assertFalse(self.run_control("cover.x", "open", "open", "close")["success"])

    def test_a_stateless_device_that_is_gone_is_not_confirmed(self):
        self.assertFalse(self.run_control("scene.movie", "unknown", "unavailable")["success"])

    def test_cover_on_its_way_is_sent_but_not_confirmed(self):
        """Audit 10: a cover still opening (or a lock still locking) has not reached its end state yet."""
        out = self.run_control("cover.x", "closed", "opening", "open")
        self.assertFalse(out["success"])
        self.assertIs(out["confirmed"], False)
        self.assertTrue(out["in_progress"] and out["sent"])


class Notes(unittest.TestCase):
    def note(self, name, result):
        st = agent._jh_new_state()
        agent._jh_note_tool(st, name, result)
        return st

    def failed(self, name, result):
        return bool(agent._jh_failures(self.note(name, result)))

    def test_bulk_with_nothing_done_is_a_failure(self):
        self.assertTrue(self.failed("bulk_control", json.dumps({"success": True, "count": 0})))
        self.assertTrue(self.note("bulk_control", json.dumps({"success": True, "count": 2}))["changed"])

    def test_plan_with_nothing_done_is_a_failure(self):
        self.assertTrue(self.failed("execute_plan", json.dumps({"succeeded": 0, "failed": 2})))

    def test_text_results(self):
        self.assertTrue(self.note("create_goal", "Goal #1 opened")["changed"])
        self.assertTrue(self.failed("create_goal", "Couldn't open that goal: x"))

    def test_home_assistant_tools(self):
        self.assertTrue(self.note("HassTurnOn", json.dumps({"response_type": "action_done"}))["changed"])
        self.assertTrue(self.failed("HassTurnOn", json.dumps({"response_type": "error"})))
        self.assertFalse(self.note("GetLiveContext", "{}")["changed"])
        only_failed = json.dumps({"response_type": "action_done", "data": {"success": [], "failed": [{"id": "x"}]}})
        self.assertTrue(self.failed("HassTurnOn", only_failed))

    def test_mail_block_is_a_failure(self):
        self.assertFalse(self.note("control_device", json.dumps({"error": "Blocked for safety"}))["changed"])


class LiveFacts(unittest.TestCase):
    def test_uses_the_setting_rule_and_hvac_action(self):
        h = H.Hass()
        h.states.set("light.lamp", "on", friendly_name="Lamp")
        h.states.set("light.tv_backlight", "on", friendly_name="TV")
        h.registry_entries["light.tv_backlight"] = types.SimpleNamespace(entity_category="config", labels=set(), device_id=None)
        h.states.set("switch.radiator", "on", friendly_name="Radiator")
        h.states.set("climate.a", "heat", friendly_name="Sun")
        h.states.set("climate.b", "auto", friendly_name="Hall", hvac_action="heating")
        text = agent._live_facts(h)
        self.assertIn("1 lights in total", text)
        self.assertIn("Radiator: on", text)
        self.assertIn("Sun: powered on, heat level unknown", text)
        self.assertIn("Hall: auto, heating", text)


if __name__ == "__main__":
    unittest.main()
