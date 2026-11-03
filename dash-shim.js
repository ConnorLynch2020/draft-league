/* Minimal stand-in for the `dash` data API the page was written against: loads data/*.json and
   serves it through dash.data / dash.calc / dash.onData. */
(function () {
  var IDS = ['gamemons', 'draft', 'gamemoves', 'games', 'impact', 'indirect', 'luck', 'luckdetail', 'luckgames', 'luckmoments', 'mons', 'moves',
    'schedule', 'teammoves', 'teams', 'weather'];
  var raw = {}, failed = {}, calcs = {}, subs = [];
  var colors = [0, 1, 2, 3, 4, 5, 6, 7].map(function (n) { return 'var(--dash-' + n + ')'; });

  function cols(rows) { return rows.length ? Object.keys(rows[0]) : []; }

  function evalCalc(id) {
    var c = calcs[id];
    var ins = c.inputs.map(data);
    var bad = ins.find(function (x) { return x.status === 'error'; });
    if (bad) return { status: 'error', data: [], message: bad.message };
    if (ins.some(function (x) { return x.status !== 'ok'; })) return { status: 'loading', data: [] };
    var args = ins.map(function (x) { return x.data; });
    if (c.args && c.args.every(function (a, i) { return a === args[i]; })) return c.result;
    var value = c.fn.apply(null, args);
    c.args = args;
    c.result = { status: 'ok', data: value, columns: cols(value), message: '', refreshing: false, meta: [] };
    return c.result;
  }

  function data(id) {
    if (calcs[id]) return evalCalc(id);
    if (id in raw) return { status: 'ok', data: raw[id], columns: cols(raw[id]), message: '', refreshing: false, meta: [] };
    if (failed[id]) return { status: 'error', data: [], message: failed[id] };
    if (IDS.indexOf(id) < 0) return { status: 'missing', data: [] };
    return { status: 'loading', data: [] };
  }

  function notify() {
    subs.forEach(function (fn) { try { fn(); } catch (e) { console.error(e); } });
  }

  window.dash = {
    data: data,
    calc: function (id, o) { calcs[id] = { inputs: o.inputs, fn: o.fn }; },
    onData: function (fn) { subs.push(fn); fn(); },
    colors: colors,
    setLink: function () {}, refresh: function () {}, params: function () { return {}; },
    setParams: function () {}, resetParams: function () {},
  };

  var stamp = Date.now();
  function load(name) {
    return fetch('data/' + name + '.json?v=' + stamp).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  Promise.all(IDS.map(function (id) {
    return load(id).then(function (d) { raw[id] = d; }).catch(function (e) { failed[id] = String(e); });
  })).then(notify);

  var t;
  addEventListener('resize', function () { clearTimeout(t); t = setTimeout(notify, 120); });

  load('meta').then(function (m) {
    var el = document.getElementById('site-foot');
    if (!el) return;
    var d = new Date(m.updated);
    el.textContent = m.games + ' replays · updated ' + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }).catch(function () {});
})();
