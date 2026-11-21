# Credits: JARVIS login screen (optional page)

The login screen is off by default. Its large media files are not in this repository or in the HACS download. When an
administrator switches the login screen on, JARVIS downloads them once from this repository's pinned release asset
(`jarvis-login-assets-v1.zip`, checked against its SHA-256) into `/config/www/jarvis-login/`. A copy of this file is in
that download.

## Login screen media

- **Orion Nebula fly-through video** (`space.mp4`, `space-poster.jpg`): a silent 46-second excerpt, re-encoded, from
  "3D journey through the Orion Nebula" (hubblecast106e), https://esahubble.org/videos/hubblecast106e/ (CC BY 4.0).
  Credit: NASA, ESA, F. Summers, G. Bacon, Z. Levay, J. DePasquale, L. Frattare, M. Robberto (STScI), R. Hurt
  (Caltech/IPAC), A. Fujii, and M. Kornmesser (ESA/Hubble). Acknowledgement: R. Gendler.
  The original's music is not included. No endorsement by NASA, ESA or STScI is implied.
- **"Cosmic Cliffs" in Carina** (`space-1.jpg`, Webb, weic2205a): NASA, ESA, CSA, and STScI.
  https://esawebb.org/images/weic2205a/ (CC BY 4.0). Resized for web use.
- **Pillars of Creation, visible** (`space-2.jpg`, Hubble, heic1501a): NASA, ESA/Hubble and the Hubble Heritage Team.
  https://esahubble.org/images/heic1501a/ (CC BY 4.0). Resized for web use.
- **Planet, Moon and Saturn ring textures** (`tex/2k_*`): Solar System Scope,
  https://www.solarsystemscope.com/textures/ (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/). Used unmodified:
  Mercury, Venus (atmosphere), Earth (day map and clouds), the Moon, Mars, Jupiter, Saturn and its ring, Uranus, Neptune.
- **Fonts** (`fonts/`): Orbitron, Copyright 2018 The Orbitron Project Authors, and Inter, Copyright 2020 The Inter Project
  Authors, both under the SIL Open Font License 1.1 (`fonts/OFL-Orbitron.txt`, `fonts/OFL-Inter.txt`). The latin subsets
  as served by Google Fonts, unmodified.
- **Voice prompt** "Identify yourself." (`identify.mp3`, in this repository): generated with Piper TTS
  (https://github.com/rhasspy/piper, MIT) using the voice en_US-joe-medium from https://huggingface.co/rhasspy/piper-voices.
  Its training data is CC0 (https://github.com/OHF-Voice/voice-datasets) and it was fine-tuned from Piper's rdh base model,
  which is CC0 as well, so no other licence applies to the clip; it is shared under CC0.

The ESA, NASA and Webb logos are not used.

## Software

- **three.js** r128 (`three.min.js`, in the download): MIT License, Copyright (c) 2010-2021 three.js authors.
  https://github.com/mrdoob/three.js. Its licence text is `three-LICENSE.txt` in the download.
