"""looks: the names and tables every part of the player agrees on (cli, terminal ui, stage). stdlib only, no I/O.

a *look* is a drawn scene (what the stage and the terminal ui paint behind the player); an *accent* is the colour family it
uses; *motion* is how much it moves. `auto` is a choice, not a look: the song (or the mode) picks one."""

# the first five are also what `auto` can pick from the song (lib/autolook.py, AutoLook.swift); the rest are for choosing
LOOKS = ["dusk", "rain", "neon", "stars", "paper", "aurora", "ember", "tide"]
LOOK_NAMES = {"dusk": "dusk", "rain": "rain window", "neon": "neon drive", "stars": "stargaze", "paper": "paper", "aurora": "aurora", "ember": "embers", "tide": "tide", "auto": "auto"}
ACCENTS = ["cover", "night", "ember", "sea", "mono"]
MOTIONS = ["still", "calm", "lively"]

# on auto, a mode sets the scene: the stage keeps the same table (stage/Sources/YtmStage/AutoLook.swift)
MODE_LOOK = {"work": "paper", "focus": "paper", "chill": "rain", "energy": "neon", "sleep": "stars",
             "coding": "rain", "design": "dusk", "writing": "paper", "reading": "paper", "study": "paper", "cafe": "dusk",
             "gym": "neon", "run": "neon", "drive": "neon", "cook": "dusk", "gaming": "neon", "party": "neon",
             "morning": "dusk", "winddown": "rain"}
