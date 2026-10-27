"""modes: what to play for what you're doing. pure data, no I/O: `ytm mode <name>` (bin/ytm) reads this table.

each mode: where it sits in the picker (group), what to search for (seeds, rotated), whether lyrics are allowed (instrumental),
how long a track may run in seconds (max: past it a track is a mix), how loud (vol), and a one-line description (what).
the seeds are ordinary search words: edit them to taste, or add your own with `ytm mode new <name> "seed, seed"`."""
import re

# lyric-looking words: a track whose title or album has one of these is dropped from the instrumental modes
VOCAL_HINTS = ("feat", "ft.", "remix)", "lyrics", "vocal", "(live", "acoustic version")

MODES = {
    "work":   {"seeds": ["melodic techno instrumental", "synthwave instrumental", "odesza instrumental", "deep house instrumental",
                         "lofi hip hop instrumental", "downtempo instrumental electronic", "ambient techno", "chillhop instrumental beats"],
               "instrumental": True, "max": 900, "vol": 45, "what": "no lyrics, steady energy"},
    "focus":  {"seeds": ["ambient focus", "post-rock instrumental", "minimal ambient electronic", "modern classical piano", "ólafur arnalds"],
               "instrumental": True, "max": 900, "vol": 40, "what": "quiet, no lyrics, nothing that pulls the eye"},
    "chill":  {"seeds": ["chillwave", "dream pop", "indie chill", "downtempo", "the midnight", "low roar"],
               "instrumental": False, "max": 900, "vol": 50, "what": "easy, lyrics welcome"},
    "energy": {"seeds": ["alternative rock anthems", "grandson", "nu metal", "phonk", "hard techno", "linkin park"],
               "instrumental": False, "max": 600, "vol": 60, "what": "anthems, loud"},
    "sleep":  {"seeds": ["ambient sleep", "sleep piano", "drone ambient", "rain ambient"],
               "instrumental": True, "max": 2400, "vol": 30, "timer": 45, "what": "soft, pauses itself after 45 min"},
    # 0.9.2 activity modes. what you are
    # doing, not a genre: each has its own seeds, lyrics or not, a volume, a light feel. `group` only orders the list.
    "coding":   {"group": "work", "seeds": ["synthwave coding", "cyberpunk instrumental", "programming music instrumental", "darksynth",
                                            "ambient techno", "lofi coding beats"], "instrumental": True, "max": 900, "vol": 42,
                 "what": "synthwave, techno, nothing with words: steady for long sessions"},
    "design":   {"group": "work", "seeds": ["indie electronic chill", "nu jazz", "bossa nova lounge", "french house chill", "neo soul instrumental",
                                            "future funk"], "instrumental": False, "max": 600, "vol": 45,
                 "what": "creative and warm, light vocals fine"},
    "writing":  {"group": "work", "seeds": ["ambient piano writing", "cinematic ambient", "post-rock instrumental", "film score piano",
                                            "ambient soundscape"], "instrumental": True, "max": 900, "vol": 35,
                 "what": "cinematic, quiet, no words to compete with yours"},
    "reading":  {"group": "work", "seeds": ["classical piano reading", "baroque study", "ambient reading", "acoustic guitar instrumental",
                                            "jazz piano trio"], "instrumental": True, "max": 900, "vol": 30, "what": "piano and strings, very low"},
    "study":    {"group": "work", "seeds": ["lofi study beats", "chillhop study", "brain food instrumental", "classical for studying",
                                            "piano lofi"], "instrumental": True, "max": 900, "vol": 40, "what": "lofi and light classical, steady"},
    "cafe":     {"group": "work", "seeds": ["cafe jazz", "bossa nova cafe", "coffee shop lofi", "acoustic cafe", "french cafe"],
                 "instrumental": False, "max": 600, "vol": 38, "what": "coffee-shop murmur: jazz, bossa, acoustic"},
    "gym":      {"group": "move", "seeds": ["workout hip hop", "gym motivation rap", "edm workout", "hard techno workout", "metal workout",
                                            "phonk gym"], "instrumental": False, "max": 420, "vol": 65, "what": "heavy, fast, loud"},
    "run":      {"group": "move", "seeds": ["running 170 bpm", "running pop", "upbeat indie running", "drum and bass running",
                                            "electro pop running"], "instrumental": False, "max": 420, "vol": 60, "what": "steady 150-175 bpm"},
    "drive":    {"group": "move", "seeds": ["night drive synthwave", "road trip rock", "driving indie", "retrowave drive", "bollywood road trip"],
                 "instrumental": False, "max": 600, "vol": 55, "what": "windows down, singalong"},
    "cook":     {"group": "move", "seeds": ["bollywood cooking", "funk soul cooking", "italian cooking playlist", "indie folk upbeat", "motown kitchen"],
                 "instrumental": False, "max": 420, "vol": 50, "what": "upbeat and warm, for hands-busy time"},
    "gaming":   {"group": "move", "seeds": ["video game soundtrack epic", "gaming dubstep", "synthwave gaming", "game ost chill", "drum and bass gaming"],
                 "instrumental": False, "max": 600, "vol": 50, "what": "ost and electronic, high stakes"},
    "party":    {"group": "move", "seeds": ["party hits", "bollywood party", "edm party", "hip hop party", "90s dance party"],
                 "instrumental": False, "max": 360, "vol": 70, "what": "crowd-pleasers, loud"},
    "morning":  {"group": "wind", "seeds": ["morning acoustic", "morning chill pop", "feel good morning", "indie folk morning", "soft jazz morning"],
                 "instrumental": False, "max": 420, "vol": 40, "what": "soft start, gets brighter"},
    "winddown": {"group": "wind", "seeds": ["evening acoustic", "lofi evening chill", "soft indie evening", "ambient evening", "jazz evening"],
                 "instrumental": False, "max": 600, "vol": 35, "what": "slow, warm, end of the day"},
}
for _k, _m in MODES.items(): _m.setdefault("group", "core")

WORK_SEEDS = MODES["work"]["seeds"]
MODE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,15}$")   # what a custom mode may be called
