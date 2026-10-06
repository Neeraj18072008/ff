/* the last chapter — control room: verify pass, allow entry, mark payments */
(function () {
  'use strict';

  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  var liveStatus = document.querySelector('.org-status');
  if (liveStatus) {
    function refreshLive() {
      liveStatus.title = 'checking shared database';
      fetch('/organizer/summary', { headers: { 'X-CSRF-Token': csrf } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) { return; }
          var now = new Date();
          var stamp = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' });
          liveStatus.innerHTML = '<span class="live-dot"></span> LIVE · LAST UPDATED: ' + stamp;
        })
        .catch(function () {});
    }
    refreshLive();
    setInterval(refreshLive, 5000);
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
    });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function row(label, value) {
    return '<p class="vr-row"><span>' + label + '</span><span>' + esc(value) + '</span></p>';
  }

  /* ---------------- verify tab ---------------- */
  var vBtn = document.getElementById('vBtn');
  var vInput = document.getElementById('vCode');
  var vResult = document.getElementById('vResult');

  function renderFound(d) {
    var names = d.p1 + (d.p2 ? ' + ' + d.p2 : '');
    var html = '<div class="vr ok">' +
      '<p class="vr-head">✓ PASS FOUND</p>' +
      row('name', names) +
      row('pass', d.type) +
      row('payment', d.method) +
      row('payment status', d.pay_label) +
      row('entry status', d.entry === 'used' ? 'USED' : 'UNUSED') +
      row('contact', d.contact) + '</div>';
    if (d.entry === 'used') {
      html += usedCard(d.code, d.entry_time, d.verified_by);
    } else if (d.pay_status !== 'paid') {
      html += '<div class="vr warn"><p class="vr-head">⚠ PAYMENT PENDING</p>' +
        '<p class="vr-note">collect ' + (d.method === 'cash' ? 'cash' : 'the UPI amount') +
        ' (₹' + esc(d.amount) + ') first, or verify the UPI receipt, then mark it received.</p>' +
        '<div class="vr-actions"><button class="btn" data-mp="' + esc(d.code) + '">mark payment received ✓</button></div></div>';
    } else {
      html += '<div class="vr-actions"><button class="btn" data-entry="' + esc(d.code) + '">ALLOW ENTRY</button></div>';
    }
    vResult.innerHTML = html;
    bindActions();
  }

  function usedCard(code, time, by) {
    return '<div class="vr warn"><p class="vr-head">⚠ PASS ALREADY USED</p>' +
      row('code', code) +
      row('entry recorded', fmtTime(time)) +
      row('by', by || '—') +
      '<p class="vr-note">this pass cannot be used again.</p></div>';
  }

  function fmtTime(iso) {
    try {
      var d = new Date(iso);
      return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' });
    } catch (e) { return iso || '—'; }
  }

  function bindActions() {
    vResult.querySelectorAll('[data-entry]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (b.dataset.armed !== '1') {
          b.dataset.armed = '1';
          b.textContent = 'confirm entry ✓';
          setTimeout(function () { b.dataset.armed = '0'; b.textContent = 'ALLOW ENTRY'; }, 2600);
          return;
        }
        b.disabled = true;
        post('/organizer/entry', { code: b.getAttribute('data-entry') }).then(function (r) {
          if (r.data.status === 'approved') {
            vResult.innerHTML =
              '<div class="vr ok"><p class="vr-head">✓ ENTRY APPROVED</p>' +
              row('code', r.data.code) +
              row('time', fmtTime(r.data.time)) +
              row('organizer', r.data.organizer) + '</div>';
          } else if (r.data.status === 'already_used') {
            vResult.insertAdjacentHTML('beforeend', usedCard(r.data.code, r.data.entry_time, r.data.verified_by));
          }
        });
      });
    });
    vResult.querySelectorAll('[data-mp]').forEach(function (b) {
      b.addEventListener('click', function () {
        b.disabled = true;
        post('/organizer/payment', { code: b.getAttribute('data-mp') }).then(function (r) {
          if (r.ok) { verify(b.getAttribute('data-mp')); }
        });
      });
    });
  }

  function verify(code) {
    post('/organizer/verify', { code: code }).then(function (r) {
      if (r.data.status === 'found') renderFound(r.data);
      else if (r.status === 404) {
        vResult.innerHTML = '<div class="vr err"><p class="vr-head">✕ PASS NOT FOUND</p>' +
          '<p class="vr-note">no booking with this code — check the digits.</p></div>';
      }
    });
  }

  if (vBtn && vInput) {
    vBtn.addEventListener('click', function () { verify(vInput.value); });
    vInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') verify(vInput.value); });
    vInput.addEventListener('input', function () {
      vInput.value = vInput.value.replace(/\D/g, '').slice(0, 4);
    });
  }

  /* ---------------- bookings tab: quick mark-paid ---------------- */
  document.querySelectorAll('.mark-paid').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.dataset.armed !== '1') {
        b.dataset.armed = '1';
        var old = b.textContent;
        b.dataset.old = old;
        b.textContent = 'confirm ✓';
        setTimeout(function () { if (b.dataset.armed === '1') { b.dataset.armed = '0'; b.textContent = old; } }, 2600);
        return;
      }
      b.disabled = true;
      post('/organizer/payment', { code: b.getAttribute('data-code') }).then(function () {
        b.textContent = 'received ✓';
        setTimeout(function () { window.location.reload(); }, 600);
      });
    });
  });
})();
