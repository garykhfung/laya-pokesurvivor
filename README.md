# Laya plays PokéSurvivor

A local controller for a checkout of [PokéSurvivor](https://github.com/garykhfung/pokesurvivor). It opens the game on this machine and plays a solo guest run. Nothing is uploaded, and this repo does not modify the game.

Two folders, two projects:

| Folder | What it is |
| --- | --- |
| `laya-mlx/` | This controller. It is the git project. |
| `pokesurvivor/` | Your copy of the game, with its own `.git`. It is listed in `.gitignore`, so commits here never include it. |

## Run

Needs an Apple Silicon Mac, Python 3.11 or newer, and Chrome. Clone the game once, next to `laya-mlx/`, then use a separate virtualenv so this does not change any other MLX install.

```bash
git clone https://github.com/garykhfung/pokesurvivor.git pokesurvivor
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
- **Item priority** is the held item to take when it is one of the options.

Goals pull in this order: a fleeing Meowth, a shiny, then a void. Experience and coins within 300px pull too, and a large experience gem outweighs a coin. If the feet have barely moved for 3 seconds, the next heading is taken unless that step meets a body or a shot. If the feet stay inside a 64px patch for 5 seconds and none of those goals is up, the heading changes and the walk leaves the patch.

Level-up spends the card on the slot that is firing:

1. Level that slot's attack. A buff fused into the same slot is leveled after the attack, while it is still below its top level.
2. When that attack is at top level or evolved, take a permanent stat: regeneration, experience, damage, max HP, cooldown, defense, pickup range, then speed. A one-shot heal is left for Laya.
3. When no permanent stat is offered and that attack is finished, take its evolution.
4. When the firing slot has no upgrade on the card, a permanent stat still wins. Anything left over is Laya's.

On a bond, the most upgraded attack is the anchor. Swords Dance is fused next, then Harden, then an attack of a different type.

For an item, the side-panel priority wins when it is offered. Otherwise the Lucky Egg is held for the first 10 minutes, then Choice Band over Life Orb. Any other offer is left to Laya.

## Update the game

The game checkout is independent. From `pokesurvivor/`:

```bash
git checkout main
git pull
```

Do that whenever you want a newer PokéSurvivor. This controller keeps using `dev.html` from that folder.
