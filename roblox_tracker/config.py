"""Constants, flag categories, built-in patterns, cue words and prompts."""
import os
import re
from datetime import datetime

from . import paths

DB_PATH, IMG = paths.data_path("roblox_players.db"), paths.data_path("captures")
# Defaults. The live values are config.OLLAMA / config.MODEL, overwritten at startup from the saved settings.
OLLAMA, MODEL = "http://localhost:11434/api/generate", "llama3.2:1b"

NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
CHAT_RE = re.compile(r"^\[?@?([A-Za-z0-9_]{3,20})\]?\s*:\s*(.{2,})$")

# scanning / tracking
MIN_CONF, GRACE, IDLE_SECS, MOVE_PX, CROP_EVERY = 0.65, 3, 60, 40, 300

# look recognition (tune MATCH/MARGIN if you get wrong / missed auto-IDs)
USE_YOLO = True                 # optional person detector (pip install ultralytics); falls back silently
GALLERY_MAX, MATCH, MARGIN = 16, 0.92, 0.03
# Matching ONE outfit against the shared watchlist is a much weaker signal than matching your own taught players, because the
# fingerprint is only a colour summary. So it is stricter, and its results are always shown as "possible, unverified".
CENTRAL_MATCH, CENTRAL_MARGIN = 0.96, 0.02
EVIDENCE_MAX_BYTES = 60000   # the picture sent is small: cropped to one player, at most this many bytes
EVIDENCE_WINDOW = 10 * 60    # how long after an alert the owner can still choose to send a picture

# on-device text classifier
N_FEAT = 1 << 14

# overlay
OV_TOP, OV_SHARE = "On top of game", "Share view (mirror)"
CORNERS = ["Bottom-right", "Bottom-left", "Top-right", "Top-left", "Off"]

# The flag categories are the section names of Cardea's Incident Classification Schedule (the same list is in firestore.rules,
# the dashboard, the LATTICE forms and the bridge). A 1B model is too noisy to guess most of them from chat, so by default the AI
# only suggests the ones below; the built-in patterns (further down) and your lexicon.json always apply.
DEFAULT_AI_ON = {"offences_against_persons", "commerce_labour_and_finance", "digital_data_and_ai", "organised_crime"}

CATS = {
    "cardea_penalty_notices": "minor on-the-spot offences: noise, loitering, queue jumping, defacing, fare evasion",
    "threshold_and_district_2_access": "trying to pass the checkpoint without valid credentials, sharing or faking passes, obstructing the threshold",
    "offences_against_cardea": "abuse, threats or obstruction toward Cardea officers, posts, vehicles or equipment",
    "offences_against_persons": "violence, threats of harm, exploitation, grooming, persistent harassment or telling someone to hurt themselves",
    "property_and_assets": "theft, damage, trespass or occupying someone else's property",
    "weapons_and_arms": "possessing, carrying, using, making or supplying weapons, armour or explosives",
    "narcotics_and_stims": "dealing or using controlled substances, combat stims or neuro-chems",
    "commerce_labour_and_finance": "fraud, scams, rackets, illegal lending, unlicensed trade or workforce abuse",
    "digital_data_and_ai": "hacking, sharing or demanding personal info, deepfakes, drones or interfering with Cardea's safety systems",
    "body_cyberware_and_neural": "illegal implants or augmentation, misuse of neural recordings or simulation",
    "public_order_and_streets": "disorder, riots, curfew breaches, flooding the chat or other disruption of the district",
    "expression_media_and_culture": "unlicensed press, broadcast, performance, education or public messaging",
    "surveillance_and_compliance": "refusing identification or scanning, failing to register, not cooperating with Cardea's safety systems",
    "residence_family_and_identity": "homes, households, forged identity records, animals, births and deaths",
    "vehicles_transit_and_infrastructure": "superway, road, tunnel or lift offences, vehicle misuse",
    "environment_utilities_and_health": "air, water, power, waste or public health offences",
    "justice_and_custody": "escaping custody, tampering with evidence, intimidating witnesses, interfering with the court process",
    "organised_crime": "organised criminal groups, rackets, corruption, proscribed or extremist organisations",
    "emergency_and_lockdown": "offences that only exist, or get worse, while a district lockdown is in force",
    "internal_conduct": "misconduct by Cardea staff (staff only; handled by Workforce Integrity)",
}
DEFAULT_AI_OFF = set(CATS) - DEFAULT_AI_ON

# Built-in patterns. Add your own in lexicon.json (use the category names above).
BUILTIN = {
    "commerce_labour_and_finance": [r"free\s*robux", r"robux\s*generator", r"verify.{0,20}(group|link|account)"],
    "offences_against_persons": [r"how old are (you|u)\b", r"\basl\b", r"send (me )?(a )?(pic|photo)s?",
                                 r"(add|dm) me on (snap|insta|discord|telegram)", r"don'?t tell (your )?(mom|dad|parents)",
                                 r"\bkys\b", r"kill yourself", r"i'?ll (find|hurt|kill) you", r"\bswat(ting)?\b"],
    "digital_data_and_ai": [r"\bdox(x?ed|x?ing)?\b", r"your (ip|address) is"],
}

# A 1B model is a bad judge of its own confidence, so for the touchy categories it may only flag a message that
# also contains a cue word (or that your own trained model agrees with). Pattern/lexicon flags are unaffected.
CUES = {
    "offences_against_persons": r"(\b(age|old|asl|pics?|photos?|selfies?|snap(chat)?|insta(gram)?|discord|telegram|kik|whatsapp|"
                                r"tiktok|dm|private|secret|alone|meet|cam|nudes?|address|school|phone|number|home|parents?|mom|"
                                r"dad|boyfriend|girlfriend|bf|gf|cute|pretty|sexy|body|kys|kill (yourself|urself)|die|suicide|hang|"
                                r"jump off|rope|end it|swat\w*)\b|find you|your (house|home|address|school|ip)|\birl\b|real life|"
                                r"come to your|hurt you|kill you|bomb|shoot up|stab)",
    "digital_data_and_ai": r"\b(ip|address|lives? at|phone|number|real name|school|dox\w*|swat\w*|location|street)\b",
    "commerce_labour_and_finance": r"\b(robux|free|giveaway|generator|verify|password|login|link|profile|bio|click|code|claim)\b",
}

# Few-shot examples that are always shown to the model and always used to train the on-device classifier.
SEEDS = [
    ("gg wp", "none"), ("anyone want to trade?", "none"), ("lol that was so funny", "none"),
    ("hello everyone", "none"), ("this game is trash lol", "none"), ("where do i go next", "none"),
    ("free robux go to my profile and click the link", "commerce_labour_and_finance"), ("kys", "offences_against_persons"),
    ("how old are you? send me a pic", "offences_against_persons"), ("i will find you and hurt you", "offences_against_persons"),
    ("yh gimme a gun imma beat yo ahh", "none"), ("imma get u back lol", "none"),
    ("bro you are so bad lmao", "none"), ("give me your sword", "none"),
    ("i will destroy you in this game", "none"), ("add me as a friend", "none"),
    ("send me the trade", "none"), ("who is the murderer", "none"), ("stop camping bro", "none"),
    ("what is your favourite game", "none"), ("how old is this game", "none"),
]

PROMPT = ("You help a Roblox player keep notes on other players. The chat line below was read from the screen with OCR, "
          "so it may be garbled, cut off or just a random word. Most Roblox chat is harmless kid banter. These are NOT "
          'violations: greetings, game talk, jokes, trash talk, in-game fighting or roleplay ("beat you", "gimme a gun", '
          '"i\'ll get you"), slang ("ahh", "bro", "yh"), mild swearing, typos. Threats only count when they are about '
          "real-world harm. offences_against_persons (grooming) only counts when the message asks a person for their age, photos or "
          "personal details, asks them to move to a private app, or asks them to keep a secret. Do not guess from a "
          "username. When unsure, answer none.\nCategories:\n"
          + "\n".join(f"- {k}: {v}" for k, v in CATS.items())
          + '\n- none: anything else\nAnswer with JSON containing "category" and "confidence" (0 to 1).\n\n')

os.makedirs(IMG, exist_ok=True)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Keep in step with highSeverity() in firebase/firestore.rules and HIGH_SEVERITY in the dashboard's config.js.
HIGH_SEVERITY = ("offences_against_persons", "weapons_and_arms", "organised_crime", "internal_conduct")
