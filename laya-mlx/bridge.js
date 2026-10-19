/* Drives a local solo run. Injected only by laya-mlx/play.py into dev.html. */
(function () {
  var STEP = 40;
  var HAZARD_SOON = 0.7;
  var DIRS = [
    ['stand still', 0, 0],
    ['move north', 0, -1],
    ['move north-east', 1, -1],
    ['move east', 1, 0],
    ['move south-east', 1, 1],
    ['move south', 0, 1],
    ['move south-west', -1, 1],
    ['move west', -1, 0],
    ['move north-west', -1, -1]
  ];
  var MOVE_KEYS = {
    'stand still': [],
    'move north': ['KeyW'],
    'move south': ['KeyS'],
    'move west': ['KeyA'],
    'move east': ['KeyD'],
    'move north-east': ['KeyW', 'KeyD'],
    'move south-east': ['KeyS', 'KeyD'],
    'move south-west': ['KeyS', 'KeyA'],
    'move north-west': ['KeyW', 'KeyA']
  };

  var held = {};
  var busy = false;
  var loop = false;
  var cardSent = '';
  var overHandled = false;
  var mark = 0;

  function key(type, code) {
    window.dispatchEvent(new KeyboardEvent(type, {
      code: code, key: code, bubbles: true, cancelable: true
    }));
  }

  function tap(code) {
    key('keydown', code);
    key('keyup', code);
  }

  function releaseMove() {
    Object.keys(held).forEach(function (code) { key('keyup', code); });
    held = {};
  }

  function holdMove(label) {
    var want = MOVE_KEYS[label] || [];
    Object.keys(held).forEach(function (code) {
      if (want.indexOf(code) < 0) { key('keyup', code); delete held[code]; }
    });
    want.forEach(function (code) {
      if (!held[code]) { key('keydown', code); held[code] = true; }
    });
  }

  function open(id) {
    return G.UI && G.UI.isOpen(id);
  }

  function phase() {
    if (!G.Game) return 'other';
    if (G.Judgment && G.Judgment.cinematic && G.Game.state === 'playing') return 'cinematic';
    if (G.Game.state === 'over' || open('scr-over')) return 'over';
    if (G.Game.state === 'levelup' || open('scr-levelup')) return 'levelup';
    if (open('scr-item')) return 'item';
    if (open('scr-bond')) return 'bond';
    if (G.Game.state === 'playing') return 'playing';
    return 'other';
  }

  function cardBox(ph) {
    return document.getElementById(ph === 'levelup' ? 'cards' : ph === 'item' ? 'item-cards' : 'bond-cards');
  }

  function cardsOf(ph) {
    var box = cardBox(ph);
    if (!box) return [];
    var used = {};
    return [].map.call(box.querySelectorAll('.card'), function (el) {
      var nm = el.querySelector('.nm');
      var chip = nm && nm.querySelector('.tag-chip');
      var title = '';
      if (nm) {
        [].forEach.call(nm.childNodes, function (n) {
          if (n !== chip && n.nodeType === 3) title += n.textContent;
        });
        title = title.replace(/\s+/g, ' ').trim() || nm.textContent.replace(/\s+/g, ' ').trim();
      }
      var tag = chip ? chip.textContent.replace(/\s+/g, ' ').trim() : '';
      var ds = ((el.querySelector('.ds') || {}).textContent || '').replace(/\s+/g, ' ').trim();
      var label = title || 'reward';
      if (used[label]) label = (title + ' ' + (tag || Object.keys(used).length)).trim();
      used[label] = true;
      var raw = title.split('→')[0].split('->')[0].trim();
      var def = (G.Moves && G.Moves.ALL || []).find(function (m) {
        return m.name === raw || (G.T && G.T(m.name) === raw);
      });
      return {
        label: label,
        detail: [tag, ds].filter(Boolean).join('. ').slice(0, 180),
        move: def ? def.id : '',
        type: def ? def.type : '',
        kind: def ? def.kind : '',
        upgrade: /^(Nv\.|Lv\.)/.test(tag) || title.indexOf('→') >= 0 || title.indexOf('->') >= 0,
        fresh: tag.indexOf('Nuevo') === 0 || tag.indexOf('New') === 0
      };
    });
  }

  function knownMoves(pl) {
    var out = [];
    (pl.slots || []).forEach(function (g, si) {
      g.forEach(function (m, mi) {
        out.push({
          name: m.name, id: m.id, kind: m.kind, type: m.type, lvl: m.lvl,
          slot: si, lead: mi === 0, active: si === pl.active, group: g.length
        });
      });
    });
    return out;
  }

  function bondInfo(pl) {
    if (!open('scr-bond') || !pl) return null;
    var sub = (document.getElementById('bond-sub') || {}).textContent || '';
    var grow = null;
    pl.slots.forEach(function (g) { if (g.length > 1 && g.length < 4) grow = g; });
    var back = document.getElementById('bond-back');
    var step = back && !back.classList.contains('hidden') ? 2 : 1;
    var first = '', bestLen = 0;
    if (step === 2) {
      pl.moves.forEach(function (m) {
        [m.name, G.T ? G.T(m.name) : ''].forEach(function (n) {
          if (n && sub.indexOf(n) >= 0 && n.length > bestLen) { bestLen = n.length; first = m.id; }
        });
      });
    }
    return {
      mode: grow ? 'add' : 'pair',
      step: step,
      group: grow ? grow.map(function (m) { return m.id; }) : [],
      first: first
    };
  }

  /** Keep the slot with the highest-level attack selected. */
  function useBestAttack() {
    var pl = G.Game.player;
    if (!pl || !pl.slots || !pl.slots.length) return;
    var bestSlot = pl.active, bestLvl = -1;
    (pl.slots[pl.active] || []).forEach(function (m) {
      if (m.kind !== 'buff' && m.lvl > bestLvl) bestLvl = m.lvl;
    });
    pl.slots.forEach(function (g, i) {
      g.forEach(function (m) {
        if (m.kind !== 'buff' && m.lvl > bestLvl) { bestLvl = m.lvl; bestSlot = i; }
      });
    });
    if (bestSlot !== pl.active) tap('Digit' + (bestSlot + 1));
  }

  function heading() {
    var w = !!held.KeyW, a = !!held.KeyA, s = !!held.KeyS, d = !!held.KeyD;
    if (w && d && !a && !s) return 'move north-east';
    if (s && d && !w && !a) return 'move south-east';
    if (s && a && !w && !d) return 'move south-west';
    if (w && a && !s && !d) return 'move north-west';
    if (w && !a && !s && !d) return 'move north';
    if (d && !w && !a && !s) return 'move east';
    if (s && !w && !a && !d) return 'move south';
    if (a && !w && !s && !d) return 'move west';
    return '';
  }

  function safeDirs(pl) {
    var soon = (G.Hazards.all || []).filter(function (h) {
      return !h.hit && (h.delay - h.t) < HAZARD_SOON;
    });
    var out = [];
    DIRS.forEach(function (d) {
      var len = Math.hypot(d[1], d[2]) || 1;
      var x = pl.x + (d[0] === 'stand still' ? 0 : d[1] / len * STEP);
      var y = pl.y + (d[0] === 'stand still' ? 0 : d[2] / len * STEP);
      // isFree also rejects water. The shield will not step into a pond.
      if (d[0] !== 'stand still' && !G.World.isFree(x, y, pl.r)) return;
      if (soon.some(function (h) { return G.Hazards.inside(h, x, y, pl.r); })) return;
      out.push(d[0]);
    });
    return out;
  }

  /** Walls and indestructible props. Shots die on them, so the bot stays clear. */
  function blocksNear(pl) {
    var TS = G.World.TS || 32;
    var reach = 120;
    var seen = {};
    var out = [];
    var x0 = pl.x - reach, x1 = pl.x + reach, y0 = pl.y - reach, y1 = pl.y + reach;
    for (var y = y0; y <= y1; y += TS) {
      for (var x = x0; x <= x1; x += TS) {
        var block = G.World.projectileBlock(x, y, 4);
        if (!block) continue;
        if (block === 'wall') {
          var tx = Math.floor(x / TS), ty = Math.floor(y / TS);
          var key = 'w' + tx + ',' + ty;
          if (seen[key]) continue;
          seen[key] = true;
          var cx = tx * TS + TS / 2, cy = ty * TS + TS / 2;
          var dx = cx - pl.x, dy = cy - pl.y;
          out.push({ dx: Math.round(dx), dy: Math.round(dy), r: 16, dist: Math.round(Math.hypot(dx, dy)) });
        } else if (block.solid && !block.destructible) {
          var pkey = 'p' + block.tx + ',' + block.ty;
          if (seen[pkey]) continue;
          seen[pkey] = true;
          var pdx = block.x - pl.x, pdy = block.y - pl.y;
          out.push({ dx: Math.round(pdx), dy: Math.round(pdy), r: Math.round(block.r || 13), dist: Math.round(Math.hypot(pdx, pdy)) });
        }
      }
    }
    out.sort(function (a, b) { return a.dist - b.dist; });
    return out.slice(0, 20);
  }

  function snapshot(ph) {
    var pl = G.Game.player;
    var foes = [];
    if (pl && G.EnemyMgr) {
      foes = G.EnemyMgr.all().filter(function (e) { return e && !e.dead; }).map(function (e) {
        var dx = e.x - pl.x, dy = e.y - pl.y;
        return { name: e.name, boss: !!e.boss, r: Math.round(e.r || 14), dist: Math.round(Math.hypot(dx, dy)), dx: Math.round(dx), dy: Math.round(dy) };
      }).filter(function (e) { return e.dist < 260; }).sort(function (a, b) { return a.dist - b.dist; }).slice(0, 12);
    }
    var pickups = [];
    if (pl && G.Pickups) {
      pickups = G.Pickups.all().filter(function (p) {
        return p && (p.kind === 'xp' || p.kind === 'xpBig' || p.kind === 'coin');
      }).map(function (p) {
        var dx = p.x - pl.x, dy = p.y - pl.y;
        return { kind: p.kind, dist: Math.round(Math.hypot(dx, dy)), dx: Math.round(dx), dy: Math.round(dy) };
      }).filter(function (p) { return p.dist < 320; }).sort(function (a, b) { return a.dist - b.dist; }).slice(0, 16);
    }
    var hazards = [];
    if (pl && G.Hazards) {
      hazards = (G.Hazards.all || []).filter(function (h) { return !h.hit; }).map(function (h) {
        var dx = (h.x || pl.x) - pl.x, dy = (h.y || pl.y) - pl.y;
        return { kind: h.kind, dist: Math.round(Math.hypot(dx, dy)), dx: Math.round(dx), dy: Math.round(dy), delay: Math.round((h.delay - h.t) * 10) / 10 };
      }).filter(function (h) { return h.dist < 220 || h.delay < HAZARD_SOON; }).slice(0, 4);
    }
    var liquid = null;
    if (pl && pl.inLiquid && G.World.liquidEffect(pl.mon.types).dmg) liquid = G.World.liquidKind();
    return {
      phase: ph,
      hp: pl ? Math.round(pl.hp) : 0,
      maxHp: pl ? Math.round(pl.maxHp) : 1,
      x: pl ? Math.round(pl.x) : 0,
      y: pl ? Math.round(pl.y) : 0,
      r: pl ? pl.r : 14,
      heading: heading(),
      level: pl ? pl.level : 1,
      mon: pl && pl.mon ? pl.mon.name : '',
      time: G.Game.st ? G.Game.st.time : 0,
      liquid: liquid,
      enemies: foes,
      pickups: pickups,
      blocks: pl ? blocksNear(pl) : [],
      hazards: hazards,
      safe: pl && ph === 'playing' ? safeDirs(pl) : [],
      cards: ph === 'playing' || ph === 'cinematic' || ph === 'over' ? [] : cardsOf(ph),
      moves: pl ? knownMoves(pl) : [],
      bond: ph === 'bond' ? bondInfo(pl) : null
    };
  }

  function apply(cmd, snap) {
    if (!cmd || cmd.action === 'wait') return;
    if (cmd.action === 'card') {
      var now = phase();
      if (now !== 'levelup' && now !== 'item' && now !== 'bond') return;
      var sig = (snap.cards || []).map(function (c) { return c.label; }).join('|');
      if (cardSent === sig) return;
      cardSent = sig;
      releaseMove();
      tap('Digit' + ((cmd.index || 0) + 1));
      setTimeout(function () {
        if (cardSent === sig && phase() !== 'playing') cardSent = '';
      }, 800);
      return;
    }
    if (cmd.action === 'move' && phase() === 'playing') holdMove(cmd.label);
  }

  function tick() {
    var ph = phase();
    if (ph === 'playing') { cardSent = ''; useBestAttack(); }
    if (ph === 'cinematic') { tap('Space'); return; }
    if (ph === 'over') {
      if (overHandled) return;
      overHandled = true;
      releaseMove();
      var btn = document.getElementById('btn-again');
      if (btn) btn.click();
      try { G.Flow.play(); } catch (e) { console.error('[bot]', e); }
      return;
    }
    overHandled = false;
    if (ph !== 'playing' && ph !== 'levelup' && ph !== 'item' && ph !== 'bond') return;
    var box = cardBox(ph);
    if (ph !== 'playing' && box && box.classList.contains('guard')) { releaseMove(); return; }
    var pl = G.Game.player;
    if (ph === 'playing' && pl && Date.now() - mark > 2000) {
      mark = Date.now();
      console.log('[bot] at', Math.round(pl.x), Math.round(pl.y), 'hp', Math.round(pl.hp));
    }
    if (busy) return;
    var snap = snapshot(ph);
    busy = true;
    fetch('/decide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(snap)
    }).then(function (r) {
      if (!r.ok) throw new Error('decide ' + r.status);
      return r.json();
    }).then(function (cmd) {
      apply(cmd, snap);
    }).catch(function (e) {
      console.warn('[bot]', e);
    }).then(function () { busy = false; });
  }

  function resolveMon(q) {
    q = (q || '').trim().toLowerCase();
    if (!q || !G.DEX) return null;
    if (/^\d+$/.test(q)) return G.DEX_BY[+q] || null;
    var exact = G.DEX.filter(function (m) { return m.name.toLowerCase() === q; });
    if (exact.length === 1) return exact[0];
    var starts = G.DEX.filter(function (m) { return m.name.toLowerCase().indexOf(q) === 0; });
    return starts.length === 1 ? starts[0] : null;
  }

  function begin(mon) {
    G.DB.playAsGuest();
    var save = G.DB.save;
    save.starter = mon.dex;
    save.partner = mon.dex;
    G.DB.grant(mon.dex, 'starter');
    G.Flow.goMenu();
    G.Flow.play();
    console.log('[bot] run', mon.name, G.Game.state);
  }

  function ask(then) {
    var preset = resolveMon(new URLSearchParams(location.search).get('mon'));
    if (preset) { then(preset); return; }
    var scr = document.createElement('section');
    scr.className = 'scr';
    scr.innerHTML =
      '<div class="box" style="width:min(420px,100%)">' +
      '<h2>Which Pokémon?</h2>' +
      '<p class="sub">Type a name or a Pokédex number</p>' +
      '<input id="bot-mon" class="field" placeholder="Pikachu or 25" autocomplete="off" spellcheck="false">' +
      '<p id="bot-mon-err" class="err"></p>' +
      '<button id="bot-mon-ok" class="btn gold" type="button">Play</button></div>';
    document.body.appendChild(scr);
    var input = document.getElementById('bot-mon');
    var err = document.getElementById('bot-mon-err');
    function go() {
      var mon = resolveMon(input.value);
      if (!mon) { err.textContent = 'No single Pokémon matches that. Try the full name or its number.'; return; }
      scr.remove();
      then(mon);
    }
    document.getElementById('bot-mon-ok').onclick = go;
    input.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      e.stopPropagation();
      go();
    });
    input.focus();
  }

  function arm() {
    if (!window.G || !G.DB || !G.Flow || !G.Game || !G.UI || !G.DEX) { setTimeout(arm, 50); return; }
    var boot = document.getElementById('boot');
    if (boot && !boot.classList.contains('hidden')) { setTimeout(arm, 50); return; }
    var tapEl = document.getElementById('tap-start');
    if (tapEl && !tapEl.classList.contains('hidden')) { tapEl.click(); setTimeout(arm, 50); return; }
    ask(function (mon) {
      if (G.Game.state === 'ui') {
        try { begin(mon); }
        catch (e) { console.error('[bot] start', e); return; }
      }
      if (!loop) { loop = true; setInterval(tick, 200); }
    });
  }

  arm();
})();
