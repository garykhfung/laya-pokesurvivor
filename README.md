# Laya plays PokéSurvivor

A local controller for a checkout of [PokéSurvivor](https://github.com/garykhfung/pokesurvivor). It opens the game on this machine and plays a solo guest run. The aim is to stay alive until the Judgment at 30:00. Nothing is uploaded, and this repo does not modify the game.

Two folders, two projects:

| Folder | What it is |
| --- | --- |
| `laya-mlx/` | This controller. It is the git project. |
| `pokesurvivor/` | Your copy of the game, with its own `.git`. It is listed in `.gitignore`, so commits here never include it. |

## Run

Needs an Apple Silicon Mac, Python 3.11 or newer, and Chrome. Use a separate virtualenv so this does not change any other MLX install.

```bash
python3 -m venv .venv
.venv/bin/pip install -r laya-mlx/requirements.txt
.venv/bin/python laya-mlx/play.py
.venv/bin/python laya-mlx/play.py pikachu
.venv/bin/python laya-mlx/play.py 25
```

That serves `pokesurvivor/dev.html` at `http://127.0.0.1:8765` and opens it in a throwaway Chrome profile. The page asks which Pokémon to play and whether it is shiny, then which permanent stats to start with. Those stats are not bought with coins. Random Pokémon skips that screen, picks another Pokémon each run, and starts with every upgrade at its maximum. A name on the command line only fills the first field. While the run is going, a panel on the right shows each decision: the question, the response, and how long that response took. The Laya checkpoint downloads once into the Hugging Face cache.

`python laya-mlx/decide.py` checks the card and movement rules without loading the model.

## How it plays

`laya-mlx/decide.py` turns each snapshot from the game into one action: a direction, a card, or wait. Movement, level-up, bonding, and the usual item picks are fixed rules. Laya is asked only when a card is on screen and none of those rules takes it. Its answer is kept inside the labels that were offered.

Movement is eight directions, scored again on every snapshot. Walking into another Pokémon, or into a hostile shot over the next third of a second, is the worst score. That outranks a goal, a pickup, and closing in. Traps (poison, sticky, blast) and enemy areas on the ground come next. Walls and trees are given room so shots are not swallowed. If no direction is safe, it stands still.

Three side-panel settings change that score for the run:

- **Dodge distance** is how close a wild Pokémon may come before the walk steps away. The default is 150px.
- **Attack range** Long holds that distance even when the attack in use is short. Short closes until the attack can connect, and stops at the dodge distance. With no setting, an attack shorter than 180px still closes until it connects.
- **Item priority** starts at Focus Sash. Leftovers is the next choice for the first 10 minutes. After that, Choice Band comes ahead of Life Orb.

Goals pull in this order while the step stays clear of a body or a shot: an open void, a fleeing Meowth, then a shiny. The void comes first because it closes in 45 seconds and winning it awards the move merge. While a boss is alive, a Meowth and a shiny wait. Experience and coins within 300px pull too, and a large experience gem outweighs a coin. If the feet have barely moved for 3 seconds, the next heading is taken unless that step meets a body or a shot. If the feet stay inside a 64px patch for 5 seconds and none of those goals is up, the heading changes and the walk leaves the patch.

Level-up spends the card on the attack this run is keeping. With Attack range Long, that is the longest attack that still reaches from the dodge distance. A shorter move is left alone. A dash is left alone too: Quick Attack, Aerial Ace, Flame Charge, and U-turn carry the Pokémon into the horde. The run fires one of those only when no other attack is available, and it never spends a level on it.

1. Level that attack. A buff fused into the same slot is leveled after the attack, while it is still below its top level.
2. When that attack is at top level or evolved, take a permanent stat: experience, damage, max HP, regeneration, cooldown, defense, pickup range, then speed. A one-shot heal is left for Laya.
3. When no permanent stat is offered and that attack is finished, take its evolution.
4. When that attack has no upgrade on the card, a permanent stat still wins. Anything left over is Laya's.

On a bond, that same attack is the anchor. A physical attack fuses Swords Dance, a special attack fuses Nasty Plot, and Harden comes next, then an attack of a different type.

For an item, the side-panel priority wins when it is offered. Otherwise Focus Sash is held, then Leftovers, for the first 10 minutes. After that, Choice Band comes ahead of Life Orb. Any other offer is left to Laya. Random Pokémon starts on the species' longest attack that is not a dash, and its bans go to dashes first, then the shortest moves.

## Update the game

The game checkout is independent. From `pokesurvivor/`:

```bash
git checkout main
git pull
```

Do that whenever you want a newer PokéSurvivor. This controller keeps using `dev.html` from that folder.
