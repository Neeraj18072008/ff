/* the last chapter — booking wizard (progressive enhancement:
   works fully without js, becomes a 3-step wizard with it) */
(function () {
  'use strict';

  var form = document.getElementById('bookForm');
  if (!form) return;
  form.classList.add('js-mode');

  var steps = Array.prototype.slice.call(form.querySelectorAll('.fstep'));
  var inds = Array.prototype.slice.call(document.querySelectorAll('[data-step-ind]'));
  var idx = 0;

  var typeInputs = Array.prototype.slice.call(form.querySelectorAll('input[name="pass_type"]'));
  var payInputs = Array.prototype.slice.call(form.querySelectorAll('input[name="payment"]'));
  var coupleBlock = document.getElementById('coupleBlock');
  var personTitle = form.querySelector('.pb-title');

  function type() {
    var r = typeInputs.filter(function (i) { return i.checked; })[0];
    return r ? r.value : 'single';
  }
  function price() {
    var el = form.querySelector('[data-price="' + type() + '"]');
    return el ? el.textContent.trim() : '';
  }

  function syncCouple() {
    var isCouple = type() === 'couple';
    if (coupleBlock) coupleBlock.classList.toggle('show', isCouple);
    if (personTitle) personTitle.textContent = isCouple ? 'person 01' : 'your details';
  }

  function syncSel() {
    typeInputs.forEach(function (i) {
      var card = i.closest('.pass-card');
      if (card) card.classList.toggle('sel', i.checked);
    });
    payInputs.forEach(function (i) {
      var card = i.closest('.pay-card');
      if (card) card.classList.toggle('sel', i.checked);
    });
  }

  function syncSummary() {
    var t = document.getElementById('sum-type'),
        p = document.getElementById('sum-pay'),
        a = document.getElementById('sum-amt');
    var pr = payInputs.filter(function (i) { return i.checked; })[0];
    if (t) t.textContent = type();
    if (p) p.textContent = pr ? pr.value : 'cash';
    if (a) a.textContent = price();
  }

  function show(i) {
    idx = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach(function (s, j) { s.classList.toggle('on', j === idx); });
    inds.forEach(function (el, j) { el.classList.toggle('on', j <= idx); });
    var top = form.getBoundingClientRect().top + window.scrollY - 76;
    if (Math.abs(window.scrollY - top) > 40) window.scrollTo({ top: top, behavior: 'smooth' });
  }

  function fieldStep(name) {
    if (name === 'pass_type') return 0;
    if (name === 'payment') return 2;
    return 1;
  }

  /* jump straight to the step that owns any server-side error */
  var bad = form.querySelector('.field.bad, .f-err');
  if (bad) {
    var step = bad.closest('.fstep');
    if (step) show(steps.indexOf(step));
  } else {
    show(0);
  }

  /* validation per step */
  function validName(v) { return /^[A-Za-z][A-Za-z .'\-]{1,59}$/.test(v.trim()); }

  function validate(step) {
    var msgs = [];
    if (step === 0) {
      if (!typeInputs.some(function (i) { return i.checked; })) msgs.push('choose a pass type');
    }
    if (step === 1) {
      var p1 = form.p1_name, d1 = form.p1_dob, ct = form.contact;
      if (!validName(p1.value)) { mark(p1, true); msgs.push('enter a valid name'); } else mark(p1, false);
      if (!d1.value) { mark(d1, true); msgs.push('date of birth needed'); } else mark(d1, false);
      if (type() === 'couple') {
        var p2 = form.p2_name, d2 = form.p2_dob;
        if (!validName(p2.value)) { mark(p2, true); msgs.push('person 02 name needed'); } else mark(p2, false);
        if (!d2.value) { mark(d2, true); msgs.push('person 02 dob needed'); } else mark(d2, false);
      }
      if (!/^[6-9]\d{9}$/.test(ct.value.replace(/\D/g, ''))) { mark(ct, true); msgs.push('valid 10-digit mobile needed'); } else mark(ct, false);
    }
    if (msgs.length) {
      alert('check:\n· ' + msgs.join('\n· '));
      return false;
    }
    return true;
  }

  function mark(input, bad) {
    var f = input.closest('.field');
    if (f) f.classList.toggle('bad', bad);
  }

  form.querySelectorAll('[data-next]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (validate(idx)) show(idx + 1);
    });
  });
  form.querySelectorAll('[data-back]').forEach(function (b) {
    b.addEventListener('click', function () { show(idx - 1); });
  });

  typeInputs.forEach(function (i) {
    i.addEventListener('change', function () { syncCouple(); syncSel(); syncSummary(); });
  });
  payInputs.forEach(function (i) {
    i.addEventListener('change', syncSel);
    i.addEventListener('change', syncSummary);
  });

  syncCouple();
  syncSel();
  syncSummary();
})();
