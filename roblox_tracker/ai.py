"""Chat flagging: tiny on-device classifier + the decision of whether an LLM verdict is trusted."""
import json
import os
import re

import numpy as np

from . import config, paths
from .config import CATS, CUES, N_FEAT, PROMPT, SEEDS
from .textutil import http, jac, remove_file, text_vec


class Clf:
    """Tiny on-device text classifier (violation vs fine). Retrains every time you label something."""
    FILE = paths.data_path("chat_model.npz")

    def __init__(self):
        self.w, self.b = np.zeros(N_FEAT, np.float32), 0.0
        if os.path.exists(self.FILE):
            d = np.load(self.FILE)
            self.w, self.b = d["w"], float(d["b"])

    def p(self, text):
        return float(1 / (1 + np.exp(-np.clip(self.w @ text_vec(text) + self.b, -30, 30))))

    def fit(self, data):
        self.w, self.b = np.zeros(N_FEAT, np.float32), 0.0
        X = [(text_vec(t), 0.0 if lab == "none" else 1.0) for t, lab in data]
        for _ in range(25):
            for x, y in X:
                g = y - 1 / (1 + np.exp(-np.clip(self.w @ x + self.b, -30, 30)))
                self.w += 0.3 * g * x
                self.b += 0.3 * g
        np.savez(self.FILE, w=self.w, b=self.b)

    def reset(self):
        remove_file(self.FILE)
        self.fit(SEEDS)


def verdict(msg, examples, clf, min_conf, ai_off):
    """Ask the local LLM about one OCR'd chat line.

    examples: [(text, label)] from the user's own labels; clf: Clf; min_conf: 0..1; ai_off: categories switched off.
    Returns (category, confidence) if the line should be flagged, else None. Raises on Ollama/JSON errors.
    """
    words = re.findall(r"[A-Za-z']{2,}", msg)
    if len(msg) < 6 or len(words) < 2 or sum(c.isalpha() for c in msg) < 0.6 * len(msg):
        return None
    p = clf.p(msg) if len(examples) >= 12 else None
    shots = sorted(list(examples) + SEEDS, key=lambda e: -jac(msg, e[0]))[:8]
    shown = "\n".join(f'Message: {t}\nAnswer: {{"category": "{lab}", "confidence": {0.05 if lab == "none" else 0.9}}}'
                      for t, lab in shots)
    r = json.loads(http(config.OLLAMA, {"model": config.MODEL, "prompt": PROMPT + shown + f"\nMessage: {msg}\nAnswer:",
                                        "stream": False, "format": "json", "options": {"temperature": 0}}))["response"]
    out = json.loads(r)
    cat = str(out.get("category", "none")).lower()
    try:
        conf = float(out.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if cat not in CATS or cat in ai_off or conf < min_conf:
        return None
    if p is not None and p < 0.25:                           # your own labels say this kind of message is fine
        return None
    if cat in CUES and not (re.search(CUES[cat], msg, re.I) or (p is not None and p >= 0.6)):
        return None                                          # small model alone isn't enough for this category
    return cat, conf
