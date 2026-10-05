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

# on-device text classifier
N_FEAT = 1 << 14

# overlay
OV_TOP, OV_SHARE = "On top of game", "Share view (mirror)"
CORNERS = ["Bottom-right", "Bottom-left", "Top-right", "Top-left", "Off"]

# too noisy for a 1B model; lexicon patterns still cover them
DEFAULT_AI_OFF = {"profanity", "spam", "bullying", "harassment"}

CATS = {
    "racism": "racial slurs, racial stereotypes or hate toward an ethnicity",
    "antisemitism": "hate or conspiracy talk targeting Jewish people",
    "xenophobia": "hate toward nationalities, immigrants or foreigners",
    "religious_hate": "hate toward a religion or its followers",
    "homophobia": "slurs or hate toward gay, lesbian or bisexual people",
    "transphobia": "slurs, mockery or hate toward trans or nonbinary people",
    "sexism": "misogyny, gender-based insults, demeaning women or men",
    "ableism": "mocking disability or using disability slurs as insults",
    "harassment": "targeted insults or persistent hostility toward a person",
    "bullying": "pile-ons, humiliation, exclusion, mocking a specific player",
    "threats": "threats of violence, swatting or real-world harm",
    "doxxing": "sharing or demanding personal info, addresses, IPs",
    "self_harm_encouragement": "telling someone to hurt or kill themselves",
    "sexual_content": "sexual or explicit talk or roleplay",
    "predatory_behaviour": "asking age/photos, moving to private chats, secrecy, grooming signs",
    "scam": "free-Robux offers, fake trades, phishing, account stealing",
    "offsite_links": "invites or links to Discord, Telegram or other sites",
    "extremism": "extremist, terrorist or hate-group content",
    "spam": "repeated advertising or flooding",
    "exploiting": "cheating, exploit scripts, ban-evasion talk",
    "profanity": "strong swearing not aimed at a protected group",
}

# Built-in patterns for the non-slur categories. Add your own (slurs etc.) in lexicon.json.
BUILTIN = {
    "scam": [r"free\s*robux", r"robux\s*generator", r"verify.{0,20}(group|link|account)"],
    "offsite_links": [r"discord\.gg/", r"\bt\.me/", r"https?://"],
    "predatory_behaviour": [r"how old are (you|u)\b", r"\basl\b", r"send (me )?(a )?(pic|photo)s?",
                            r"(add|dm) me on (snap|insta|discord|telegram)", r"don'?t tell (your )?(mom|dad|parents)"],
    "self_harm_encouragement": [r"\bkys\b", r"kill yourself"],
    "threats": [r"i'?ll (find|hurt|kill) you", r"\bswat(ting)?\b"],
    "doxxing": [r"\bdox(x?ed|x?ing)?\b", r"your (ip|address) is"],
}

# A 1B model is a bad judge of its own confidence, so for the touchy categories it may only flag a message that
# also contains a cue word (or that your own trained model agrees with). Pattern/lexicon flags are unaffected.
CUES = {
    "predatory_behaviour": r"\b(age|old|asl|pics?|photos?|selfies?|snap(chat)?|insta(gram)?|discord|telegram|kik|whatsapp|"
                           r"tiktok|dm|private|secret|alone|meet|cam|nudes?|address|school|phone|number|home|parents?|mom|"
                           r"dad|boyfriend|girlfriend|bf|gf|cute|pretty|sexy|body)\b",
    "sexual_content": r"\b(sex\w*|nudes?|naked|porn\w*|horny|lewd|erp|strip\w*|kiss\w*|bikini)\b",
    "doxxing": r"\b(ip|address|lives? at|phone|number|real name|school|dox\w*|swat\w*|location|street)\b",
    "threats": r"(find you|your (house|home|address|school|ip)|swat|\birl\b|real life|come to your|hurt you|kill you|bomb|shoot up|stab)",
    "self_harm_encouragement": r"\b(kys|kill (yourself|urself)|die|suicide|hang|jump off|rope|end it)\b",
    "scam": r"\b(robux|free|giveaway|generator|verify|password|login|link|profile|bio|click|code|claim)\b",
    "offsite_links": r"(discord|telegram|t\.me|https?|www\.|\.com|\.gg|snap|insta|tiktok|youtube)",
}

# Few-shot examples that are always shown to the model and always used to train the on-device classifier.
SEEDS = [
    ("gg wp", "none"), ("anyone want to trade?", "none"), ("lol that was so funny", "none"),
    ("hello everyone", "none"), ("this game is trash lol", "none"), ("where do i go next", "none"),
    ("free robux go to my profile and click the link", "scam"), ("kys", "self_harm_encouragement"),
    ("how old are you? send me a pic", "predatory_behaviour"), ("i will find you and hurt you", "threats"),
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
          "real-world harm. predatory_behaviour only counts when the message asks a person for their age, photos or "
          "personal details, asks them to move to a private app, or asks them to keep a secret. Do not guess from a "
          "username. When unsure, answer none.\nCategories:\n"
          + "\n".join(f"- {k}: {v}" for k, v in CATS.items())
          + '\n- none: anything else\nAnswer with JSON containing "category" and "confidence" (0 to 1).\n\n')

os.makedirs(IMG, exist_ok=True)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
