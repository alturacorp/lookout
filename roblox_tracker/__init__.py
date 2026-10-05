"""Lookout (the Python package keeps its original folder name, roblox_tracker), split into modules.

    config.py    constants, flag categories, built-in patterns, cue words, prompts
    store.py     SQLite wrapper + schema + small settings helpers
    textutil.py  http helpers, look-alike name skeletons, text fingerprints
    ai.py        on-device text classifier + the "should this message be flagged" decision
    vision.py    outfit fingerprints, person detector, look gallery
    overlay.py   click-through overlay + share-view window + shared painting code
    teach.py     F1 freeze-and-teach window
    dialogs.py   secondary dialogs (AI categories, training, chat regions, data cleanup)
    app.py       main window, scan loop, tracking, chat intake, background workers

Run with:  python -m roblox_tracker
"""
