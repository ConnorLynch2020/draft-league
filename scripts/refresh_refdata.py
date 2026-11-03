#!/usr/bin/env python3
"""Refresh scripts/ref/*.json (move and type reference data) from Pokemon Showdown.

Only needed if the league changes generation or Showdown rebalances a move you care about.
"""
import json
import os
import re
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref")
BASE = "https://play.pokemonshowdown.com/data/"


def get(name):
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "draft-league-stats/1.0"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def secondaries(m):
    out = []
    for s in (m.get("secondaries") or ([m["secondary"]] if m.get("secondary") else [])):
        if not s or s.get("self"):
            continue
        chance = s.get("chance")
        if chance is None:
            continue
        if s.get("status"):
            kind = s["status"]
        elif s.get("volatileStatus") == "flinch":
            kind = "flinch"
        elif s.get("boosts"):
            kind = "drop"
        elif s.get("volatileStatus") == "confusion":
            kind = "confusion"
        elif s.get("volatileStatus"):
            kind = "other"
        else:
            kind = "other"
        out.append({"chance": chance, "kind": kind})
    return out


MOD_URL = "https://raw.githubusercontent.com/smogon/pokemon-showdown/master/data/mods/gen%d/moves.ts"
GEN = 5  # the league's generation; mods are applied newest to oldest so later ones win


def mod_overrides(gen):
    """Parse the accuracy / critRatio / secondary overrides out of a Showdown mod's moves.ts."""
    req = urllib.request.Request(MOD_URL % gen, headers={"User-Agent": "draft-league-stats/1.0"})
    text = urllib.request.urlopen(req, timeout=60).read().decode()
    out = {}
    for m in re.finditer(r"^\t(\w+): \{\n(.*?)^\t\},?$", text, re.S | re.M):
        mid, body = m.group(1), m.group(2)
        o = {}
        a = re.search(r"^\t\taccuracy: (true|\d+),", body, re.M)
        if a:
            o["accuracy"] = True if a.group(1) == "true" else int(a.group(1))
        c = re.search(r"^\t\tcritRatio: (\d+),", body, re.M)
        if c:
            o["critRatio"] = int(c.group(1))
        sec = re.search(r"^\t\t(secondary|secondaries): (.*?)^\t\t(?:\}|\]),?$|^\t\tsecondary: null", body, re.S | re.M)
        if sec:
            chunk = sec.group(0)
            sl = []
            for sm in re.finditer(r"chance: (\d+)(.*?)(?=chance: |\Z)", chunk, re.S):
                rest = sm.group(2)
                item = {"chance": int(sm.group(1))}
                st = re.search(r"\bstatus: '(\w+)'", rest)
                vs = re.search(r"volatileStatus: '(\w+)'", rest)
                if st:
                    item["status"] = st.group(1)
                elif vs:
                    item["volatileStatus"] = vs.group(1)
                elif "boosts:" in rest:
                    item["boosts"] = {"x": -1}
                if "self:" in rest.split("}")[0]:
                    item["self"] = True
                sl.append(item)
            o["secondaries"] = sl
            o["secondary"] = None
        if o:
            out[mid] = o
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    modern = get("moves.json")
    for gen in range(8, GEN - 1, -1):
        for mid, o in mod_overrides(gen).items():
            if mid in modern:
                modern[mid].update(o)
    moves = {}
    for m in modern.values():
        if m.get("isNonstandard") in ("CAP", "Custom"):
            continue
        moves[m["name"]] = {
            "acc": m["accuracy"], "cat": m["category"], "bp": m.get("basePower", 0),
            "crit": m.get("critRatio") or 1, "willCrit": bool(m.get("willCrit")),
            "ohko": bool(m.get("ohko")), "fixed": m.get("damage") is not None,
            "sec": secondaries(m),
            "pri": m.get("priority", 0), "tgt": m.get("target", "normal"),
        }
    species, speed = {}, {}
    for s in get("pokedex.json").values():
        if "types" in s:
            species[s["name"]] = s["types"]
        if "baseStats" in s:
            # base Speed, and whether the species can have Prankster (+1 priority on status moves)
            speed[s["name"]] = {"spe": s["baseStats"]["spe"]}
            if "Prankster" in (s.get("abilities") or {}).values():
                speed[s["name"]]["prankster"] = True
    with open(os.path.join(OUT, "moves.json"), "w") as f:
        json.dump(moves, f, separators=(",", ":"), sort_keys=True)
    with open(os.path.join(OUT, "species.json"), "w") as f:
        json.dump(species, f, separators=(",", ":"), sort_keys=True)
    with open(os.path.join(OUT, "speed.json"), "w") as f:
        json.dump(speed, f, separators=(",", ":"), sort_keys=True)
    print(len(moves), "moves,", len(species), "species")


if __name__ == "__main__":
    main()
