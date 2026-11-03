# Plan: a fuller Pokémon value stat (HP Impact)

Status: proposal, nothing built yet.

## The idea

A Pokémon helps its team in two ways, and both can be measured in HP bars:

```
HP Impact = HP Added      damage it caused (direct + set-up damage) + healing it gave
          + HP Prevented  damage it stopped or denied: screens, Intimidate, burns, Protect /
                          Wide Guard blocks, immunities, Helping Hand boosts, and turns it
                          made opponents lose (flinch, paralysis, sleep, freeze, Taunt)
```

**Damage taken is never subtracted.** Being targeted mostly reflects the opponent's choices, and
soaking hits is often the job. Staying healthy already pays off on its own, because more turns
in means more chances to add value. Damage taken is still shown as information.

**Weights are kept to a minimum.** Almost every number comes from one of three things (the
exceptions are the labelled heuristics for speed control and redirection, below):
1. **What the replay shows**, such as HP lost or healed.
2. **Gen 5 mechanics**, such as Intimidate (−1 Attack = ⅔ damage), burn (½ physical damage),
   doubles screens (about ⅔ damage) and Helping Hand (1.5×).
3. **League averages from our own games** for the value of a denied action: the average damage
   per use of a move, or the average damage per move of a Pokémon. These are averages, not a
   fitted model, so the small sample doesn't cause trouble.

## What's wrong with the current "value"

`value = (direct damage dealt + 0.1 × turns on field) / team games`

- **Indirect damage gets no credit.** In the 49 games so far, about 80 HP bars of sand, burn,
  poison, Stealth Rock and Leech Seed damage are credited to nobody (about 16% of all damage).
- **Healing gets no credit**, across 500+ heal events.
- **Support gets no credit**, apart from the arbitrary 0.1 per turn on the field.

## HP Added (direct, from the log)

| Event | Credited to |
| --- | --- |
| Direct move damage to opponents | Attacker (minus any it does to its own ally) |
| Burn / poison ticks | Pokémon that inflicted the status (Toxic Spikes: setter) |
| Sandstorm / hail ticks on opponents | Weather setter |
| Stealth Rock / Spikes | Setter |
| Leech Seed drain and heal | Seeder |
| Rocky Helmet, Rough Skin, Iron Barbs | Holder |
| Recover, Roost, Rest, Leftovers, Sitrus, drain moves | The Pokémon itself |
| Wish, Heal Pulse, Healing Wish, Lunar Dance | The healer |

Self-inflicted damage (Life Orb, recoil) isn't subtracted, for the same reason damage taken isn't.
It's shown alongside so heavy Life Orb users can be spotted.

## HP Prevented (support, valued with mechanics and league averages)

"Average damage" below always means the league average from our own replays: per use of that
move, split across its targets for spread moves, or per move for that Pokémon.

| Effect (log line) | How much HP it's worth | Credited to |
| --- | --- | --- |
| **Intimidate** (`-ability … Intimidate`, `-unboost atk`) | While the drop lasts: the physical damage that Pokémon dealt × ½ (−1 Attack leaves ⅔ of the damage, so it would have done half again as much) | Intimidator |
| **Reflect / Light Screen** | Physical or special damage allies took while it was up × ½ (doubles screens leave about ⅔) | Setter |
| **Burn** (on top of the ticks) | The burned Pokémon's physical damage while burned × 1 (halved, so it would have done the same again) | Burner |
| **Protect / Detect** (`-activate … Protect`) | Average damage of each attack that **targeted it** and was blocked. The log writes this line only when an attack hits the Protect, so a Protect nobody attacked earns nothing. A spread move counts once per protected target, not once per move | User |
| **Wide Guard / Quick Guard** | Average damage of each attack blocked, on every ally it covered | User |
| **Immunity on a switch-in or ability** (`-immune`, Levitate, Lightning Rod, Storm Drain, Volt/Water Absorb) | Average damage of the absorbed attack | Immune Pokémon |
| **Follow Me / Rage Powder** | Attacks drawn into an immunity earn the full average damage. Attacks it absorbs are never charged, and (heuristic, see below) each opposing single-target attack it draws that turn earns ½ that attack's average damage | Redirector |
| **Lost turns** (`cant` from flinch, paralysis, sleep, freeze, Taunt, Encore, Disable) | The victim's average damage per move (or the move it was locked into) | Whoever caused it (Fake Out user, paralyzer, sleeper, Taunter…) |
| **Helping Hand** | ⅓ of the boosted attack's damage (1.5× → the extra half) | Helper |

Healing on top of these:
- **Heal Bell / Aromatherapy**: each status cured earns its expected remaining cost (the burn
  ticks and halving, or the paralysis lost turns, at the league average rates).

## Heuristics for effects that are hard to measure

Some effects can't be valued from mechanics alone, so these use labelled heuristics. Each
setting is one named constant in `scripts/impact.py` and is listed in the methodology note.
Credit given to a setter here is a *share* taken from the ally's credit, never added on top, so
nothing is counted twice.

### Speed control: Tailwind, Trick Room, Icy Wind / Electroweb, Thunder Wave
The replay shows the order Pokémon moved in, so we can detect when the order was **flipped** by
the effect:

1. **Who is naturally faster.** For each pair of Pokémon, use the order they moved in on turns
   with no speed effects. If they never met in a clean turn, compare base Speed (this needs base
   stats added to `scripts/ref/species.json` by `refresh_refdata.py`). Priority moves are
   ignored, since they go first anyway.
2. **Flipped action.** On a turn the effect is active, an ally moves before an opponent that is
   naturally faster than it (or, under Trick Room, slower than it).
3. **Credit to the setter (or the Pokémon that slowed the opponent):**
   - **Denied action**: the flipped ally KOs that opponent before it moves → the setter gets
     the opponent's average damage per move, just like a flinch. This comes from what
     happened, not from a weight.
   - **Went-first share**: `SPEED_SHARE` (default **25%**) of the HP Added by each other
     flipped ally attack goes to the setter. The heuristic is that moving first is worth a
     meaningful but minority part of the hit.
   - Tailwind and Trick Room setters who also attack keep their own damage. They don't take a
     share from themselves.

### Redirection: Follow Me, Rage Powder
`REDIRECT_SHARE` (default **50%**): each opposing single-target attack that hits the redirector
on a turn it used Follow Me or Rage Powder earns half that attack's league-average damage. The
log doesn't record the original target, so the heuristic assumes about half of those attacks
were aimed at the partner. Attacks drawn into an immunity still earn full credit (above).

### Other set-up support (only if it shows up enough to matter)
Same "share of what it enabled" pattern, at `SPEED_SHARE`, for allies' attacks while the
effect is active: Gravity, Wide Guard turns (on top of blocks), and stat boosts given to allies
(Decorate-style, Coaching-style; rare in Gen 5). Start without these and add them only if a
Pokémon clearly reads too low.

### Tuning check
After building, list each support Pokémon's rank under the default shares and under half and
double them. If a ranking only holds at one setting, say so on the page.

Luck (crits, misses) isn't removed here. The Luck tab covers it.

## What's shown

- **HP Impact per game** replaces value on the Draft tab. Rank by per *team* game (not being
  brought counts as 0), with per *brought* game alongside.
- **Breakdown** in tooltips and the Pokémon table: Damage · Set-up damage · Healing ·
  Prevented · Speed/support share · = Impact, plus Taken and recoil as information. Readers can tell a sweeper from a
  wall from a support at a glance.
- **Comparison:** vs league average per brought game, and vs the average for its draft round.
  Both are plain averages, so no replacement level is needed.
- Games brought next to every number, as now.

## Implementation

1. **`scripts/replay.py`**: track who caused each HP change and each effect.
   - Status sources (who burned, poisoned, paralyzed or slept whom), the weather setter, hazard
     setters per side, the Leech Seed seeder.
   - Effects with durations: Intimidate drops (until switch-out or reset), screens (turns),
     burns, Tailwind, Trick Room, speed drops and paralysis (for speed control).
   - Move order within each turn, flagging priority moves, for the flipped-order check.
   - Blocked and absorbed attacks (`-activate` Protect / Wide Guard, `-immune`, redirect
     abilities), and the move and target that were blocked.
   - `cant` lines and their cause, Helping Hand (`-singleturn`) linked to the next ally attack.
   - Healing (`-heal`) with the healer.
   - Write a per-game event list. Existing counters stay as they are, so other tabs don't move.
2. **`scripts/impact.py`** (new): two passes over the season.
   - Pass 1: league averages (damage per use of each move per target, damage per move of
     each Pokémon). Fall back to the league average for that move's category when a move or
     Pokémon has too few uses (fewer than 3).
   - Pass 2: value every prevented effect with those averages and add up each Pokémon's
     components per game and per season.
3. **`scripts/build.py`**: write components and `impact` into `gamemons.json` and `mons.json`.
4. **`index.html`**: swap value for HP Impact/game across the Draft tab (plot, rounds, tiers,
   classes, steals/busts). Add the breakdown columns and tooltips to the Pokémon tab, remove
   `D_TURN_W`, and rewrite the methodology note.
5. **Check**: hand-verify two or three games (a sand game, a Trick Room or Follow Me game, one
   with Intimidate and screens). Assert in the build that HP Added (attributed damage plus what
   nobody caused) equals the total HP lost in each game.

## Decisions for you

1. Starting heuristic shares: `SPEED_SHARE` 25%, `REDIRECT_SHARE` 50%. Good starting points,
   or would you rather they were more or less generous to support?
2. Show Life Orb / recoil next to damage, or subtract it? Recommendation: show it, don't
   subtract, to be consistent with not subtracting damage taken.
