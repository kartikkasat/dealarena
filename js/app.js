/* DealArena interface (Vue 3, no build step). All computation runs on the server: see backend/. */
(function () {
  'use strict';
  var fmt0 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });

  // ---------------------------------------------------------------- API client
  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var d = data && data.detail;
          var msgs = Array.isArray(d) ? d.map(function (x) { return typeof x === 'string' ? x : (x.msg || JSON.stringify(x)); }) : [d || ('Request failed (' + res.status + ')')];
          var err = new Error(msgs.join('; ')); err.messages = msgs; err.status = res.status; throw err;
        }
        return data;
      });
    });
  }

  function newCompetitor(type, n) {
    var id = 'c' + Date.now().toString(36) + n;
    if (type === 'pe') return { id: id, name: 'PE Sponsor ' + n, type: 'pe', enabled: true, targetIrr: 20, holdYears: 5, ebitdaGrowth: 5,
      ebitdaUplift: 5, exitMultiple: 10, leverage: 5, debtPaydown: 30, maxEquity: 500, signalNoise: 10, shading: 96, feverProb: 0, feverPremium: 0, observedBid: null };
    return { id: id, name: 'Strategic ' + n, type: 'strategic', enabled: true, synergyPct: 8, signalNoise: 10, financingLimit: 1300,
      shading: 94, feverProb: 10, feverPremium: 8, observedBid: null };
  }

  // ---------------------------------------------------------------- charts
  Chart.defaults.font.family = 'Plex, system-ui, sans-serif';
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = '#4A5568';
  Chart.defaults.animation = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 200 };
  Chart.register({
    id: 'markers',
    afterDatasetsDraw: function (chart, args, opts) {
      if (!opts || !opts.lines) return;
      var x = chart.scales.x, a = chart.chartArea, ctx = chart.ctx;
      ctx.save();
      opts.lines.forEach(function (l) {
        if (l.x == null) return;
        var px = x.getPixelForValue(l.x);
        if (px < a.left || px > a.right) return;
        ctx.strokeStyle = l.color; ctx.lineWidth = 2; ctx.setLineDash(l.dash || []);
        ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
      });
      ctx.restore();
    }
  });

  // ---------------------------------------------------------------- number field
  var NumField = {
    template: '#num-field',
    props: { label: String, unit: String, hint: String, modelValue: [Number, String, null], min: Number, max: Number,
      step: [Number, String], allowEmpty: Boolean, placeholder: String },
    emits: ['update:modelValue'],
    methods: {
      emitVal: function (e) {
        var raw = e.target.value;
        if (raw === '' && this.allowEmpty) { this.$emit('update:modelValue', null); return; }
        var v = parseFloat(raw);
        if (isNaN(v)) { e.target.value = this.modelValue == null ? '' : this.modelValue; return; }
        this.$emit('update:modelValue', v);
      }
    }
  };

  var app = Vue.createApp({
    template: '#app-tpl',
    data: function () {
      return {
        sc: null, defaults: null, result: null, meta: null, runs: [], stress: null,
        online: true, running: false, errors: [], toast: '', justRan: false,
        approver: '', decisionNote: '',
        autoRun: true, tab: 'Rivals', tabs: ['Rivals', 'Strategies', 'Risk', 'Run log'], openComp: null,
        ladderWidth: 860, rulerWidth: 860,
        weightDefs: [
          { key: 'financial', label: 'Financial value', hint: 'Weight on expected value created for shareholders' },
          { key: 'strategic', label: 'Strategic value', hint: 'Weight on your stated strategic value, counted only if we win' },
          { key: 'emotional', label: 'Emotional value', hint: 'Weight on your stated emotional value, counted only if we win' },
          { key: 'risk', label: 'Risk aversion', hint: 'Penalty on the average loss in the worst 5% of wins' }
        ]
      };
    },
    computed: {
      rec: function () { return this.result ? this.result.recommendation : null; },
      th: function () { return this.result ? this.result.thresholds : null; },
      plan: function () { return this.result ? this.result.bidPlan : null; },
      ad: function () { return this.result ? this.result.adaptation : null; },
      currentDecision: function () {
        var m = this.meta; if (!m) return null;
        var row = this.runs.filter(function (r) { return r.runId === m.runId; })[0];
        return row ? row.decision : null;
      },
      adaptiveOn: function () { return !!(this.ad && this.ad.enabled); },
      activeCompetitors: function () { return this.sc ? this.sc.competitors.filter(function (c) { return c.enabled; }).length : 0; },
      adStatus: function () {
        var a = this.ad; if (!a) return '';
        if (a.status === 'converged') return 'Settled after ' + a.rounds + ' rounds; no rival gains by changing its bid alone.';
        if (a.status === 'near') return 'Settled within one EUR ' + fmt0.format(a.bidStep) + 'm bid step after ' + a.rounds + ' rounds; no rival gains more than EUR ' + Math.max(0.1, a.maxRivalGain).toFixed(1) + 'm by deviating (exploitability).';
        return 'Did not settle after ' + a.rounds + ' rounds: treat as unreliable.';
      },
      ladder: function () {
        var th = this.th; if (!th) return null;
        var w = Math.max(300, this.ladderWidth), compact = w < 640;
        var labelW = compact ? 0 : 250, left = labelW + (compact ? 2 : 14), right = w - 56;
        var barH = 16, rowH = compact ? 42 : 30, top = 26;
        var rows = th.rows.slice().sort(function (a, b) { return (b.active - a.active) || (a.max - b.max); });
        rows.push({ key: 'naive', label: 'Simple break-even (ignores winner\'s curse)', max: th.naiveBreakEven, active: false, ref: true });
        var prop = this.result.proposed ? this.result.proposed.bid : null;
        var vals = [th.ceiling, th.naiveBreakEven, this.sc.deal.standaloneValue];
        if (this.plan) vals.push(this.plan.bid);
        if (prop) vals.push(prop);
        th.rows.forEach(function (r) { if (!r.above) vals.push(r.max); });
        var lo = Math.floor(Math.min.apply(null, vals) * 0.88 / 50) * 50, hi = Math.ceil(Math.max.apply(null, vals) * 1.04 / 50) * 50;
        var sx = function (v) { return left + (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (right - left); };
        var stepT = (hi - lo) > 700 ? 200 : ((hi - lo) > 350 ? 100 : 50); if (compact && stepT < 100) stepT = 100; while ((hi - lo) / stepT > 7) stepT *= 2;
        var ticks = []; for (var t = Math.ceil(lo / stepT) * stepT; t <= hi; t += stepT) ticks.push({ v: t, x: sx(t), label: fmt0.format(t) });
        var out = rows.map(function (r, i) {
          var y = top + i * rowH + (compact ? 14 : 0);
          return { key: r.key, label: r.label, y: y, labelY: compact ? y - 4 : y + barH / 2 + 4, x: r.above ? right : sx(r.max),
            valueText: r.above ? 'not binding' : fmt0.format(r.max), binding: !!r.binding, active: r.active !== false, ref: !!r.ref };
        });
        var bottom = top + rows.length * rowH + (compact ? 8 : 0);
        return { w: w, h: bottom + (prop ? 62 : 46), top: top, bottom: bottom, left: left, right: right, labelX: compact ? left : 0, barH: barH,
          ticks: ticks, rows: out, xCeil: sx(th.ceiling), xRec: this.plan ? sx(this.plan.bid) : null, xProp: prop ? sx(prop) : null,
          anchor: function (x) { return x > right - 90 ? 'end' : (x < left + 90 ? 'start' : 'middle'); } };
      },
      ruler: function () {
        var r = this.result; if (!r) return null;
        var w = Math.max(300, this.rulerWidth), compact = w < 640;
        var labelW = compact ? 0 : 230, left = labelW + (compact ? 2 : 14), right = w - 12, rowH = compact ? 42 : 30, top = 14;
        var cs = r.competitors;
        var lo = Math.min(r.curve[0].bid, Math.min.apply(null, cs.map(function (c) { return c.p025; })));
        var hi = Math.max(this.th.ceiling * 1.05, Math.max.apply(null, cs.map(function (c) { return c.p975; })));
        lo = Math.floor(lo / 50) * 50; hi = Math.ceil(hi / 50) * 50;
        var sx = function (v) { return left + (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (right - left); };
        var stepT = (hi - lo) > 700 ? 200 : 100; while ((hi - lo) / stepT > 7) stepT *= 2;
        var ticks = []; for (var t = Math.ceil(lo / stepT) * stepT; t <= hi; t += stepT) ticks.push({ v: t, x: sx(t), label: fmt0.format(t) });
        var rows = cs.map(function (c, i) {
          var y = top + i * rowH + rowH / 2 + (compact ? 8 : 0);
          return { id: c.id, name: c.name, y: y, labelY: compact ? y - 11 : y + 4, x5: sx(c.p5), x95: sx(c.p95), x50: sx(c.p50), x025: sx(c.p025), x975: sx(c.p975),
            xObs: c.observedPctl === null ? null : sx(c.observedBid), obsOut: c.observedPctl !== null && (c.observedPctl < 0.025 || c.observedPctl > 0.975) };
        });
        var bottom = top + cs.length * rowH + (compact ? 10 : 0);
        return { w: w, h: bottom + 24, top: top, bottom: bottom, labelX: compact ? left : 0, ticks: ticks, rows: rows,
          xRec: this.plan ? sx(this.plan.bid) : null, xCeil: sx(this.th.ceiling) };
      }
    },
    watch: {
      sc: { deep: true, handler: function () { if (this.autoRun && this._ready) this.scheduleRun(); } },
      result: function () { var self = this; this.$nextTick(function () { self.measure(); self.drawCharts(); }); },
      tab: function () { var self = this; this.$nextTick(function () { self.measure(); self.drawCharts(); }); }
    },
    mounted: function () {
      var self = this;
      window.addEventListener('resize', function () { self.measure(); });
      this.checkHealth().then(function () { return api('GET', '/api/scenario/default'); })
        .then(function (s) { self.defaults = JSON.parse(JSON.stringify(s)); self.sc = s; self.$nextTick(function () { self._ready = true; self.runNow(false); }); })
        .catch(function () { self.online = false; });
      this._hb = setInterval(function () { self.checkHealth().catch(function () {}); }, 15000);
    },
    methods: {
      eur: function (v) { return v == null || isNaN(v) ? '' : 'EUR ' + fmt0.format(v) + 'm'; },
      eurSigned: function (v) { if (v == null || isNaN(v)) return ''; var r = Math.round(v); return (r > 0 ? '+' : (r < 0 ? '−' : '')) + 'EUR ' + fmt0.format(Math.abs(r)) + 'm'; },
      pct: function (v) { return v == null ? '' : fmt0.format(v * 100) + '%'; },
      fmtInt: function (v) { return fmt0.format(v); },
      lower: function (s) { return s ? s.charAt(0).toLowerCase() + s.slice(1) : ''; },
      timeOf: function (iso) { try { return new Date(iso).toLocaleTimeString('en-GB'); } catch (e) { return iso; } },
      compWarn: function (id) {
        if (!this.result) return false;
        var c = this.result.competitors.find(function (x) { return x.id === id; });
        return !!c && (c.ood.length > 0 || (c.observedPctl !== null && (c.observedPctl < 0.025 || c.observedPctl > 0.975)));
      },
      flash: function (msg, ms) { var self = this; this.toast = msg; clearTimeout(this._t); this._t = setTimeout(function () { self.toast = ''; }, ms || 3500); },
      checkHealth: function () {
        var self = this;
        return api('GET', '/api/health').then(function () { self.online = true; }).catch(function (e) { self.online = false; throw e; });
      },
      measure: function () {
        var lw = this.$refs.ladderWrap, rw = this.$refs.rulerWrap;
        if (lw && lw.clientWidth) this.ladderWidth = lw.clientWidth;
        if (rw && rw.clientWidth) this.rulerWidth = rw.clientWidth;
      },
      scheduleRun: function () { var self = this; clearTimeout(this._deb); this._deb = setTimeout(function () { self.runNow(false); }, 600); },
      accept: function (res, manual, label) {
        var before = this.rec ? Math.round(this.rec.bid) : null;
        this.result = res.result; this.meta = res.run; this.errors = []; this.stress = null;
        var self = this; this.justRan = true; clearTimeout(this._jr); this._jr = setTimeout(function () { self.justRan = false; }, 900);
        this.loadRuns();
        if (manual) {
          var after = this.rec ? Math.round(this.rec.bid) : null;
          var change = before === after ? 'system bid unchanged at ' + (after ? this.eur(after) : 'walk away') : 'system bid ' + (after ? this.eur(after) : 'walk away') + (before ? ', was ' + this.eur(before) : '');
          this.flash((label || 'Run') + ' ' + res.run.runId.slice(-6) + ' complete (seed ' + res.result.seed + '): ' + change + '.');
        }
      },
      runNow: function (manual) {
        var self = this;
        if (!this.sc) return;
        if (this.running) { this._queued = true; return; }
        this.running = true;
        var t0 = Date.now();
        api('POST', '/api/simulate', this.sc).then(function (res) {
          var wait = Math.max(0, (manual ? 300 : 0) - (Date.now() - t0));
          setTimeout(function () { self.running = false; self.online = true; self.accept(res, manual); self.next(); }, wait);
        }).catch(function (e) {
          self.running = false;
          if (e.status) { self.errors = e.messages || [e.message]; } else { self.online = false; }
          if (manual) self.flash(e.status ? 'Run not completed: see the message above.' : 'The compute engine is not reachable.');
          self.next();
        });
      },
      next: function () { if (this._queued) { this._queued = false; this.runNow(false); } },
      runStress: function () {
        var self = this; if (this.running) return;
        this.running = true;
        api('POST', '/api/simulate/stress', this.sc).then(function (res) {
          self.running = false;
          var r = res.result.recommendation;
          self.stress = { bid: r ? r.bid : null, pWin: r ? r.pWin : 0 };
          self.loadRuns();
          self.flash('Stress run ' + res.run.runId.slice(-6) + ' logged.');
        }).catch(function (e) { self.running = false; self.flash(e.message); });
      },
      replay: function (id) {
        var self = this; if (this.running) return;
        this.running = true;
        api('POST', '/api/runs/' + encodeURIComponent(id) + '/replay').then(function (res) {
          self.running = false;
          self.accept(res, false);
          self.flash('Replay of run ' + id.slice(-6) + ' with seed ' + res.result.seed + ': ' + (res.identical ? 'identical result.' : 'result differs.'), 4500);
        }).catch(function (e) { self.running = false; self.flash(e.message); });
      },
      decide: function (decision) {
        var self = this, id = this.meta && this.meta.runId; if (!id) return;
        api('POST', '/api/runs/' + encodeURIComponent(id) + '/decision', { approver: this.approver, decision: decision, note: this.decisionNote })
          .then(function (d) { self.decisionNote = ''; self.loadRuns(); self.flash('Run ' + id.slice(-6) + ' ' + d.decision + ' by ' + d.approver + '; logged.'); })
          .catch(function (e) { self.flash((e.messages && e.messages.join(' ')) || e.message, 5000); });
      },
      loadRuns: function () {
        var self = this;
        api('GET', '/api/runs?limit=50').then(function (r) { self.runs = r; }).catch(function () {});
      },
      resetExample: function () {
        if (!this.defaults) return;
        this.sc = JSON.parse(JSON.stringify(this.defaults)); this.openComp = null; this.stress = null;
        this.flash('Sample case restored');
        if (!this.autoRun) this.runNow(true);
      },
      addCompetitor: function (type) {
        var c = newCompetitor(type, this.sc.competitors.length + 1);
        this.sc.competitors.push(c); this.openComp = c.id;
      },
      // ---------------------------------------------------------- charts (Risk tab only)
      drawCharts: function () {
        if (this.tab !== 'Risk' || !this.result) return;
        var r = this.result, small = window.innerWidth < 640;
        var lines = [{ x: this.th.ceiling, color: '#B42318' }];
        if (this.plan) lines.push({ x: this.plan.bid, color: '#2459C4' });
        if (r.proposed) lines.push({ x: r.proposed.bid, color: '#A86A12', dash: [5, 3] });
        this.chart('curveChart', {
          type: 'line',
          data: { datasets: [
            { label: 'Win chance (%)', yAxisID: 'y2', data: r.curve.map(function (c) { return { x: c.bid, y: c.pWin * 100 }; }), borderColor: '#AEB9CA', borderWidth: 2, pointRadius: 0, tension: .2 },
            { label: 'Expected value (EURm)', yAxisID: 'y', data: r.curve.map(function (c) { return { x: c.bid, y: c.expFv }; }), borderColor: '#2F6B4F', borderWidth: 2, pointRadius: 0, tension: .2 }
          ] },
          options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            scales: { x: { type: 'linear', grid: { color: '#F0F2F5' }, ticks: { maxTicksLimit: small ? 5 : 8 } },
                      y: { grid: { color: '#F0F2F5' }, title: { display: !small, text: 'EURm' } },
                      y2: { position: 'right', min: 0, max: 100, grid: { display: false } } },
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, boxHeight: 2 } }, markers: { lines: lines } } }
        });
        var hr = r.histRec || [], hp = r.histProposed || [];
        var base = hr.length ? hr : hp;
        var ds = [];
        if (hr.length) ds.push({ label: 'System bid', data: hr.map(function (b) { return b.share * 100; }), backgroundColor: hr.map(function (b) { return b.to <= 0 ? 'rgba(36,89,196,.4)' : '#2459C4'; }) });
        if (hp.length) ds.push({ label: 'Proposal', data: hp.map(function (b) { return b.share * 100; }), backgroundColor: hp.map(function (b) { return b.to <= 0 ? 'rgba(168,106,18,.4)' : '#D9A75A'; }) });
        this.chart('histChart', {
          type: 'bar', data: { labels: base.map(function (b) { return fmt0.format(b.from); }), datasets: ds },
          options: { maintainAspectRatio: false, barPercentage: 1, categoryPercentage: .85,
            scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: small ? 6 : 12 } }, y: { grid: { color: '#F0F2F5' }, title: { display: !small, text: '% of wins' } } },
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } } }
        });
      },
      chart: function (ref, cfg) {
        this._charts = this._charts || {};
        var el = this.$refs[ref]; if (!el) return;
        var ex = this._charts[ref];
        if (ex && ex.canvas === el) { ex.data = cfg.data; ex.options = cfg.options; ex.update(); ex.resize(); }
        else { if (ex) ex.destroy(); this._charts[ref] = new Chart(el, cfg); }
      }
    }
  });
  app.component('num-field', NumField);
  app.mount('#app');
})();
