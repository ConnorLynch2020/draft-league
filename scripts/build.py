#!/usr/bin/env python3
"""Rebuild every data/*.json file from the replay links in replays.txt.

    python3 scripts/build.py            # fetch any new replays, rebuild data/
    python3 scripts/build.py --offline  # only use cached replays

replays.txt: one replay URL per line, optionally followed by a week number
(`https://replay.pokemonshowdown.com/gen5doublescustomgame-123 4`). '#' starts a comment.
A line can instead name a replay file downloaded from Showdown and committed under replays/
(`replays/my-game.html 4`), for games whose link wasn't saved.
Fetched replay logs are cached in cache/ so a rebuild never re-downloads them.
"""
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict, OrderedDict

sys.path.insert(0, os.path.dirname(__file__))
from replay import parse_replay  # noqa: E402
from luck import luck_for_game, aggregate_luck  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, "cache")
CONFIG = os.path.join(ROOT, "config")


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, obj, compact=False):
    with open(os.path.join(DATA, name + ".json"), "w") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def to_id(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def r2(x):
    return round(x, 2)


def r1(x):
    return round(x, 1)


def r6(x):
    return round(x, 6)


# The reference species file has current types. Gen 5 had no Fairy type: these were Normal, Psychic,
# Water, Steel or Grass back then.
FAIRY_GEN5 = {
    "Cleffa": "Normal", "Clefairy": "Normal", "Clefable": "Normal", "Igglybuff": "Normal", "Jigglypuff": "Normal",
    "Wigglytuff": "Normal", "Togepi": "Normal", "Togetic": "Normal", "Togekiss": "Normal", "Snubbull": "Normal",
    "Granbull": "Normal", "Azurill": "Normal", "Marill": "Water", "Azumarill": "Water", "Ralts": "Psychic",
    "Kirlia": "Psychic", "Gardevoir": "Psychic", "Mime Jr.": "Psychic", "Mr. Mime": "Psychic", "Mawile": "Steel",
    "Cottonee": "Grass", "Whimsicott": "Grass",
}


def species_types(species, name):
    """Gen 5 types for a species name, trying the full forme name and then the base name before '-'."""
    t = species.get(name) or species.get(name.split("-")[0])
    if not t:
        print("No types for %s" % name)
        return []
    out = []
    for x in t:
        if x == "Fairy":
            x = FAIRY_GEN5.get(name) or FAIRY_GEN5.get(name.split("-")[0])
        if x and x not in out:
            out.append(x)
    return out


def parse_links(path):
    """replays.txt -> OrderedDict(replay id -> (week override, local HTML path or None))."""
    out = OrderedDict()
    with open(path) as f:
        for raw in f:
            line = raw.split("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            week = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            if parts[0].lower().endswith((".html", ".htm")):
                file = os.path.normpath(os.path.join(ROOT, parts[0]))
                if not os.path.isfile(file):
                    sys.exit("Replay file %s doesn't exist. Upload it to the replays/ folder." % parts[0])
                rep = read_html_replay(file)
                out[rep["id"]] = (week, file)
                continue
            m = re.search(r"replay\.pokemonshowdown\.com/([A-Za-z0-9\-]+?)(?:\.json|\.log)?(?:\?.*)?$", parts[0])
            if not m:
                sys.exit("Can't read a replay link from: %r" % raw.strip())
            out[m.group(1)] = (week, None)
    return out


def read_html_replay(path):
    """A replay saved with Showdown's "Download replay" button -> the same dict the replay JSON API gives."""
    with open(path, encoding="utf-8", errors="replace") as f:
        page = f.read()
    m = re.search(r'<script[^>]*class="(?:battle-log-data|log)"[^>]*>(.*?)</script>', page, re.S)
    if m:
        log = m.group(1).replace("\\/", "/")  # the download escapes every '/' as '\/'
    else:
        m = re.search(r'<textarea[^>]*class="(?:battle-log-data|log)"[^>]*>(.*?)</textarea>', page, re.S)
        if not m:
            sys.exit("Replay file %s has no battle log in it. Use the file from Showdown's Download replay "
                     "button." % os.path.relpath(path, ROOT))
        log = html.unescape(m.group(1))
    log = log.strip("\r\n").replace("\r\n", "\n")
    m = re.search(r'name="replayid" value="([^"]+)"', page)
    rid = m.group(1) if m else ""
    if not re.fullmatch(r"[A-Za-z0-9\-]+", rid):
        rid = re.sub(r"[^A-Za-z0-9\-]+", "-", os.path.splitext(os.path.basename(path))[0]).strip("-")
    m = re.search(r"^\|t:\|(\d+)", log, re.M)
    uploadtime = int(m.group(1)) if m else int(os.path.getmtime(path))
    players = [ln.split("|")[3] for ln in log.split("\n")
               if ln.startswith("|player|") and len(ln.split("|")) > 3 and ln.split("|")[3]]
    return {"id": rid, "log": log, "uploadtime": uploadtime, "players": players,
            "file": os.path.relpath(path, ROOT).replace(os.sep, "/")}


def fetch(rid, offline):
    path = os.path.join(CACHE, rid + ".json")
    if os.path.exists(path):
        return load(path)
    if offline:
        sys.exit("Replay %s isn't cached and --offline was given." % rid)
    url = "https://replay.pokemonshowdown.com/%s.json" % rid
    req = urllib.request.Request(url, headers={"User-Agent": "draft-league-stats/1.0"})
    last = None
    for attempt in range(4):
        try:
            body = urllib.request.urlopen(req, timeout=30).read()
            rep = json.loads(body)
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "w") as f:
                json.dump(rep, f, ensure_ascii=False)
            time.sleep(0.3)
            return rep
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    sys.exit("Couldn't fetch %s (%s). Private replays can't be read; make sure it's uploaded as public." % (url, last))


def main():
    offline = "--offline" in sys.argv
    os.makedirs(DATA, exist_ok=True)
    links = parse_links(os.path.join(ROOT, "replays.txt"))
    players_cfg = load(os.path.join(CONFIG, "players.json"))  # account id -> display name
    schedule_cfg = load(os.path.join(CONFIG, "schedule.json"))  # [{week, player1, player2}]
    acct_to_player = {to_id(a): p for a, p in players_cfg.items()}
    pair_week = {}
    for s in schedule_cfg:
        pair_week[frozenset((s["player1"], s["player2"]))] = s["week"]

    # ---- parse every replay -------------------------------------------------
    games = []
    for rid, (week_override, file) in links.items():
        rep = read_html_replay(file) if file else fetch(rid, offline)
        g = parse_replay(rep)
        names = {}
        for side in ("p1", "p2"):
            acct = g["accounts"].get(side)
            pl = acct_to_player.get(to_id(acct or ""))
            if pl is None:
                sys.exit("Replay %s: Showdown account %r isn't in config/players.json. Add it and re-run." % (rid, acct))
            names[side] = pl
        g.update(id=rid, rep=rep, names=names)
        g["week"] = week_override or pair_week.get(frozenset(names.values()))
        if g["week"] is None:
            sys.exit("Replay %s: %s vs %s isn't on the schedule; add a week number after the link." % (
                rid, names["p1"], names["p2"]))
        if g["winner_side"] is None:
            sys.exit("Replay %s has no winner recorded (unfinished game?)." % rid)
        games.append(g)

    # game number inside each series, in play order
    games.sort(key=lambda g: (g["week"], frozenset(g["names"].values()) and sorted(g["names"].values()), g["rep"]["uploadtime"]))
    counter = defaultdict(int)
    for g in games:
        key = (g["week"], tuple(sorted(g["names"].values())))
        counter[key] += 1
        g["game"] = counter[key]

    species_ref = load(os.path.join(ROOT, "scripts", "ref", "species.json"), {})
    out_games, gm_rows, gmv_rows = [], [], []
    ind = defaultdict(lambda: {"hits": 0, "damage": 0.0, "faints": 0})
    weather_games = defaultdict(int)
    team = defaultdict(lambda: defaultdict(float))
    team_species = defaultdict(set)
    team_accounts = defaultdict(list)
    mon = defaultdict(lambda: defaultdict(float))
    mv = defaultdict(lambda: {"uses": 0, "kos": 0, "damage": 0.0})
    tmv = defaultdict(int)
    roster = defaultdict(set)

    for g in games:
        r = g["names"]
        ws = g["winner_side"]
        ls = "p2" if ws == "p1" else "p1"
        sides = {s: g["mons"][s] for s in ("p1", "p2")}
        first_blood = None
        if g["faints"]:
            first_blood = r["p2" if g["faints"][0][1] == "p1" else "p1"]
        dmg = {s: sum(m.damage for m in sides[s].values()) for s in sides}
        taken = {"p1": dmg["p2"], "p2": dmg["p1"]}  # opponent-dealt, so friendly fire is excluded
        lost = {s: sum(m.fainted for m in sides[s].values()) for s in sides}
        kos = {s: sum(m.kos for m in sides[s].values()) for s in sides}
        wm = sides[ws]
        left = sum(1 for m in wm.values() if m.brought and not m.fainted)
        hp_left = sum(m.frac for m in wm.values() if m.brought and not m.fainted)
        leads = {s: " + ".join(m.species.split("-")[0] for m in sides[s].values() if m.led) for s in sides}
        gid = g["id"].split("-")[-1]
        date = datetime.datetime.fromtimestamp(g["rep"]["uploadtime"], datetime.timezone.utc).strftime("%Y-%m-%d")
        out_games.append(OrderedDict([
            ("id", gid),
            ("replay", g["rep"]["file"] if g["rep"].get("file") else "https://replay.pokemonshowdown.com/" + g["id"]),
            ("date", date), ("week", g["week"]), ("game", g["game"]),
            ("matchup", "%s vs %s" % (r["p1"], r["p2"])),
            ("player1", r["p1"]), ("player2", r["p2"]),
            ("account1", g["accounts"]["p1"]), ("account2", g["accounts"]["p2"]),
            ("winner", r[ws]), ("loser", r[ls]), ("turns", g["turns"]),
            ("leads1", leads["p1"]), ("leads2", leads["p2"]),
            ("lost1", lost["p1"]), ("lost2", lost["p2"]),
            ("first_blood", first_blood),
            ("damage1", r1(dmg["p1"])), ("damage2", r1(dmg["p2"])),
            ("winner_mons_left", left), ("winner_hp_left", r2(hp_left)),
            ("weather", ", ".join(g["weather"])),
        ]))
        for w in g["weather"]:
            weather_games[w] += 1
        for name, frac, fainted in g["indirect"]:
            ind[name]["hits"] += 1
            ind[name]["damage"] += frac
            ind[name]["faints"] += 1 if fainted else 0
        for s in ("p1", "p2"):
            p = r[s]
            won = s == ws
            t = team[p]
            t["games"] += 1
            t["wins"] += won
            t["losses"] += not won
            t["turns"] += g["turns"]
            t["kos"] += kos[s]
            t["damage"] += dmg[s]
            t["taken"] += taken[s]
            t["lost"] += lost[s]
            if first_blood is not None:
                if first_blood == p:
                    t["fb"] += 1
                    t["fb_win"] += won
            if won:
                t["left"] += left
                t["hp"] += hp_left
            acct = g["accounts"][s]
            if acct not in team_accounts[p]:
                team_accounts[p].append(acct)
            for sp in g["rosters"][s]:
                roster[p].add(sp)
            for sp, m in sides[s].items():
                roster[p].add(sp)
                if m.brought:
                    team_species[p].add(sp)
                row = mon[(p, sp)]
                if m.brought:
                    row["brought"] += 1
                    row["brought_wins"] += won
                if m.led:
                    row["led"] += 1
                    row["led_wins"] += won
                for k in ("kos", "damage", "taken", "turns", "sacrifices", "friendly_fire", "crits",
                          "super_effective", "misses", "statuses"):
                    row[k] += getattr(m, k)
                row["faints"] += m.fainted
                gm_rows.append(OrderedDict([
                    ("game", gid), ("player", p), ("pokemon", sp), ("led", int(m.led)), ("won", int(won)),
                    ("kos", m.kos), ("damage", r6(m.damage)), ("taken", r6(m.taken)), ("faints", int(m.fainted)),
                    ("turns_on_field", m.turns), ("sacrifices", m.sacrifices), ("friendly_fire", r6(m.friendly_fire)),
                    ("crits", m.crits), ("super_effective", m.super_effective), ("misses", m.misses),
                    ("statuses", m.statuses), ("hp", 0 if m.fainted else r6(m.frac)),
                ]))
                for mname, mm in m.moves.items():
                    gmv_rows.append(OrderedDict([
                        ("game", gid), ("player", p), ("pokemon", sp), ("move", mname),
                        ("uses", mm["uses"]), ("kos", mm["kos"]), ("damage", r6(mm["damage"])),
                    ]))
                    tmv[(p, sp, mname)] += mm["uses"]
                    x = mv[mname]
                    x["uses"] += mm["uses"]
                    x["kos"] += mm["kos"]
                    x["damage"] += mm["damage"]
        g["luck"] = luck_for_game(g)

    # ---- schedule ------------------------------------------------------------
    sched = []
    series = defaultdict(list)
    for og in out_games:
        series[(og["week"], tuple(sorted((og["player1"], og["player2"]))))].append(og)
    series_w = defaultdict(int)
    series_l = defaultdict(int)
    for s in schedule_cfg:
        key = (s["week"], tuple(sorted((s["player1"], s["player2"]))))
        gl = series.get(key, [])
        row = OrderedDict([
            ("id", "w%d-%s-%s" % (s["week"], s["player1"], s["player2"])),
            ("week", s["week"]), ("matchup", "%s vs %s" % (s["player1"], s["player2"])),
            ("player1", s["player1"]), ("player2", s["player2"]),
        ])
        if gl:
            w1 = sum(1 for x in gl if x["winner"] == s["player1"])
            w2 = len(gl) - w1
            winner = s["player1"] if w1 > w2 else (s["player2"] if w2 > w1 else None)
            if winner:
                series_w[winner] += 1
                series_l[s["player2"] if winner == s["player1"] else s["player1"]] += 1
            row.update(status="Played", winner=winner, score="%d–%d" % (w1, w2))
        else:
            row.update(status="Upcoming", winner=None, score=None)
        for n in range(1, 4):
            row["game%d" % n] = gl[n - 1]["id"] if len(gl) >= n else None
            row["game%d_url" % n] = gl[n - 1]["replay"] if len(gl) >= n else None
        sched.append(row)

    # ---- teams ---------------------------------------------------------------
    teams = []
    for p, t in team.items():
        n = t["games"]
        teams.append(OrderedDict([
            ("player", p), ("series_wins", series_w[p]), ("series_losses", series_l[p]),
            ("games", int(n)), ("wins", int(t["wins"])), ("losses", int(t["losses"])),
            ("win_pct", round(t["wins"] / n, 3)), ("avg_turns", r1(t["turns"] / n)),
            ("kos", int(t["kos"])), ("kos_per_game", r2(t["kos"] / n)),
            ("damage", r1(t["damage"])), ("taken", r1(t["taken"])),
            ("mons_lost", int(t["lost"])), ("lost_per_game", r2(t["lost"] / n)),
            ("first_blood_games", int(t["fb"])), ("first_blood_pct", round(t["fb"] / n, 3)),
            ("win_pct_after_first_blood", round(t["fb_win"] / t["fb"], 3) if t["fb"] else None),
            ("avg_mons_left_in_wins", r2(t["left"] / t["wins"]) if t["wins"] else None),
            ("avg_hp_left_in_wins", r2(t["hp"] / t["wins"]) if t["wins"] else None),
            ("mons_used", len(team_species[p])), ("accounts", ", ".join(team_accounts[p])),
        ]))
    teams.sort(key=lambda x: (-x["series_wins"], -x["wins"], x["player"]))

    # ---- mons ----------------------------------------------------------------
    mons = []
    for p, species in roster.items():
        for sp in species:
            mon[(p, sp)]  # make sure never-brought Pokémon still get a row
    for (p, sp), row in mon.items():
        b = row["brought"]
        tg = team[p]["games"]
        mons.append(OrderedDict([
            ("id", "%s|%s" % (p, sp)), ("player", p), ("pokemon", sp),
            ("types", species_types(species_ref, sp)), ("team_games", int(tg)),
            ("brought", int(b)), ("bring_pct", round(b / tg, 3)),
            ("led", int(row["led"])), ("lead_pct", round(row["led"] / tg, 3)),
            ("win_pct_brought", round(row["brought_wins"] / b, 3) if b else None),
            ("win_pct_led", round(row["led_wins"] / row["led"], 3) if row["led"] else None),
            ("kos", int(row["kos"])), ("kos_per_app", r2(row["kos"] / b) if b else None),
            ("damage", r2(row["damage"])), ("damage_per_app", r2(r2(row["damage"]) / b) if b else None),
            ("taken", r2(row["taken"])), ("faints", int(row["faints"])),
            ("survival_pct", round(1 - row["faints"] / b, 3) if b else None),
            ("turns_on_field", int(row["turns"])), ("sacrifices", int(row["sacrifices"])),
            ("friendly_fire", r2(row["friendly_fire"])), ("crits", int(row["crits"])),
            ("super_effective", int(row["super_effective"])), ("misses", int(row["misses"])),
            ("statuses", int(row["statuses"])),
        ]))
    mons.sort(key=lambda x: (x["player"], x["pokemon"]))

    # ---- moves ---------------------------------------------------------------
    moves = []
    for name, x in mv.items():
        moves.append(OrderedDict([
            ("move", name), ("uses", x["uses"]), ("kos", x["kos"]), ("damage", r2(x["damage"])),
            ("damage_per_use", r2(r2(x["damage"]) / x["uses"]) if x["damage"] and x["uses"] else None),
            ("kos_per_10_uses", r2(10 * x["kos"] / x["uses"]) if x["damage"] and x["uses"] else None),
        ]))
    moves.sort(key=lambda x: (-x["uses"], x["move"]))

    totals = defaultdict(int)
    for (p, sp, m), u in tmv.items():
        totals[(p, sp)] += u
    teammoves = [OrderedDict([
        ("id", "%s|%s|%s" % k), ("player", k[0]), ("pokemon", k[1]), ("move", k[2]),
        ("uses", u), ("share", round(u / totals[(k[0], k[1])], 3)),
    ]) for k, u in tmv.items() if u]
    teammoves.sort(key=lambda x: (x["player"], x["pokemon"], -x["uses"], x["move"]))

    indirect = [OrderedDict([("source", k), ("hits", v["hits"]), ("damage", r2(v["damage"])),
                             ("faints", v["faints"])]) for k, v in ind.items()]
    indirect.sort(key=lambda x: (-x["damage"], x["source"]))
    weather = [{"weather": k, "games": v} for k, v in sorted(weather_games.items(), key=lambda kv: (-kv[1], kv[0]))]

    save("games", out_games)
    save("schedule", sched)
    save("teams", teams)
    save("mons", mons)
    save("draft", load(os.path.join(CONFIG, "draft.json"), []))
    save("moves", moves)
    save("teammoves", teammoves)
    save("gamemons", gm_rows, compact=True)
    save("gamemoves", gmv_rows, compact=True)
    save("indirect", indirect)
    save("weather", weather)
    for name, rows in aggregate_luck(games, out_games).items():
        save(name, rows)
    save("meta", {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "games": len(out_games)})
    print("Built %d games, %d players, %d Pokémon." % (len(out_games), len(teams), len(mons)))


if __name__ == "__main__":
    main()
