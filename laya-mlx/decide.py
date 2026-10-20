"""Turn a PokéSurvivor snapshot into a Laya question, then shield the answer.

The model only sees a short English description. The game already marks which
directions are free of walls and imminent hazards; if Laya's top choice is not
one of those, the next-highest safe label is used instead.

    python laya-mlx/decide.py
"""

import copy
import json
import math
import time
from pathlib import Path

# Phrases, not single words: Laya follows these more reliably.
MOVE_LABELS = [
    "stand still",
    "move north",
    "move north-east",
    "move east",
    "move south-east",
    "move south",
    "move south-west",
    "move west",
    "move north-west",
]

# Level-up stat titles are Spanish in the game. The sentence glosses them.
STAT_GLOSS = {
    "Vigor": "more max HP",
    "Potencia": "more damage",
    "Carrera": "faster movement",
    "Reflejos": "shorter move cooldown",
    "Coraza": "less damage taken",
    "Síntesis": "HP regeneration",
    "Imán": "pick up experience from farther away",
    "Aprendizaje": "more experience",
    "Baya Zidra": "heal a large amount now",
}

_BEARINGS = [
    "north", "north-east", "east", "south-east",
    "south", "south-west", "west", "north-west",
]


def load_agent():
    """Local Apple Silicon checkpoint. Downloads once into the Hugging Face cache."""
    import laya_mlx as laya
    return laya.load("aac6fef/laya-mlx")


def _bearing(dx, dy):
    # Game Y grows south, so north is negative dy.
    ang = math.atan2(float(dx), -float(dy))
    i = int((ang + math.pi / 8) % (2 * math.pi) / (math.pi / 4))
    return _BEARINGS[i]


def _range(dist):
    dist = float(dist)
    if dist < 36:
        return "on top of you"
    if dist < 80:
        return "very close"
    if dist < 160:
        return "close"
    if dist < 280:
        return "nearby"
    return "in the distance"


def _gloss(label, detail):
    hint = STAT_GLOSS.get(label)
    bits = [b for b in (hint, detail) if b]
    return (label + (" (" + ". ".join(bits) + ")" if bits else ""))


def describe(snap):
    """One or two sentences. Numbers stay out except HP percent and a few clocks."""
    hp = float(snap.get("hp") or 0)
    mx = float(snap.get("maxHp") or 1) or 1
    pct = round(100 * hp / mx)
    parts = [
        "You are %s in a Pokémon survival run. "
        "HP %d percent. Level %d. Time %d seconds."
        % (snap.get("mon") or "your Pokémon", pct, int(snap.get("level") or 1), int(snap.get("time") or 0))
    ]
    if snap.get("liquid"):
        parts.append("You are standing in %s, which hurts you." % snap["liquid"])
    foes = snap.get("enemies") or []
    if not foes:
        parts.append("No wild Pokémon are close.")
    else:
        bits = []
        for e in foes[:4]:
            kind = "boss " if e.get("boss") else ""
            bits.append(
                "a %s%s %s to the %s"
                % (kind, e.get("name") or "wild Pokémon", _range(e.get("dist") or 0),
                   _bearing(e.get("dx") or 0, e.get("dy") or 0))
            )
        parts.append("Threats: " + "; ".join(bits) + ".")
    hazards = snap.get("hazards") or []
    if hazards:
        bits = []
        for h in hazards[:3]:
            bits.append(
                "a %s zone %s to the %s, striking in %.1f seconds"
                % (h.get("kind") or "danger", _range(h.get("dist") or 0),
                   _bearing(h.get("dx") or 0, h.get("dy") or 0), float(h.get("delay") or 0))
            )
        parts.append("Danger on the ground: " + "; ".join(bits) + ".")
    else:
        parts.append("No danger zone is about to hit.")
    safe = [s for s in (snap.get("safe") or []) if s in MOVE_LABELS]
    parts.append("Safe directions: " + ", ".join(safe) + "." if safe else "No direction is safe.")
    phase = snap.get("phase")
    if phase in ("levelup", "item", "bond"):
        intro = {
            "levelup": "Choose one level-up reward.",
            "item": "Choose which held item to keep.",
            "bond": "Choose which moves to fuse.",
        }[phase]
        lines = []
        for i, c in enumerate(snap.get("cards") or [], 1):
            lines.append("Option %d: %s." % (i, _gloss(c.get("label") or "", c.get("detail") or "")))
        parts.append(intro + " " + " ".join(lines))
    return " ".join(parts)


def questions_for(snap):
    phase = snap.get("phase")
    if phase in ("levelup", "item", "bond"):
        labels = []
        for c in snap.get("cards") or []:
            lab = c.get("label") or ""
            if lab and lab not in labels:
                labels.append(lab)
        if not labels:
            return None
        return {
            "pick": {
                "type": "choice",
                "instructions": "Which option should the Pokémon take?",
                "criteria": labels,
            }
        }
    return {
        "move": {
            "type": "choice",
            "instructions": "Which way should the Pokémon move to stay alive?",
            "criteria": MOVE_LABELS,
        },
        "stay_hit": {
            "type": "noul",
            "instructions": "Is the Pokémon about to be hit if it stays where it is?",
        },
    }


# Same 40px lookahead as safeDirs in bridge.js. Game Y grows south.
_STEP = (18, 40)
_VECS = {
    "move north": (0, -1),
    "move north-east": (1, -1),
    "move east": (1, 0),
    "move south-east": (1, 1),
    "move south": (0, 1),
    "move south-west": (-1, 1),
    "move west": (-1, 0),
    "move north-west": (-1, -1),
}


# One step per death. Laya picks the knob and the direction; these bounds pick the number.
STEPS = {"kite": 10, "short": 10, "contact_gap": 4, "trap_weight": 200, "egg_until": 30}
CLAMP = {
    "kite": (80, 260),
    "short": (48, 320),
    "contact_gap": (8, 64),
    "trap_weight": (200, 4000),
    "egg_until": (120, 900),
}
# Phrases, not single words: Laya follows these more reliably.
KNOB_LABEL = {
    "kite": "kite distance",
    "contact_gap": "contact gap",
    "short": "closing distance",
    "trap_weight": "trap penalty",
    "egg_until": "lucky egg time",
    "item_late": "held item order",
    "stat_order": "stat order",
}
WAY_LABEL = {
    "increase": "increase it",
    "decrease": "decrease it",
    "earlier": "move it earlier",
    "later": "move it later",
}
LABEL_KNOB = {label: key for key, label in KNOB_LABEL.items()}
LABEL_WAY = {label: key for key, label in WAY_LABEL.items()}
RUNS = Path(__file__).resolve().parent.parent / "runs"
RUN_ID = None
_RUN_N = 0


def default_policy():
    """Today's constants. A death moves one of these, then the next life reads the file."""
    return {
        "id": "p0",
        "parent": None,
        "kite": 150,
        "short": 180,
        "contact_gap": 28,
        "trap_weight": 1600,
        "egg_until": 600,
        "stat_order": ["rgn", "xp", "atk", "hp", "cd", "def", "mag", "spd"],
        "item_late": ["choiceband", "lifeorb"],
    }


POLICY = default_policy()


def set_policy(policy):
    global POLICY
    POLICY = copy.deepcopy(policy)


def begin_life():
    global RUN_ID, _RUN_N
    _RUN_N += 1
    RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-%d" % _RUN_N
    return RUN_ID


def save_policy(path, policy):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")


def load_policy(path=None):
    """Read runs/policy.json. The first run writes today's constants."""
    path = Path(path) if path else RUNS / "policy.json"
    policy = default_policy()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for key in policy:
            if key in data:
                policy[key] = data[key]
        set_policy(policy)
    else:
        set_policy(policy)
        save_policy(path, POLICY)
    begin_life()
    return POLICY


def _attack_reach(snap):
    """Shortest live attack in the firing slot. 0 when every shot already reaches."""
    reaches = []
    short = float(POLICY["short"])
    for m in snap.get("moves") or []:
        if not m.get("active") or m.get("kind") == "buff":
            continue
        reach = float(m.get("reach") or 0)
        if 0 < reach < short:
            reaches.append(reach)
    return min(reaches) if reaches else 0.0


def _close_score(ux, uy, foes, reach, pr):
    """Step toward the nearest foe until it is inside `reach`, and stop at contact.

    The probe is one tick long, and shorter when a full step would walk into them.
    """
    if not foes:
        return 0.0
    near = min(foes, key=lambda e: math.hypot(float(e.get("dx") or 0), float(e.get("dy") or 0)))
    score = 0.0
    for e in foes:
        dx, dy = float(e.get("dx") or 0), float(e.get("dy") or 0)
        now = math.hypot(dx, dy)
        body = float(e.get("r") or 14) + pr + 4
        step = 16.0
        if e is near and dx * ux + dy * uy > 0 and now > body:
            step = min(step, now - body)
        dist = math.hypot(dx - ux * step, dy - uy * step)
        if dist < body:
            score -= 1200 + (body - dist) * 10
        elif e is near and dist <= reach:
            score += 500
        elif e is near and dist < now:
            score += (now - dist) * 50
    return score


# Whole trail inside this radius of its center counts as stuck.
_STUCK_R = 64
_STUCK_S = 5


def _escape(snap, safe):
    """Leave a patch the feet have not left for 5 seconds, on a different heading.

    ponytail: one centroid. Opposite of the failed heading when that center is underfoot.
    """
    pts = [p for p in (snap.get("trail") or []) if isinstance(p, dict)]
    if not any(float(p.get("dt") or 0) >= _STUCK_S for p in pts):
        return None
    xs = [float(p.get("x") or 0) for p in pts]
    ys = [float(p.get("y") or 0) for p in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    if max(math.hypot(x - mx, y - my) for x, y in zip(xs, ys)) > _STUCK_R:
        return None
    ax = float(snap.get("x") or 0) - mx
    ay = float(snap.get("y") or 0) - my
    heading = snap.get("heading") or ""
    if math.hypot(ax, ay) < 12:
        hx, hy = _VECS.get(heading, (0, 0))
        ax, ay = -float(hx), -float(hy)
    best, best_score = None, -1e18
    for label in safe:
        vx, vy = _VECS[label]
        length = math.hypot(vx, vy) or 1
        score = vx / length * ax + vy / length * ay
        if label == heading:
            score -= 1000
        if score > best_score:
            best, best_score = label, score
    return best


def _circle_depth(dx, dy, cx, cy, rad):
    return rad - math.hypot(float(dx) - cx, float(dy) - cy)


def _beam_depth(h, cx, cy, pr):
    ang = float(h.get("angle") or 0)
    ca, sa = math.cos(ang), math.sin(ang)
    px, py = cx - float(h.get("dx") or 0), cy - float(h.get("dy") or 0)
    along = px * ca + py * sa
    if along < -pr or along > float(h.get("len") or 0) + pr:
        return 0.0
    side = abs(-px * sa + py * ca)
    half = float(h.get("w") or 0) / 2 + pr * 0.6
    return half - side if side <= half else 0.0


def _danger(snap, cx, cy, pr):
    """Positive when this point sits in a trap blast or an enemy area."""
    depth = 0.0
    for h in snap.get("hazards") or []:
        if h.get("kind") == "beam":
            depth += _beam_depth(h, cx, cy, pr)
        else:
            d = _circle_depth(h.get("dx") or 0, h.get("dy") or 0, cx, cy, float(h.get("r") or 0) + pr * 0.6)
            if d > 0:
                depth += d
    for t in snap.get("traps") or []:
        d = _circle_depth(t.get("dx") or 0, t.get("dy") or 0, cx, cy, float(t.get("r") or 74) + pr * 0.4)
        if d > 0:
            depth += d
    return depth


_GOAL_RANK = {"thief": 3, "shiny": 2, "rift": 1}


def _goal(snap):
    """Meowth first (it flees), then a shiny, then a void."""
    best = None
    best_key = None
    for g in snap.get("goals") or []:
        kind = g.get("kind")
        if kind not in _GOAL_RANK:
            continue
        key = (_GOAL_RANK[kind], -float(g.get("dist") or 0))
        if best_key is None or key > best_key:
            best, best_key = g, key
    return best


def steer(snap):
    """Keep walking. Dodge contact, stay off walls, close for a short attack, then collect.

    ponytail: eight fixed directions, rescored every snapshot. Short attacks use one
    16px probe toward the nearest foe. A real pathfinder would be the upgrade if it
    starts trapping itself in crowds.
    """
    safe = []
    for s in snap.get("safe") or []:
        if s in _VECS and s not in safe:
            safe.append(s)
    if not safe:
        return "stand still"
    nudge = snap.get("nudge")
    if nudge in safe:
        return nudge
    goal = _goal(snap)
    if not goal:
        leave = _escape(snap, safe)
        if leave:
            return leave
    foes = snap.get("enemies") or []
    loot = snap.get("pickups") or []
    blocks = snap.get("blocks") or []
    pr = float(snap.get("r") or 14)
    heading = snap.get("heading") or ""
    reach = _attack_reach(snap)
    kite = float(POLICY["kite"])
    gap_extra = float(POLICY["contact_gap"])
    trap_weight = float(POLICY["trap_weight"])
    gx = float(goal.get("dx") or 0) if goal else 0.0
    gy = float(goal.get("dy") or 0) if goal else 0.0
    best, best_score = safe[0], -1e18
    for label in safe:
        vx, vy = _VECS[label]
        length = math.hypot(vx, vy) or 1
        ux, uy = vx / length, vy / length
        score = 18 if label == heading else 0
        if reach:
            score += _close_score(ux, uy, foes, reach, pr)
        for ahead in _STEP:
            cx, cy = ux * ahead, uy * ahead
            depth = _danger(snap, cx, cy, pr)
            if depth > 0:
                score -= trap_weight + depth * 8
            if goal:
                score += (math.hypot(gx, gy) - math.hypot(gx - cx, gy - cy)) * 40
            if not reach:
                for e in foes:
                    if e.get("shiny") or e.get("thief"):
                        continue
                    dist = math.hypot(float(e.get("dx") or 0) - cx, float(e.get("dy") or 0) - cy)
                    gap = float(e.get("r") or 14) + pr + gap_extra
                    if dist < gap:
                        score -= 1200 + (gap - dist) * 10
                    elif dist < kite:
                        score -= (kite - dist) * (3 if e.get("boss") else 1.6)
            for b in blocks:
                bx, by = float(b.get("dx") or 0), float(b.get("dy") or 0)
                dist = math.hypot(bx - cx, by - cy)
                gap = float(b.get("r") or 16) + pr + 48
                if dist < gap:
                    score -= (gap - dist) * 12
            for p in loot:
                px, py = float(p.get("dx") or 0), float(p.get("dy") or 0)
                now = math.hypot(px, py)
                if now > 300:
                    continue
                kind = p.get("kind")
                w = 2 if kind == "xpBig" else 1.5 if kind == "coin" else 1
                score += w * (now - math.hypot(px - cx, py - cy)) * 4
        if score > best_score:
            best, best_score = label, score
    return best


def shield(choice, probabilities, safe):
    """Keep `choice` when it is safe. Otherwise the safe label with the next-highest probability.

    An empty safe list cannot be improved, so the raw choice is returned.
    ponytail: ties break by label text; a scored shortlist would need the model's own order.
    """
    allowed = []
    for s in safe or []:
        if isinstance(s, str) and s not in allowed:
            allowed.append(s)
    if not allowed:
        return choice
    if choice in allowed:
        return choice
    ranked = sorted((probabilities or {}).items(), key=lambda kv: (-float(kv[1]), str(kv[0])))
    for label, _p in ranked:
        if label in allowed:
            return label
    return allowed[0]


SWORDS = "swords-dance"
HARDEN = "harden"


def _moves(snap):
    return snap.get("moves") or []


def _best_attack(moves):
    atks = [m for m in moves if m.get("kind") != "buff"]
    if not atks:
        return None
    return max(atks, key=lambda m: (m.get("lvl") or 0, bool(m.get("active")), m.get("name") or ""))


def _using(moves):
    """The attack in the slot that is firing. Buff-only slot falls back to its lead."""
    active = [m for m in moves if m.get("active") and m.get("kind") != "buff"]
    if active:
        return max(active, key=lambda m: (m.get("lvl") or 0, bool(m.get("lead"))))
    leads = [m for m in moves if m.get("active") and m.get("lead")]
    return leads[0] if leads else _best_attack(moves)


def _find(cards, pred):
    for i, card in enumerate(cards):
        if pred(card):
            return i
    return None


def _attack_cards(cards, moves):
    by_id = {m.get("id"): m for m in moves}
    out = []
    for i, card in enumerate(cards):
        m = by_id.get(card.get("move"))
        if m and m.get("kind") != "buff":
            out.append((i, m))
        elif card.get("fresh") and card.get("kind") not in ("buff", "", None):
            out.append((i, card))
    return out


# Lucky Egg is the early hold. The late order lives on the policy.
_EXP_ITEMS = ("luckyegg",)


def _full_bond(moves):
    return any((m.get("group") or 0) >= 4 for m in moves)


def _permanent(cards):
    by_stat = {}
    for i, card in enumerate(cards):
        sid = card.get("stat")
        if sid and sid not in by_stat:
            by_stat[sid] = i
    for sid in POLICY["stat_order"]:
        if sid in by_stat:
            return by_stat[sid]
    return None


def _grouped_buff(moves, mid):
    """True when this buff shares a slot with an attack."""
    slots = {m.get("slot") for m in moves if m.get("id") == mid and (m.get("group") or 0) > 1}
    return any(m.get("slot") in slots and m.get("kind") != "buff" for m in moves)


def _bond_lead(moves):
    for m in moves:
        if (m.get("group") or 0) >= 4 and m.get("lead") and m.get("kind") != "buff":
            return m
    for m in moves:
        if (m.get("group") or 0) >= 4 and m.get("kind") != "buff":
            return m
    return None


def level_choice(cards, moves):
    """Upgrade the move in use, else a different-type 4th attack.

    A buff is taken only as an upgrade of one already fused to an attack.
    Once four moves are fused, take a permanent stat instead and keep upgrading only that fused attack.
    """
    if _full_bond(moves):
        hit = _permanent(cards)
        if hit is not None:
            return hit
        lead = _bond_lead(moves)
        if lead:
            return _find(cards, lambda c: c.get("move") == lead.get("id") and (c.get("upgrade") or "→" in (c.get("label") or "")))
        return None
    using = _using(moves)
    if using:
        hit = _find(cards, lambda c: c.get("move") == using.get("id") and (c.get("upgrade") or "→" in (c.get("label") or "")))
        if hit is not None:
            return hit
    for mid in (SWORDS, HARDEN):
        if not _grouped_buff(moves, mid):
            continue
        hit = _find(cards, lambda c, mid=mid: c.get("move") == mid and (c.get("upgrade") or "→" in (c.get("label") or "")))
        if hit is not None:
            return hit
    slots = {m.get("slot") for m in moves}
    primary = _best_attack(moves)
    if len(slots) >= 3 and primary:
        hit = _find(
            cards,
            lambda c: c.get("fresh") and c.get("kind") not in ("buff", "", None) and c.get("type") and c.get("type") != primary.get("type"),
        )
        if hit is not None:
            return hit
    return None


def bond_choice(cards, moves, bond):
    """Anchor the most upgraded attack, fuse Swords Dance then Harden, then a different-type attack."""
    if not bond:
        return None
    by_id = {m.get("id"): m for m in moves}
    mode, step = bond.get("mode"), bond.get("step") or 1
    group = [by_id[i] for i in (bond.get("group") or []) if i in by_id]
    have = {m.get("id") for m in group}

    def named(mid):
        return _find(cards, lambda c, mid=mid: c.get("move") == mid)

    def strongest(pred=None):
        opts = [(i, m) for i, m in _attack_cards(cards, moves) if not pred or pred(m)]
        if not opts:
            return None
        return max(opts, key=lambda im: (im[1].get("lvl") or 0, im[1].get("name") or ""))[0]

    if mode == "pair" and step == 1:
        return strongest()
    if mode == "pair" and step == 2:
        for mid in (SWORDS, HARDEN):
            hit = named(mid)
            if hit is not None:
                return hit
        first = by_id.get(bond.get("first"))
        ftype = first.get("type") if first and first.get("kind") != "buff" else None
        if ftype:
            hit = strongest(lambda m: m.get("type") != ftype)
            if hit is not None:
                return hit
        return strongest()
    for mid in (SWORDS, HARDEN):
        if mid not in have:
            hit = named(mid)
            if hit is not None:
                return hit
    anchor = next((m for m in group if m.get("kind") != "buff"), None)
    if anchor and (len(group) >= 3 or {SWORDS, HARDEN} <= have):
        hit = strongest(lambda m: m.get("type") != anchor.get("type"))
        if hit is not None:
            return hit
    return strongest()


def item_choice(cards, time):
    """Lucky Egg for the first 10 minutes, then Choice Band over Life Orb."""
    ids = [c.get("item") or "" for c in cards]
    if not any(ids):
        return None
    order = _EXP_ITEMS if float(time or 0) < float(POLICY["egg_until"]) else tuple(POLICY["item_late"])
    for iid in order:
        for i, got in enumerate(ids):
            if got == iid:
                return i
    return None


def build_choice(snap):
    cards = snap.get("cards") or []
    if not cards:
        return None
    phase = snap.get("phase")
    if phase == "levelup":
        return level_choice(cards, _moves(snap))
    if phase == "item":
        return item_choice(cards, snap.get("time") or 0)
    if phase == "bond":
        return bond_choice(cards, _moves(snap), snap.get("bond"))
    return None


def _note(source, question, result, scene, raw=None):
    note = {"source": source, "question": question, "scene": scene, "result": result or ""}
    if raw and raw != result:
        note["raw"] = raw
    return note


def decide(agent, snap):
    phase = snap.get("phase")
    scene = describe(snap) if phase in ("playing", "levelup", "item", "bond") else ""
    forced = build_choice(snap)
    if forced is not None:
        cards = snap.get("cards") or []
        label = cards[forced].get("label")
        return {
            "action": "card",
            "index": forced,
            "label": label,
            "shielded": False,
            "note": _note("rule", "Which option should the Pokémon take?", label, scene),
        }
    if phase not in ("levelup", "item", "bond"):
        if phase != "playing":
            return {"action": "wait"}
        label = steer(snap)
        return {
            "action": "move",
            "label": label,
            "shielded": False,
            "note": _note("rule", "Which way should the Pokémon move to stay alive?", label, scene),
        }
    qs = questions_for(snap)
    if not qs:
        return {"action": "wait"}
    result = agent.predict(describe(snap), qs)
    answers = result["answers"]
    if "pick" not in answers:
        return {"action": "wait"}
    ans = answers["pick"]
    labels = qs["pick"]["criteria"]
    label = shield(ans.get("choice"), ans.get("probabilities"), labels)
    index = labels.index(label) if label in labels else 0
    return {
        "action": "card",
        "label": label,
        "index": index,
        "shielded": label != ans.get("choice"),
        "note": _note("laya", qs["pick"]["instructions"], label, scene, ans.get("choice")),
    }


def cause_of(snap):
    """First matching tag. The game never names the killer, so this is inferred.

    Enemy shots are not in the snapshot yet, so a hit with no overlap stays unseen.
    """
    if snap.get("liquid"):
        return "liquid"
    pr = float(snap.get("r") or 14)
    for trap in snap.get("traps") or []:
        dist = math.hypot(float(trap.get("dx") or 0), float(trap.get("dy") or 0))
        if dist <= float(trap.get("r") or 74):
            return "trap:" + (trap.get("kind") or "trap")
    for hazard in snap.get("hazards") or []:
        if hazard.get("kind") == "beam":
            if _beam_depth(hazard, 0, 0, pr) > 0:
                return "hazard:beam"
        else:
            rad = float(hazard.get("r") or 0) + pr * 0.6
            if _circle_depth(hazard.get("dx") or 0, hazard.get("dy") or 0, 0, 0, rad) > 0:
                return "hazard:" + (hazard.get("kind") or "zone")
    nearest, nearest_d, close = None, 1e18, 0
    for foe in snap.get("enemies") or []:
        dist = float(foe.get("dist") or math.hypot(float(foe.get("dx") or 0), float(foe.get("dy") or 0)))
        if dist < nearest_d:
            nearest, nearest_d = foe, dist
        if dist < 80:
            close += 1
    if nearest is not None and nearest_d < float(nearest.get("r") or 14) + pr + 8:
        return "contact:" + (nearest.get("name") or "wild Pokémon")
    if close >= 4:
        return "crowd"
    return "unseen"


def _melee(death, policy):
    short = float(policy["short"])
    for move in death.get("moves") or []:
        if not move.get("active") or move.get("kind") == "buff":
            continue
        reach = float(move.get("reach") or 0)
        if move.get("kind") == "melee" or 0 < reach < short:
            return True
    return False


def knobs_for(death, policy):
    """Knobs this death is allowed to move. shot_weight stays out until shots are recorded."""
    cause = death.get("cause") or ""
    out = []

    def add(key):
        if key not in out:
            out.append(key)

    if cause.startswith("contact") or cause == "crowd":
        add("kite")
        add("contact_gap")
    if cause.startswith("contact") and _melee(death, policy):
        add("short")
    if cause == "liquid" or cause.startswith("trap") or cause.startswith("hazard"):
        add("trap_weight")
    survived = float(death.get("time") or 0)
    item = death.get("item") or ""
    egg = float(policy["egg_until"])
    if (survived < egg and item != "luckyegg") or (survived >= egg and item == "luckyegg"):
        add("egg_until")
    if survived >= egg:
        add("item_late")
    if death.get("full_bond") or _full_bond(death.get("moves") or []):
        add("stat_order")
    return out


def ways_for(knobs):
    ways = []
    if any(key in STEPS for key in knobs):
        ways.extend(["increase", "decrease"])
    if any(key in ("stat_order", "item_late") for key in knobs):
        ways.extend(["earlier", "later"])
    return ways


def death_text(death, policy, knobs):
    parts = [
        "The Pokémon died from %s. It survived %d seconds."
        % (death.get("cause") or "an unknown hit", int(float(death.get("time") or 0)))
    ]
    samples = death.get("last_hp") or []
    if samples:
        parts.append("Recent HP: %s." % ", ".join(str(int(p.get("hp") or 0)) for p in samples))
    bits = []
    for key in knobs:
        val = policy[key]
        shown = ", ".join(val) if isinstance(val, list) else str(val)
        bits.append("%s is %s" % (KNOB_LABEL[key], shown))
    if bits:
        parts.append("Current settings: %s." % ". ".join(bits))
    return " ".join(parts)


def _child_id(policy_id):
    tail = str(policy_id or "p0")[1:]
    n = int(tail) if tail.isdigit() else 0
    return "p%d" % (n + 1)


def _nudge_list(items, focus, earlier):
    items = list(items)
    if focus not in items:
        return None
    i = items.index(focus)
    j = i - 1 if earlier else i + 1
    if j < 0 or j >= len(items):
        return None
    items[i], items[j] = items[j], items[i]
    return items


def _thin_stat(death, order):
    stats = death.get("stats") or {}
    return min(order, key=lambda sid: (int(stats.get(sid) or 0), order.index(sid)))


def _focus_item(order):
    if "choiceband" in order:
        return "choiceband"
    return order[0] if order else None


def apply_step(policy, knob, way, death):
    """One clamped step. No change (already at the bound, or a bad pair) leaves the policy."""
    nxt = copy.deepcopy(policy)
    if knob in STEPS:
        if way == "earlier":
            way = "increase"
        elif way == "later":
            way = "decrease"
        if way not in ("increase", "decrease"):
            return None, None
        delta = STEPS[knob] if way == "increase" else -STEPS[knob]
        raw = float(nxt[knob]) + delta
        lo, hi = CLAMP[knob]
        val = min(hi, max(lo, raw))
        if isinstance(nxt[knob], int):
            val = int(val)
        if val == nxt[knob]:
            return None, None
        edit = {"knob": knob, "from": nxt[knob], "to": val}
        nxt[knob] = val
    elif knob in ("stat_order", "item_late"):
        if way == "increase":
            way = "earlier"
        elif way == "decrease":
            way = "later"
        if way not in ("earlier", "later"):
            return None, None
        old = list(nxt[knob])
        focus = _thin_stat(death, old) if knob == "stat_order" else _focus_item(old)
        moved = _nudge_list(old, focus, way == "earlier")
        if not moved:
            return None, None
        edit = {"knob": knob, "from": old, "to": moved}
        nxt[knob] = moved
    else:
        return None, None
    nxt["parent"] = policy["id"]
    nxt["id"] = _child_id(policy["id"])
    return nxt, edit


def ask_laya(agent, death, policy):
    """Ask the loaded Laya checkpoint which knob, and which way. A failed call changes nothing."""
    knobs = knobs_for(death, policy)
    if not knobs:
        return None, None
    ways = ways_for(knobs)
    questions = {
        "knob": {
            "type": "choice",
            "instructions": "Which one setting should change after this death?",
            "criteria": [KNOB_LABEL[key] for key in knobs],
        },
        "way": {
            "type": "choice",
            "instructions": "Which way should that setting move?",
            "criteria": [WAY_LABEL[key] for key in ways],
        },
    }
    try:
        result = agent.predict(death_text(death, policy, knobs), questions)
        answers = result["answers"]
    except Exception:
        return None, None
    if "knob" not in answers or "way" not in answers:
        return None, None
    knob_ans, way_ans = answers["knob"], answers["way"]
    knob = LABEL_KNOB.get(shield(knob_ans.get("choice"), knob_ans.get("probabilities"), questions["knob"]["criteria"]))
    way = LABEL_WAY.get(shield(way_ans.get("choice"), way_ans.get("probabilities"), questions["way"]["criteria"]))
    if not knob or not way:
        return None, None
    return apply_step(policy, knob, way, death)


def _write_death(path, death):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(death) + "\n")


def _fill_edit(path, edit):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return
    last = json.loads(lines[-1])
    last["edit"] = edit
    lines[-1] = json.dumps(last)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_death(agent, snap, runs=None):
    """Write the death line, then ask Laya. The policy file changes only when the edit lands."""
    global RUN_ID
    runs = Path(runs) if runs is not None else RUNS
    if not RUN_ID:
        begin_life()
    policy = copy.deepcopy(POLICY)
    death = {
        "kind": "death",
        "run": RUN_ID,
        "policy": policy["id"],
        "time": float(snap.get("time") or 0),
        "level": int(snap.get("level") or 1),
        "item": snap.get("item") or "",
        "full_bond": bool(snap.get("full_bond")) or _full_bond(snap.get("moves") or []),
        "cause": cause_of(snap),
        "last_hp": snap.get("last_hp") or [],
        "moves": snap.get("moves") or [],
        "stats": snap.get("stats") or {},
        "edit": None,
    }
    path = runs / "deaths.jsonl"
    _write_death(path, death)
    new_policy, edit = ask_laya(agent, death, policy)
    if edit:
        set_policy(new_policy)
        save_policy(runs / "policy.json", new_policy)
        _fill_edit(path, edit)
    print("[bot] death", death["cause"], edit or "unchanged")
    begin_life()
    return {"edit": edit, "policy": POLICY["id"]}


def _check():
    got = shield(
        "move north",
        {"move north": 0.7, "move west": 0.2, "stand still": 0.1},
        ["move west", "stand still"],
    )
    assert got == "move west", got
    assert shield("move west", {"move north": 0.9, "move west": 0.1}, ["move west"]) == "move west"
    text = describe({
        "phase": "playing",
        "hp": 40, "maxHp": 90, "level": 2, "time": 12,
        "enemies": [{"name": "Pidgey", "dist": 50, "dx": 0, "dy": -50, "boss": False}],
        "hazards": [],
        "safe": ["move west", "stand still"],
    })
    assert "Pidgey" in text and "north" in text and "move west" in text, text
    noted = decide(None, {
        "phase": "playing", "hp": 40, "maxHp": 90, "level": 2, "time": 12,
        "enemies": [], "hazards": [], "pickups": [], "safe": ["move east"],
    })
    assert noted["action"] == "move" and noted["note"]["result"] == noted["label"], noted
    assert noted["note"]["source"] == "rule" and noted["note"]["question"].startswith("Which way"), noted
    assert "HP" in noted["note"]["scene"], noted
    ember = {"id": "ember", "name": "Ascuas", "kind": "projectile", "type": "fire", "lvl": 3, "slot": 0, "lead": True, "active": True, "group": 1}
    water = {"id": "water-gun", "name": "Pistola Agua", "kind": "projectile", "type": "water", "lvl": 1, "slot": 1, "lead": True, "active": False, "group": 1}
    swords = {"id": "swords-dance", "name": "Danza Espada", "kind": "buff", "type": "normal", "lvl": 1, "slot": 2, "lead": True, "active": False, "group": 1}
    harden = {"id": "harden", "name": "Fortaleza", "kind": "buff", "type": "normal", "lvl": 1, "slot": 3, "lead": True, "active": False, "group": 1}
    moves = [ember, water, swords, harden]
    up = [
        {"label": "Danza Espada", "move": "swords-dance", "kind": "buff", "type": "normal", "fresh": True},
        {"label": "Ascuas", "move": "ember", "kind": "projectile", "type": "fire", "upgrade": True},
    ]
    assert level_choice(up, moves) == 1
    assert level_choice(up[:1], moves) is None
    fused_buff = [dict(ember, slot=0, group=2), dict(swords, slot=0, group=2, active=False)]
    assert level_choice([
        {"label": "Danza Espada", "move": "swords-dance", "upgrade": True},
    ], fused_buff) == 0
    fourth = [
        {"label": "Lanzallamas", "move": "flamethrower", "kind": "cone", "type": "fire", "fresh": True},
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water", "fresh": True},
    ]
    assert level_choice(fourth, [ember, swords, harden]) == 1
    pair = [
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
        {"label": "Ascuas", "move": "ember", "kind": "projectile", "type": "fire"},
    ]
    assert bond_choice(pair, moves, {"mode": "pair", "step": 1, "group": [], "first": ""}) == 1
    step2 = [
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
        {"label": "Danza Espada", "move": "swords-dance", "kind": "buff", "type": "normal"},
        {"label": "Fortaleza", "move": "harden", "kind": "buff", "type": "normal"},
    ]
    assert bond_choice(step2, moves, {"mode": "pair", "step": 2, "group": [], "first": "ember"}) == 1
    assert bond_choice(step2, moves, {"mode": "add", "step": 1, "group": ["ember", "swords-dance"], "first": ""}) == 2
    flame = {"id": "flamethrower", "name": "Lanzallamas", "kind": "cone", "type": "fire", "lvl": 1, "slot": 4, "lead": True, "active": False, "group": 1}
    off = [
        {"label": "Lanzallamas", "move": "flamethrower", "kind": "cone", "type": "fire"},
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
    ]
    assert bond_choice(off, moves + [flame], {"mode": "add", "step": 1, "group": ["ember", "swords-dance", "harden"], "first": ""}) == 1
    fused = [dict(ember, group=4)]
    assert level_choice([
        {"label": "Ascuas", "move": "ember", "upgrade": True},
        {"label": "Síntesis", "stat": "rgn"},
    ], fused) == 1
    assert level_choice([
        {"label": "Potencia", "stat": "atk"},
        {"label": "Aprendizaje", "stat": "xp"},
    ], fused) == 1
    assert level_choice([
        {"label": "Lanzallamas", "move": "flamethrower", "fresh": True, "kind": "cone"},
        {"label": "Ascuas", "move": "ember", "upgrade": True},
    ], fused) == 1
    assert item_choice([
        {"item": "leftovers"}, {"item": "luckyegg"},
    ], 120) == 1
    assert item_choice([
        {"item": "luckyegg"}, {"item": "choiceband"},
    ], 120) == 0
    assert item_choice([
        {"item": "luckyegg"}, {"item": "choiceband"},
    ], 600) == 1
    assert item_choice([
        {"item": "choiceband"}, {"item": "lifeorb"},
    ], 700) == 0
    assert item_choice([{"item": "leftovers"}, {"item": "shellbell"}], 100) is None
    ways = [s for s in MOVE_LABELS if s != "stand still"]
    assert steer({"safe": ways, "r": 14, "enemies": [{"dx": 0, "dy": -50, "r": 16}], "pickups": []}) == "move south"
    assert steer({"safe": ways, "r": 14, "enemies": [], "pickups": [{"kind": "coin", "dx": 100, "dy": 0}]}) == "move east"
    assert steer({"safe": ways, "r": 14, "heading": "move west", "enemies": [], "pickups": []}) == "move west"
    dodged = steer({
        "safe": ways, "r": 14,
        "enemies": [{"dx": 36, "dy": 0, "r": 16}],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
    })
    assert dodged in ("move west", "move south-west", "move north-west"), dodged
    assert steer({"safe": ["stand still"], "enemies": [], "pickups": []}) == "stand still"
    assert steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "blocks": [{"dx": 30, "dy": 0, "r": 16}],
    }) == "move west"
    melee = [{"id": "tackle", "kind": "melee", "lvl": 1, "active": True, "reach": 48}]
    assert steer({
        "safe": ways, "r": 14, "moves": melee, "pickups": [],
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "moves": melee,
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
        "pickups": [{"kind": "coin", "dx": 0, "dy": 80}],
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "moves": melee, "pickups": [],
        "enemies": [{"dx": 0, "dy": -20, "r": 16}],
    }) != "move north"
    far = [{"id": "ember", "kind": "projectile", "lvl": 1, "active": True, "reach": 496}]
    assert steer({
        "safe": ways, "r": 14, "moves": far, "pickups": [],
        "enemies": [{"dx": 0, "dy": -50, "r": 16}],
    }) == "move south"

    def trail(pts, secs=5):
        n = len(pts)
        return [
            {"x": x, "y": y, "dt": 0 if n == 1 else secs * (n - 1 - i) / (n - 1)}
            for i, (x, y) in enumerate(pts)
        ]

    assert steer({
        "safe": ways, "r": 14, "x": 100, "y": 100, "heading": "move east",
        "trail": trail([(100, 100), (112, 96), (90, 108), (104, 88), (100, 100)]),
        "enemies": [{"dx": 0, "dy": -40, "r": 16}],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
    }) == "move west"
    assert steer({
        "safe": ways, "r": 14, "x": 100, "y": 0, "heading": "move south",
        "trail": trail([(100, 30), (108, 40), (92, 48), (100, 36), (104, 44)]),
        "enemies": [], "pickups": [],
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "x": 200, "y": 0, "heading": "move east",
        "trail": trail([(i * 40, 0) for i in range(6)]),
        "enemies": [], "pickups": [],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "x": 100, "y": 100, "heading": "move east",
        "trail": trail([(100, 100), (104, 100), (100, 102)], 2),
        "enemies": [], "pickups": [],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "enemies": [],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
        "traps": [{"kind": "boom", "dx": 40, "dy": 0, "r": 74}],
    }) == "move west"
    assert steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "hazards": [{"kind": "zone", "dx": 0, "dy": -30, "r": 50, "delay": 1.2}],
    }) == "move south"
    assert steer({
        "safe": ways, "r": 14,
        "enemies": [{"dx": 80, "dy": 0, "r": 16, "shiny": True}],
        "goals": [{"kind": "shiny", "dx": 80, "dy": 0, "dist": 80}],
        "pickups": [{"kind": "coin", "dx": -100, "dy": 0}],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "nudge": "move north",
        "enemies": [{"dx": 80, "dy": 0, "r": 16, "shiny": True}],
        "goals": [{"kind": "shiny", "dx": 80, "dy": 0, "dist": 80}],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
    }) == "move north"
    assert steer({
        "safe": ["move east"], "r": 14, "nudge": "move north",
        "enemies": [], "pickups": [], "heading": "move east",
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "goals": [
            {"kind": "rift", "dx": 40, "dy": 0, "dist": 40},
            {"kind": "thief", "dx": 0, "dy": -120, "dist": 120},
        ],
    }) == "move north"

    held = {"cause": "contact:Pidgey", "time": 10, "item": "luckyegg", "moves": [], "full_bond": False}
    base = default_policy()
    assert knobs_for(held, base) == ["kite", "contact_gap"]
    melee = dict(held, moves=[{"active": True, "kind": "melee", "reach": 48}])
    assert knobs_for(melee, base) == ["kite", "contact_gap", "short"]
    assert knobs_for(dict(held, cause="trap:poison"), base) == ["trap_weight"]
    assert knobs_for(dict(held, cause="hazard:zone"), base) == ["trap_weight"]
    assert knobs_for(dict(held, cause="liquid"), base) == ["trap_weight"]
    assert knobs_for(dict(held, cause="crowd"), base) == ["kite", "contact_gap"]
    assert "shot_weight" not in knobs_for(dict(held, cause="shot"), base)
    early = {"cause": "unseen", "time": 10, "item": "", "moves": [], "full_bond": False}
    assert knobs_for(early, base) == ["egg_until"]
    late_egg = dict(early, time=700, item="luckyegg")
    assert knobs_for(late_egg, base) == ["egg_until", "item_late"]
    assert knobs_for(dict(early, time=700, item="choiceband"), base) == ["item_late"]
    assert knobs_for(dict(early, item="luckyegg", full_bond=True), base) == ["stat_order"]
    assert knobs_for(dict(early, item="luckyegg"), base) == []
    assert cause_of({"liquid": "lava"}) == "liquid"
    assert cause_of({"traps": [{"kind": "poison", "dx": 10, "dy": 0, "r": 74}]}) == "trap:poison"
    assert cause_of({"r": 14, "enemies": [{"name": "Pidgey", "dist": 20, "r": 16}]}) == "contact:Pidgey"
    crowd = {"r": 14, "enemies": [{"name": "Pidgey", "dist": 50, "r": 14}] * 4}
    assert cause_of(crowd) == "crowd"
    assert cause_of({"r": 14, "enemies": [{"name": "Pidgey", "dist": 200, "r": 14}]}) == "unseen"
    grown, edit = apply_step(base, "kite", "increase", {})
    assert edit == {"knob": "kite", "from": 150, "to": 160} and grown["id"] == "p1" and grown["parent"] == "p0"
    low = default_policy()
    low["kite"] = 80
    assert apply_step(low, "kite", "decrease", {}) == (None, None)
    slid, edit = apply_step(base, "stat_order", "later", {"stats": {}})
    assert edit["to"][:2] == ["xp", "rgn"] and slid["stat_order"][0] == "xp"
    assert apply_step(base, "stat_order", "earlier", {"stats": {}}) == (None, None)
    swapped, edit = apply_step(base, "item_late", "later", {})
    assert edit["to"] == ["lifeorb", "choiceband"] and swapped["item_late"][0] == "lifeorb"
    egg_policy = default_policy()
    egg_policy["egg_until"] = 100
    set_policy(egg_policy)
    assert item_choice([{"item": "luckyegg"}, {"item": "choiceband"}], 120) == 1
    atk_first = default_policy()
    atk_first["stat_order"] = ["atk", "rgn", "xp", "hp", "cd", "def", "mag", "spd"]
    set_policy(atk_first)
    assert level_choice([
        {"label": "Potencia", "stat": "atk"},
        {"label": "Aprendizaje", "stat": "xp"},
    ], fused) == 0
    set_policy(default_policy())

    class Pick:
        def predict(self, text, qs):
            assert "contact:Pidgey" in text and "kite distance" in qs["knob"]["criteria"]
            assert "increase it" in qs["way"]["criteria"]
            return {"answers": {
                "knob": {"choice": "kite distance", "probabilities": {}},
                "way": {"choice": "increase it", "probabilities": {}},
            }}

    class Boom:
        def predict(self, text, qs):
            raise RuntimeError("down")

    class Silent:
        def predict(self, text, qs):
            raise AssertionError("no question on an empty knob list")

    class Empty:
        def predict(self, text, qs):
            return {"answers": {}}

    quiet = {"cause": "unseen", "time": 10, "item": "luckyegg", "moves": [], "full_bond": False}
    assert ask_laya(Silent(), quiet, default_policy()) == (None, None)
    assert ask_laya(Empty(), held, default_policy()) == (None, None)
    asked, edit = ask_laya(Pick(), held, default_policy())
    assert edit["to"] == 160 and asked["kite"] == 160

    import tempfile
    snap = {"time": 10, "item": "luckyegg", "r": 14, "enemies": [{"name": "Pidgey", "dist": 20, "r": 16}], "moves": []}
    failed = Path(tempfile.mkdtemp())
    set_policy(default_policy())
    missed = record_death(Boom(), snap, failed)
    assert missed["edit"] is None and missed["policy"] == "p0"
    assert json.loads((failed / "deaths.jsonl").read_text().strip())["edit"] is None
    assert not (failed / "policy.json").exists()
    landed = Path(tempfile.mkdtemp())
    set_policy(default_policy())
    hit = record_death(Pick(), snap, landed)
    assert hit["edit"]["knob"] == "kite" and hit["policy"] == "p1"
    saved = json.loads((landed / "policy.json").read_text())
    assert saved["kite"] == 160 and saved["parent"] == "p0"
    assert json.loads((landed / "deaths.jsonl").read_text().strip())["edit"]["to"] == 160
    set_policy(default_policy())
    print("ok")


if __name__ == "__main__":
    _check()
