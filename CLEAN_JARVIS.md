# Clean Jarvis

Clean Jarvis is a patch of [JARVIS AI Assistant](https://github.com/sam3gp8/jarvis-aio) by sam3gp8, published under the same MIT licence. The assistant is still JARVIS, with the same functions and calm voice. Clean Jarvis changes how honest and private it is.

## What is different

**It does not invent things**
- A cleaned personality: truth first, with no made-up house details or backstory.
- Spoken briefings read real facts only.
- On a device command, JARVIS may only say it worked if a device tool really ran. A failed or unconfirmed action is reported as such.

**Privacy between people**
- Chat history, memory, facts, reminders and goals are separated per person, automatically for new users.
- Non-admin logins keep normal household use, but cannot reach admin actions, mail, or other people's data.
- Face snapshots are no longer stored in the public web folder.

**Better day to day**
- Clearer device matching: a vague name asks which device you mean instead of guessing.
- Chat tab with saved history, press-to-talk, read-aloud, a thinking indicator and cancel.

**Voice control**

![Hey JARVIS listening in the Chat tab](docs/images/clean-jarvis-voice.png)

- Hey JARVIS switch in the Chat tab: JARVIS listens through your computer microphone on the web page and waits for "Hey Jarvis" before taking a command. Off by default, remembered per browser, and the audio goes only to your own Home Assistant.
- After "Hey Jarvis" you can pause before the command: the page waits for you to start and stops after a real pause. The button stays red while it listens. When nothing is said, nothing is sent.
- Listening pauses during hold to talk and while JARVIS speaks.
- Punctuation from speech recognition never changes a command: "Switch, lamp, green." works like "switch lamp green". Numbers and times such as 21.5 and 7:30 keep their marks.
- Device names that only sound alike are still found, so a misheard "fone" finds Phone. If two devices both sound right, JARVIS asks which one you mean.

## Install

Requires Home Assistant 2026.11 or newer, which includes the Marketplace.

**If you already have the original JARVIS**, make a backup, then remove it first. The Marketplace will not install over it:

1. Go to **Settings > Devices & services**, open JARVIS and delete it.
2. Go to **Settings > Marketplace**, open JARVIS and uninstall it.

**Then install Clean Jarvis:**

1. Go to **Settings > Marketplace**.
2. Select **Add from link** in the upper-right corner. Connect GitHub if asked.
3. Enter `https://github.com/ZoutMax/Clean-Jarvis`, then install it as an integration.
4. Restart Home Assistant.
5. If JARVIS does not appear under **Settings > Devices & services**, add it there.

In our test, stored facts survived this swap. Clean Jarvis uses the same internal name as the original, so you can only have one of them. New chat data is stored in a newer format, so going back to the original later may not keep everything.

## Updates

Clean Jarvis follows the original project. Each new release is tested against our patches first, then published here. Updates appear in **Settings > Updates**. Home Assistant checks repositories added by link every 48 hours.

## Known limits

- The truth check covers device commands. A false statement in ordinary chat is only guarded by the personality and live facts.
- Bare phrases such as "lights off" are not always recognised as commands.
- Hey JARVIS uses the wake word set in your Home Assistant voice assistant (openWakeWord "hey_jarvis"). A plain "Jarvis" wake word is not included.
- Everything here runs with full access to your Home Assistant. As with all community projects, review it before you use it.

Please report issues about these changes here, not to the original project.

## Credit

All original work is by [sam3gp8](https://github.com/sam3gp8). See [LICENSE](LICENSE).
