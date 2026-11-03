# Plan: a fuller Pokémon value stat (Net HP)

Status: proposal, nothing built yet.

## The idea

Every game is decided by HP bars: a team that removes all six of the opponent's wins. So the
natural unit is the HP bar, and a Pokémon's value is the **HP it swung in its team's favour**:

```
Net HP = HP removed from opponents   (direct damage + damage from things it set up)
       + HP restored to its own side (its own healing and healing it gave allies)
       − HP it lost                  (damage it took, including fainting)
```

It has no fitted model, no weights and no replacement level. Every term is counted straight from
the replay. Summed over a team in one game, it comes close to that game's HP margin, so team totals
line up with results.

## What's wrong with the current "value"

`value = (direct damage dealt + 0.1 × turns on field) / team games`

- **Indirect damage gets no credit.** In the 49 games so far, about 80 HP bars came from
  sand, burn, poison, Stealth Rock, Leech Seed and similar, against 421 bars of direct damage, so
  about 16% of all damage goes uncredited.
- **Healing gets no credit.** That's 500+ heal events: Wish, Recover, drain, Leftovers, Leech Seed.
- **Bulk gets no credit.** A Pokémon that faints turn 1 and one that tanks four hits score
  nearly the same.
- **The 0.1 per turn is a made-up weight** that rewards staying in whether or not it helped.

## Who gets credit

Each HP change in the log goes to the Pokémon that caused it:

| Event | Credited to | Charged to |
| --- | --- | --- |
| Direct move damage | Attacker | Target |
| Hitting your own ally (spread moves) | — (counts against the attacker) | Ally |
| Burn / poison ticks | Pokémon that inflicted the status (Toxic Spikes: setter; Flame/Toxic Orb: nobody) | Victim |
| Sandstorm / hail ticks | Weather setter (Sand Stream / Snow Warning holder or Sandstorm/Hail user) | Victim |
| Stealth Rock / Spikes | Setter | Pokémon switching in |
| Leech Seed | Seeder, for both the drain and the heal | Victim |
| Rocky Helmet, Rough Skin, Iron Barbs | Holder | Attacker |
| Life Orb, recoil, confusion, Substitute, Belly Drum | Nobody | The Pokémon itself |
| Recover / Roost / Rest, Leftovers, Black Sludge, Sitrus, drain moves | The Pokémon itself | — |
| Wish, Heal Pulse, Healing Wish / Lunar Dance | The healer | — |
| Explosion / Memento-style self-KOs | Damage dealt as normal | Its own HP lost |

Damage is already capped at the HP the target had left, so overkill doesn't inflate anything.

## What's shown

- **Net HP per game** is the headline on the Draft tab, replacing value. Rank by per *team*
  game, as now (not being brought counts as 0), and also show per *brought* game.
- **Breakdown** in tooltips and the Pokémon table: Direct, Indirect, Healed, Taken, = Net.
  People can see *why* a Pokémon scores what it does, and anyone who disagrees with charging
  damage taken can read the first three columns on their own.
- **Comparison:** "vs average" (league average per brought game) and the existing "vs round".
  Both are plain averages, so no replacement level is needed.
- Show games brought next to every number, as now. With these sample sizes, that's more honest
  than shrinking or adding error bars.

## Known gaps (stated in the methodology note, not patched with weights)

- **Support that doesn't move HP** (Tailwind, Trick Room, Follow Me, Intimidate, screens,
  Fake Out, paralysis/sleep) only shows up indirectly, through teammates doing more and taking less.
  Redirectors are actually *charged* for the hits they soak. Rather than invent a weight, list
  these alongside: a "support" count (Tailwind/Trick Room set, attacks redirected, status
  inflicted) and the existing win % when brought.
- **Luck** (crits, misses) isn't removed. The Luck tab already covers it.

## Implementation

1. **`scripts/replay.py`**: record *who caused* each HP change.
   - Remember the status source per Pokémon (from `-status` after a move, or Toxic Spikes).
   - Remember the weather setter (`-weather … [of]`, or the last Sandstorm/Hail user), the
     hazard setters per side and the Leech Seed seeder per target.
   - Handle `-heal`: credit the healer (self, Wish user, drain attacker, seeder).
   - Add `indirect_dealt`, `healed` and `ff` (friendly fire) counters per Pokémon. `damage`,
     `taken` and the other existing counters stay as they are, so other tabs don't change.
2. **`scripts/build.py`**: add the new counters to `gamemons.json` and `mons.json`, along with
   `net_hp` and `net_hp_per_game`.
3. **`index.html`**: swap `value` for Net HP/game in the Draft tab (plot, rounds, tiers,
   classes, steals/busts). Add the breakdown columns and tooltips to the Pokémon tab, remove
   `D_TURN_W`, and rewrite the methodology note.
4. **Check**: hand-verify credits for two or three games (one sand game, one with Wish/Leech
   Seed). Assert in the build that each game's credited + uncredited HP equals total HP lost.

## Decisions for you

1. Should the headline subtract damage taken (Net), or show only HP added (Direct + Indirect +
   Healed) with Taken as a separate column? Recommendation: Net, because bulk matters and the
   breakdown covers anyone who disagrees.
2. Healing to full: Leftovers on a full-HP Pokémon logs nothing, so it isn't counted. That
   needs no decision, just a note.
