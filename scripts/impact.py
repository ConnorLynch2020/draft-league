"""HP Impact: how many HP bars a Pokémon added to its team or stopped the other team taking.

    HP Impact = HP Added      direct damage + set-up damage (status, weather, hazards, Leech Seed,
                              Rocky Helmet...) + healing given
              + HP Prevented  damage stopped or denied: Intimidate, screens, burns, Protect and
                              Wide Guard blocks, immunities, redirection, lost turns (flinch,
                              paralysis, sleep, freeze, Taunt...), speed control
              + Shares        Helping Hand and speed control take a share of an ally's damage

Damage taken and recoil are never subtracted. See docs/war-plan.md for the reasoning.

Two passes over the season:
  1. `track_game` walks one replay log and records who caused each HP change and each effect.
     Effects whose value isn't in the log (a blocked attack, a lost turn) are recorded as
     "deferred" credits that name what they are worth: the league average of a move, or of a
     Pokémon's turn.
  2. `season` works out those league averages from every game, values the deferred credits,
     runs the speed-control check (which needs every game to know who is naturally faster), and
     adds everything up per Pokémon per game.
"""
import json
import os
import re
from collections import defaultdict

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref")
with open(os.path.join(REF, "moves.json")) as _f:
    MOVES = json.load(_f)
try:
    with open(os.path.join(REF, "speed.json")) as _f:
        SPEED = json.load(_f)
except FileNotFoundError:  # pragma: no cover - refresh_refdata.py writes it
    SPEED = {}

# ---- heuristic settings (the only weights that aren't mechanics or league averages) ----------
SPEED_SHARE = 0.25     # share of a flipped ally attack's damage that goes to the speed setter
REDIRECT_SHARE = 0.50  # share of a drawn attack's average damage that goes to the redirector
MIN_SAMPLES = 3        # fewer uses than this and the league average for the category is used

# ---- Gen 5 mechanics -------------------------------------------------------------------------
SCREEN_EXTRA = 0.5     # doubles screens leave about 2/3 of the damage, so it would have been 1.5x
BURN_EXTRA = 1.0       # burn halves physical damage, so it would have been 2x
HELPING_HAND = 1 / 3   # 1.5x boost: the extra half is a third of the boosted damage

SINGLE_TARGET = {"normal", "any", "adjacentFoe"}
HEAL_SHARERS = {"Heal Pulse"}  # moves that heal someone other than the user
LOST_TURN_STATUS = {"par", "slp", "frz"}
SPEED_KINDS = ("Trick Room", "Tailwind", "paralysis", "speed drop")

# Labels for the breakdown of HP Prevented
PREVENTED_KINDS = ("intimidate", "screens", "burn", "protect", "guard", "immunity", "redirect",
                   "lost_turns", "speed")


def other(side):
    return "p2" if side == "p1" else "p1"


def stage_mult(s):
    s = max(-6, min(6, s))
    return (2 + s) / 2 if s >= 0 else 2 / (2 - s)


def hp_frac(s):
    s = s.split(" ")[0]
    if s == "0":
        return 0.0
    cur, mx = s.split("/")
    return int(cur) / int(mx)


def base_species(detail):
    from replay import base_species as _bs  # same folding as the main parser
    return _bs(detail)


def tags(parts):
    """['[from] item: Life Orb', '[of] p1a: Mew'] -> {'from': 'item: Life Orb', 'of': 'p1a: Mew'}."""
    out = {}
    for x in parts:
        m = re.match(r"\[(\w+)\]\s?(.*)", x)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def move_cat(move):
    return (MOVES.get(move) or {}).get("cat", "Status")


def is_fixed(move):
    return bool((MOVES.get(move) or {}).get("fixed"))


def is_priority(species, move):
    m = MOVES.get(move) or {}
    if m.get("pri", 0) != 0:
        return True
    return m.get("cat") == "Status" and bool((SPEED.get(species) or {}).get("prankster"))


class GameTracker:
    """Walk one replay log and record credits, deferred credits, samples and turn orders."""

    def __init__(self, log):
        self.log = log.split("\n")
        self.names = {}          # (side, nickname) -> species
        self.frac = {}           # (side, sp) -> HP fraction
        self.on_field = {}       # "p1a" -> (side, sp)
        self.status = {}         # (side, sp) -> (kind, source or None)
        self.atk = defaultdict(int)
        self.spe = defaultdict(int)
        self.spe_src = {}        # (side, sp) -> who lowered its Speed
        self.intim = defaultdict(list)  # (side, sp) -> Intimidators, one per stage still applied
        self.screens = {"p1": {}, "p2": {}}
        self.tailwind = {}       # side -> setter
        self.trick_room = None   # setter
        self.weather = None      # setter (or None when nobody on the field caused it)
        self.hazards = {"p1": {}, "p2": {}}  # side the hazard is on -> name -> setter
        self.seeded = {}         # (side, sp) -> seeder
        self.volatile_src = {}   # ((side, sp), "Taunt"/"Disable"/...) -> source
        self.bind = {}           # (side, sp) -> trapper
        self.heal_wish = {}      # side -> Healing Wish / Lunar Dance user
        self.last = None         # (side, sp, move, mid): the most recent move, as replay.py tracks it
        self.mid = 0
        self.mid_info = {}       # mid -> {user, move, cat, spread, crit:set, hh, turn}
        self.mid_dmg = defaultdict(float)     # mid -> direct damage to opponents
        self.mid_target_dmg = defaultdict(float)  # (mid, target) -> damage, the per-target samples
        self.turn = 0
        self.hit_this_turn = {}  # target -> last opposing attacker this turn (flinch source)
        self.redirect = {}       # side -> redirector, this turn
        self.guard = {}          # side -> Wide Guard / Quick Guard user, this turn
        self.hh = {}             # (side, sp) -> helper, this turn
        self.quick = set()       # (side, sp) whose Quick Claw / Custap went off this turn
        self.blocked = set()     # (mid, target) already credited as blocked or absorbed
        self.cure_by = None      # Heal Bell / Aromatherapy user while its cure lines are read
        self.just_switched = None
        self.prev_line = None
        self.intimidator = None
        self.intim_run = False

        self.credits = []        # (side, sp, comp, kind, amount)
        self.deferred = []       # dicts, valued in pass 2
        self.status_given = defaultdict(int)  # status kind -> times inflicted by an opponent
        self.mon_uses = defaultdict(int)      # species -> moves used
        self.mon_dmg = defaultdict(float)     # species -> direct damage to opponents
        self.turns = []          # per turn: snapshot + ordered actions, for speed control
        self.cur_turn = None
        self.lost_total = 0.0    # every HP loss in the game
        self.unattributed = 0.0  # HP lost that nobody on the other side caused
        self.added_total = 0.0   # HP lost credited to an opponent (direct + set-up)
        self.friendly = 0.0      # damage to a partner
        self.pain_split = 0.0    # set-up damage from Pain Split (not a -damage line)

    # -- helpers -----------------------------------------------------------------------------
    def who(self, s):
        m = re.match(r"(p[12])([a-d]?): ?(.*)", s or "")
        if not m:
            return None
        side, slot, name = m.group(1), m.group(2), m.group(3).strip()
        if slot and (side + slot) in self.on_field:
            occ = self.on_field[side + slot]
            if self.names.get((side, name)) == occ[1]:
                return occ
        sp = self.names.get((side, name))
        return (side, sp) if sp else None

    def credit(self, mon, comp, kind, amount, skind=None):
        if mon and amount:
            self.credits.append((mon[0], mon[1], comp, kind, amount, skind))

    def defer(self, mon, kind, value, mult=1.0, cap=None, heur=None, skind=None, comp="prev"):
        if mon:
            self.deferred.append({"mon": mon, "kind": kind, "value": value, "mult": mult, "cap": cap,
                                  "heur": heur, "skind": skind, "comp": comp})

    def status_source(self, mon, tg):
        """Who inflicted a status on `mon`, or None if it was self-inflicted / unknown."""
        frm = tg.get("from", "")
        if frm.startswith("item:") or frm == "move: Rest":
            return None
        if "of" in tg:
            src = self.who(tg["of"])
        elif self.just_switched == mon and "Toxic Spikes" in self.hazards[mon[0]]:
            src = self.hazards[mon[0]]["Toxic Spikes"]
        elif self.last:
            src = self.last[:2]
        else:
            src = None
        return src if src and src[0] != mon[0] else None

    def snapshot(self):
        mods = {}
        for mon in self.on_field.values():
            if self.frac.get(mon, 1.0) <= 0:
                continue
            st = self.status.get(mon)
            mods[mon] = {"par": (st[1] or "self") if st and st[0] == "par" else None,
                         "spe": self.spe[mon], "spe_src": self.spe_src.get(mon)}
        return {"turn": self.turn, "tr": self.trick_room, "tw": dict(self.tailwind), "mods": mods,
                "acts": [], "kos": []}

    def lose(self, mon, new):
        old = self.frac.get(mon, 1.0)
        lost = max(0.0, old - new)
        self.frac[mon] = new
        self.lost_total += lost
        return lost, old

    # -- main loop -----------------------------------------------------------------------------
    def run(self):
        for line in self.log:
            if not line.startswith("|"):
                continue
            p = line.split("|")
            t = p[1]
            if t in ("", "t:", "debug", "c", "j", "l", "raw", "upkeep", "-hint", "-message"):
                continue
            name = ("on_" + t[1:]) if t.startswith("-") else ("line_" + t)
            handler = getattr(self, name, None)
            if t != "-curestatus":
                self.cure_by = None
            if t != "-unboost":
                self.intim_run = False
            if handler:
                handler(p)
            if t not in ("switch", "drag", "replace", "-damage", "-heal", "-status", "-ability", "-unboost"):
                self.just_switched = None
            self.prev_line = p
        if self.cur_turn:
            self.turns.append(self.cur_turn)
        return self

    def line_turn(self, p):
        if self.cur_turn:
            self.turns.append(self.cur_turn)
        self.turn = int(p[2])
        self.hit_this_turn, self.redirect, self.guard, self.hh = {}, {}, {}, {}
        self.quick = set()
        self.cur_turn = self.snapshot()

    def line_switch(self, p):
        side, slot = p[2][:2], p[2][:3]
        name = p[2].split(":", 1)[1].strip()
        sp = base_species(p[3])
        mon = (side, sp)
        self.names[(side, name)] = sp
        old = self.on_field.get(slot)
        if old:
            self.clear_volatiles(old)
        self.on_field[slot] = mon
        self.clear_volatiles(mon)
        self.frac[mon] = hp_frac(p[4])
        self.just_switched = mon

    line_drag = line_switch

    def line_replace(self, p):
        side, slot = p[2][:2], p[2][:3]
        name = p[2].split(":", 1)[1].strip()
        sp = base_species(p[3])
        self.names[(side, name)] = sp
        self.on_field[slot] = (side, sp)

    def clear_volatiles(self, mon):
        self.atk[mon] = 0
        self.spe[mon] = 0
        self.spe_src.pop(mon, None)
        self.intim.pop(mon, None)
        self.seeded.pop(mon, None)
        self.bind.pop(mon, None)
        for k in [k for k in self.volatile_src if k[0] == mon]:
            del self.volatile_src[k]

    def line_move(self, p):
        mon = self.who(p[2])
        move = p[3]
        self.mid += 1
        if not mon:
            return
        tg = tags(p[5:])
        self.last = (mon[0], mon[1], move, self.mid)
        spread = "spread" in tg or (MOVES.get(move) or {}).get("tgt") not in SINGLE_TARGET
        self.mid_info[self.mid] = {"user": mon, "move": move, "cat": move_cat(move), "spread": spread,
                                   "crit": set(), "hh": self.hh.pop(mon, None), "turn": self.turn}
        self.mon_uses[mon[1]] += 1
        if move in ("Healing Wish", "Lunar Dance"):
            self.heal_wish[mon[0]] = mon
        if self.cur_turn is not None and self.turn > 0:
            prio = is_priority(mon[1], move) or mon in self.quick
            self.cur_turn["acts"].append({"mon": mon, "mid": self.mid, "prio": prio})

    def line_cant(self, p):
        mon = self.who(p[2])
        if not mon:
            return
        reason = p[3]
        if self.cur_turn is not None and self.turn > 0:
            self.cur_turn["acts"].append({"mon": mon, "mid": None, "prio": False})
        src, skind = None, None
        if reason == "flinch":
            src = self.hit_this_turn.get(mon)
        elif reason in LOST_TURN_STATUS:
            st = self.status.get(mon)
            src = st[1] if st and st[0] == reason else None
            skind = reason
        elif reason.startswith("move: ") or reason in ("Disable", "Encore", "Taunt"):
            src = self.volatile_src.get((mon, reason.replace("move: ", "")))
        else:
            return
        if src and src[0] != mon[0]:
            self.defer(src, "lost_turns", ("mon", mon[1]), skind=skind)

    def on_activate(self, p):
        mon = self.who(p[2])
        what = p[3] if len(p) > 3 else ""
        tg = tags(p[4:])
        if what in ("item: Quick Claw", "item: Custap Berry"):
            if mon:
                self.quick.add(mon)
            return
        if what in ("move: Heal Bell", "move: Aromatherapy"):
            self.cure_by = mon
            return
        if what in ("move: Whirlpool", "move: Fire Spin", "move: Wrap", "move: Bind", "move: Sand Tomb",
                    "move: Magma Storm", "move: Clamp", "move: Infestation"):
            src = self.who(tg.get("of", ""))
            if mon and src:
                self.bind[mon] = src
            return
        if not mon or not self.last:
            return
        a_side, a_sp, move, mid = self.last
        if a_side == mon[0] or move_cat(move) == "Status" or (mid, mon) in self.blocked:
            return
        if what == "move: Protect":
            self.blocked.add((mid, mon))
            self.defer(mon, "protect", ("move", move), cap=self.frac.get(mon, 1.0))
        elif what in ("move: Wide Guard", "move: Quick Guard"):
            self.blocked.add((mid, mon))
            self.defer(self.guard.get(mon[0], mon), "guard", ("move", move), cap=self.frac.get(mon, 1.0))
        elif what in ("ability: Lightning Rod", "ability: Storm Drain"):
            self.blocked.add((mid, mon))
            self.defer(mon, "immunity", ("move", move), cap=self.frac.get(mon, 1.0))

    def on_immune(self, p):
        mon = self.who(p[2])
        if not mon or not self.last:
            return
        a_side, a_sp, move, mid = self.last
        if a_side == mon[0] or move_cat(move) == "Status" or (mid, mon) in self.blocked:
            return
        if (MOVES.get(move) or {}).get("ohko"):
            return
        self.blocked.add((mid, mon))
        self.defer(mon, "immunity", ("move", move), cap=self.frac.get(mon, 1.0))

    def on_singleturn(self, p):
        mon = self.who(p[2])
        what = p[3]
        tg = tags(p[4:])
        if not mon:
            return
        if what == "Helping Hand":
            helper = self.who(tg.get("of", ""))
            if helper:
                self.hh[mon] = helper
        elif what in ("move: Follow Me", "move: Rage Powder"):
            self.redirect[mon[0]] = mon
        elif what in ("Wide Guard", "Quick Guard", "move: Wide Guard", "move: Quick Guard"):
            self.guard[mon[0]] = mon

    def on_crit(self, p):
        mon = self.who(p[2])
        if mon and self.last:
            self.mid_info[self.last[3]]["crit"].add(mon)

    def on_ability(self, p):
        mon = self.who(p[2])
        if mon and p[3] == "Intimidate":
            self.intimidator = mon

    def on_boost(self, p, sign=1):
        mon = self.who(p[2])
        if not mon:
            return
        stat, n = p[3], int(p[4]) * sign
        if stat == "atk":
            if sign < 0 and self.prev_line and (
                    (self.prev_line[1] == "-ability" and self.prev_line[3] == "Intimidate")
                    or (self.prev_line[1] == "-unboost" and self.intim_run)):
                src = self.intimidator
                if src and src[0] != mon[0]:
                    lowered = max(0, min(-n, self.atk[mon] + 6))
                    self.intim[mon].extend([src] * lowered)
                self.intim_run = True
            else:
                self.intim_run = False
            self.atk[mon] = max(-6, min(6, self.atk[mon] + n))
        elif stat == "spe":
            if sign < 0 and self.last and self.last[0] != mon[0]:
                self.spe_src[mon] = self.last[:2]
            self.spe[mon] = max(-6, min(6, self.spe[mon] + n))

    def on_unboost(self, p):
        self.on_boost(p, -1)

    def on_setboost(self, p):
        mon = self.who(p[2])
        if mon and p[3] in ("atk", "spe"):
            getattr(self, p[3])[mon] = int(p[4])

    def on_clearallboost(self, p):
        for mon in self.on_field.values():
            self.atk[mon] = self.spe[mon] = 0
            self.intim.pop(mon, None)

    def on_clearboost(self, p):
        mon = self.who(p[2])
        if mon:
            self.atk[mon] = self.spe[mon] = 0
            self.intim.pop(mon, None)

    def on_clearnegativeboost(self, p):
        mon = self.who(p[2])
        if mon:
            self.atk[mon] = max(0, self.atk[mon])
            self.spe[mon] = max(0, self.spe[mon])
            self.intim.pop(mon, None)

    def on_status(self, p):
        mon = self.who(p[2])
        if not mon:
            return
        kind = "psn" if p[3] == "tox" else p[3]
        src = self.status_source(mon, tags(p[4:]))
        self.status[mon] = (kind, src)
        if src:
            self.status_given[kind] += 1

    def on_curestatus(self, p):
        mon = self.who(p[2])
        kind = "psn" if p[3] == "tox" else p[3]
        if mon:
            st = self.status.pop(mon, None)
        else:
            st = None
        if self.cure_by and st and st[1] and st[1][0] != self.cure_by[0]:
            self.defer(self.cure_by, "cure", ("status", kind), comp="heal")

    def on_start(self, p):
        mon = self.who(p[2])
        if not mon:
            return
        what = p[3].replace("move: ", "")
        tg = tags(p[5:] if what == "Disable" else p[4:])
        if what == "Leech Seed" and self.last:
            self.seeded[mon] = self.last[:2]
        elif what in ("Taunt", "Disable", "Encore", "Torment", "Imprison"):
            src = self.who(tg.get("of", "")) if "of" in tg else (self.last[:2] if self.last else None)
            if src and src[0] != mon[0]:
                self.volatile_src[(mon, what)] = src

    def on_sidestart(self, p):
        side = p[2][:2]
        what = p[3].replace("move: ", "")
        setter = self.last[:2] if self.last else None
        if what in ("Reflect", "Light Screen"):
            self.screens[side][what] = setter
        elif what == "Tailwind":
            self.tailwind[side] = setter
        elif what in ("Stealth Rock", "Spikes", "Toxic Spikes"):
            self.hazards[side][what] = setter

    def on_sideend(self, p):
        side = p[2][:2]
        what = p[3].replace("move: ", "")
        self.screens[side].pop(what, None)
        self.hazards[side].pop(what, None)
        if what == "Tailwind":
            self.tailwind.pop(side, None)

    def on_fieldstart(self, p):
        if "Trick Room" in p[2]:
            tg = tags(p[3:])
            self.trick_room = self.who(tg.get("of", "")) or (self.last[:2] if self.last else None)

    def on_fieldend(self, p):
        if "Trick Room" in p[2]:
            self.trick_room = None

    def on_weather(self, p):
        tg = tags(p[3:])
        if p[2] == "none":
            self.weather = None
        elif "upkeep" in tg:
            pass
        elif "of" in tg:
            self.weather = self.who(tg["of"])
        else:
            self.weather = self.last[:2] if self.last else None

    def on_sethp(self, p):
        mon = self.who(p[2])
        if not mon:
            return
        new = hp_frac(p[3])
        old = self.frac.get(mon, 1.0)
        if new < old:
            lost, _ = self.lose(mon, new)
            user = self.last[:2] if self.last else None
            if user and user[0] != mon[0]:
                self.credit(user, "setup", "Pain Split", lost)
                self.added_total += lost
                self.pain_split += lost
            else:
                self.unattributed += lost
        else:
            self.frac[mon] = new
            self.credit(mon, "heal", "Pain Split", new - old)

    def on_heal(self, p):
        mon = self.who(p[2])
        if not mon:
            return
        new = hp_frac(p[3])
        old = self.frac.get(mon, 1.0)
        self.frac[mon] = new
        gain = max(0.0, new - old)
        tg = tags(p[4:])
        frm = tg.get("from", "")
        healer = mon
        if frm == "move: Wish" and "wisher" in tg:
            sp = self.names.get((mon[0], tg["wisher"]))
            healer = (mon[0], sp) if sp else mon
        elif frm in ("move: Healing Wish", "move: Lunar Dance"):
            healer = self.heal_wish.get(mon[0]) or mon
        elif frm.startswith("ability: Volt Absorb") or frm.startswith("ability: Water Absorb"):
            if self.last and self.last[0] != mon[0] and (self.last[3], mon) not in self.blocked:
                self.blocked.add((self.last[3], mon))
                self.defer(mon, "immunity", ("move", self.last[2]), cap=old)
        elif not frm:
            prev = self.prev_line
            if prev and prev[1] == "-damage" and "[from] Leech Seed" in prev:
                victim = self.who(prev[2])
                healer = self.seeded.get(victim, mon)
            elif self.last and self.last[2] in HEAL_SHARERS and self.last[0] == mon[0]:
                healer = self.last[:2]
        self.credit(healer, "heal", frm.replace("item: ", "").replace("move: ", "").replace("ability: ", "") or
                    (self.last[2] if self.last else "heal"), gain)

    def on_damage(self, p):
        mon = self.who(p[2])
        if not mon:
            return
        new = hp_frac(p[3])
        lost, old = self.lose(mon, new)
        tg = tags(p[4:])
        frm = tg.get("from", "")
        if frm:
            src, skind = None, None
            if frm in ("item: Life Orb", "Recoil"):
                self.credit(mon, "recoil", frm, lost)
            elif frm in ("brn", "psn"):
                st = self.status.get(mon)
                src = st[1] if st else None
                skind = "brn" if frm == "brn" else "psn"
            elif frm in ("Sandstorm", "Hail"):
                src = self.weather
            elif frm in ("Stealth Rock", "Spikes"):
                src = self.hazards[mon[0]].get(frm)
            elif frm == "Leech Seed":
                src = self.seeded.get(mon) or self.who(tg.get("of", ""))
            elif frm.startswith("move: ") and mon in self.bind:
                src = self.bind[mon]
            elif "of" in tg:
                src = self.who(tg["of"])
            if src and src[0] != mon[0]:
                self.credit(src, "setup", frm.replace("item: ", "").replace("ability: ", ""), lost, skind=skind)
                self.added_total += lost
            else:
                self.unattributed += lost
            return
        if not self.last:
            self.unattributed += lost
            return
        a_side, a_sp, move, mid = self.last
        att = (a_side, a_sp)
        if a_side == mon[0]:
            # the damage component is damage to opponents minus damage to its own partner
            self.friendly += lost
            self.credit(att, "dmg", "friendly fire", -lost)
            self.credit(att, "ff", "friendly fire", lost)
            return
        self.added_total += lost
        self.credit(att, "dmg", move, lost)
        self.mon_dmg[a_sp] += lost
        self.mid_dmg[mid] += lost
        self.mid_target_dmg[(mid, mon)] += lost
        if any(x.get("kind") == "flinch" for x in (MOVES.get(move) or {}).get("sec", [])):
            self.hit_this_turn[mon] = att  # the flinch source is the last hit that could flinch
        info = self.mid_info.get(mid, {})
        crit = mon in info.get("crit", ())
        cat = info.get("cat") or move_cat(move)
        room = old - lost  # HP the target had left: a stronger hit can't have taken more
        if cat == "Physical" and not is_fixed(move):
            # Intimidate (crits ignore the attacker's lowered Attack)
            srcs = self.intim.get(att)
            if srcs and not crit:
                s = self.atk[att]
                k = min(len(srcs), 6 - s)
                extra = min(room, lost * (stage_mult(s + k) / stage_mult(s) - 1))
                for src in srcs[:k]:
                    self.credit(src, "prev", "intimidate", extra / k)
            st = self.status.get(att)
            if st and st[0] == "brn" and st[1] and st[1][0] != a_side:
                self.credit(st[1], "prev", "burn", min(room, lost * BURN_EXTRA), skind="brn")
        if cat in ("Physical", "Special") and not is_fixed(move) and not crit:
            screen = self.screens[mon[0]].get("Reflect" if cat == "Physical" else "Light Screen")
            if screen and screen[0] == mon[0]:
                self.credit(screen, "prev", "screens", min(room, lost * SCREEN_EXTRA))
        # Follow Me / Rage Powder drew this single-target attack (heuristic: half were aimed at the partner)
        if self.redirect.get(mon[0]) == mon and not info.get("spread") and (mid, mon) not in self.blocked:
            self.blocked.add((mid, mon))
            self.defer(mon, "redirect", ("move", move), mult=REDIRECT_SHARE, heur="redirect")
        if info.get("hh") and info["hh"][0] == a_side:
            share = lost * HELPING_HAND
            self.credit(info["hh"], "share", "Helping Hand", share)
            self.credit(att, "share", "Helping Hand", -share)
        if new == 0 and self.cur_turn is not None:
            self.cur_turn["kos"].append({"mid": mid, "victim": mon})


def track_game(log):
    return GameTracker(log).run()


# ---- pass 2 ------------------------------------------------------------------------------------
def league_averages(trackers):
    """Average damage per target hit for each move, and per move used for each species."""
    by_move = defaultdict(lambda: [0.0, 0])
    by_cat = defaultdict(lambda: [0.0, 0])
    uses, dmg = defaultdict(int), defaultdict(float)
    for tr in trackers:
        for (mid, _), d in tr.mid_target_dmg.items():
            mv = tr.mid_info[mid]["move"]
            by_move[mv][0] += d
            by_move[mv][1] += 1
            cat = tr.mid_info[mid]["cat"]
            by_cat[cat][0] += d
            by_cat[cat][1] += 1
        for sp, n in tr.mon_uses.items():
            uses[sp] += n
        for sp, d in tr.mon_dmg.items():
            dmg[sp] += d
    cat_avg = {c: s / n for c, (s, n) in by_cat.items() if n}
    all_uses = sum(uses.values())
    turn_all = sum(dmg.values()) / all_uses if all_uses else 0.0

    def move_avg(move):
        s, n = by_move.get(move, (0.0, 0))
        if n >= MIN_SAMPLES:
            return s / n
        cat = move_cat(move)
        return cat_avg.get(cat, 0.0) if cat != "Status" else 0.0

    def turn_avg(sp):
        n = uses.get(sp, 0)
        return dmg.get(sp, 0.0) / n if n >= MIN_SAMPLES else turn_all

    return move_avg, turn_avg, {"move": {m: [round(s / n, 4), n] for m, (s, n) in sorted(by_move.items()) if n},
                                "category": {c: round(v, 4) for c, v in cat_avg.items()},
                                "per_move": round(turn_all, 4)}


def natural_order(trackers):
    """(a, b) -> True if species a is naturally faster than b (moves first on a clean turn)."""
    obs = defaultdict(int)
    for tr in trackers:
        for t in tr.turns:
            if t["tr"] or t["tw"]:
                continue
            acts = [a for a in t["acts"] if not a["prio"]]
            seen = []
            for a in acts:
                ma = t["mods"].get(a["mon"])
                if not ma or ma["par"] or ma["spe"]:
                    continue
                for b in seen:
                    mb = t["mods"].get(b)
                    if b[0] != a["mon"][0] and mb and not mb["par"] and not mb["spe"]:
                        obs[(b[1], a["mon"][1])] += 1
                if a["mon"] not in seen:
                    seen.append(a["mon"])

    def faster(a, b):
        x, y = obs.get((a, b), 0), obs.get((b, a), 0)
        if x != y:
            return x > y
        if x:
            return None
        sa, sb = (SPEED.get(a) or {}).get("spe"), (SPEED.get(b) or {}).get("spe")
        if sa is None or sb is None or sa == sb:
            return None
        return sa > sb

    return faster


def speed_credits(tr, faster, turn_avg, speed_share):
    """Credits for Pokémon whose speed control let an ally act before a naturally faster foe."""
    out = []  # (mon, comp, kind, amount, heur)
    for t in tr.turns:
        mods = t["mods"]
        effect_turn = t["tr"] or t["tw"] or any(m["par"] or m["spe"] for m in mods.values())
        if not effect_turn:
            continue
        order = {}
        for i, a in enumerate(t["acts"]):
            order.setdefault(a["mon"], (i, a))
        ko_by = {}
        for k in t["kos"]:
            ko_by.setdefault(k["victim"], k["mid"])
        mid_pos = {a["mid"]: i for i, a in enumerate(t["acts"]) if a["mid"]}
        for i, a in enumerate(t["acts"]):
            A = a["mon"]
            if a["prio"] or not a["mid"] or A not in mods:
                continue
            denied, share = [], None
            for B, mb in mods.items():
                if B[0] == A[0]:
                    continue
                bo = order.get(B)
                if bo:
                    if bo[0] < i or bo[1]["prio"]:
                        continue  # B went first anyway, or used priority
                elif B not in ko_by or mid_pos.get(ko_by[B], 10 ** 6) < i:
                    continue  # B switched out, or fainted before A moved
                if not faster(B[1], A[1]):
                    continue
                ma = mods[A]
                setter, kind = None, None
                if t["tr"]:
                    setter, kind = t["tr"], "Trick Room"
                elif t["tw"].get(A[0]) and not t["tw"].get(B[0]):
                    setter, kind = t["tw"][A[0]], "Tailwind"
                elif mb["par"] and not ma["par"]:
                    setter, kind = mb["par"], "paralysis"
                elif mb["spe"] < 0 and ma["spe"] <= 0:
                    setter, kind = mb["spe_src"], "speed drop"
                if not setter or setter == "self" or setter[0] != A[0]:
                    continue  # the flip was the opponent's doing, or nobody's (Choice Scarf, a boost)
                if ko_by.get(B) == a["mid"]:
                    denied.append((setter, "prev", "speed", turn_avg(B[1]), None))
                elif not share and setter != A:
                    share = (setter, kind)
            if denied:
                out += denied  # an attack that KO'd a faster foe earns the denied action, not a share
            elif share:
                s = tr.mid_dmg.get(a["mid"], 0.0) * speed_share
                if s:
                    out.append((share[0], "share", share[1], s, "speed"))
                    out.append((A, "share", share[1], -s, "speed"))
    return out


COMPONENTS = ("dmg", "setup", "heal", "prev", "share", "recoil", "ff")


def evaluate(trackers, speed_share=SPEED_SHARE, redirect_share=REDIRECT_SHARE):
    """Value every credit. Returns per-game dicts: (side, species) -> component totals + breakdown."""
    move_avg, turn_avg, averages = league_averages(trackers)
    faster = natural_order(trackers)
    games = []
    status_val = defaultdict(float)
    for tr in trackers:
        rows = defaultdict(lambda: defaultdict(float))
        prev_by = defaultdict(lambda: defaultdict(float))

        def add(mon, comp, kind, amount, skind=None):
            rows[mon][comp] += amount
            if comp == "prev":
                prev_by[mon][kind] += amount
            if skind:
                status_val[skind] += amount

        for side, sp, comp, kind, amount, skind in tr.credits:
            add((side, sp), comp, kind, amount, skind)
        later = []
        for d in tr.deferred:
            kind, val = d["value"]
            if kind == "status":
                later.append(d)
                continue
            v = move_avg(val) if kind == "move" else turn_avg(val)
            mult = d["mult"]
            if d["heur"] == "redirect":
                mult = redirect_share
            v *= mult
            if d["cap"] is not None:
                v = min(v, d["cap"])
            add(d["mon"], d["comp"], d["kind"], v, d["skind"])
        for mon, comp, kind, amount, _ in speed_credits(tr, faster, turn_avg, speed_share):
            add(mon, comp, kind, amount)
        games.append((tr, rows, prev_by, later))
    given = defaultdict(int)
    for tr in trackers:
        for k, n in tr.status_given.items():
            given[k] += n
    status_avg = {k: status_val[k] / given[k] for k in given if given[k]}
    out = []
    for tr, rows, prev_by, later in games:
        for d in later:
            # Heal Bell / Aromatherapy: what an opposing status of that kind cost on average
            rows[d["mon"]]["heal"] += status_avg.get(d["value"][1], 0.0)
        out.append({"rows": rows, "prev_by": prev_by})
    averages["status"] = {k: round(v, 4) for k, v in status_avg.items()}
    return out, averages


def impact_of(c):
    return c["dmg"] + c["setup"] + c["heal"] + c["prev"] + c["share"]
