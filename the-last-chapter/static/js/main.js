/* the last chapter — shared ui (topbar, menu, reveal, countdown) */
(function () {
  'use strict';

  /* topbar background on scroll */
  var bar = document.getElementById('topbar');
  function onScroll() {
    if (bar) bar.classList.toggle('scrolled', window.scrollY > 24);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ⋮ menu */
  var menuBtn = document.getElementById('menuBtn');
  var menuDrop = document.getElementById('menuDrop');
  if (menuBtn && menuDrop) {
    menuBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = !menuDrop.hidden;
      menuDrop.hidden = open;
      menuBtn.setAttribute('aria-expanded', String(!open));
    });
    document.addEventListener('click', function (e) {
      if (!menuDrop.hidden && !menuDrop.contains(e.target) && e.target !== menuBtn) {
        menuDrop.hidden = true;
        menuBtn.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !menuDrop.hidden) {
        menuDrop.hidden = true;
        menuBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* smooth scroll for in-page anchors */
  document.querySelectorAll('[data-scroll]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var id = a.getAttribute('href');
      var el = id && id.startsWith('#') ? document.querySelector(id) : null;
      if (el) {
        e.preventDefault();
        window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 8, behavior: 'smooth' });
      }
    });
  });

  /* reveal on scroll */
  var els = document.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window && els.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add('in');
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    els.forEach(function (el) { io.observe(el); });
  } else {
    els.forEach(function (el) { el.classList.add('in'); });
  }

  /* countdown deadline comes from EVENT.date_iso (3 November 2026, 7:00 pm IST) */
  var box = document.querySelector('[data-deadline]');
  if (box) {
    var target = new Date(box.getAttribute('data-deadline')).getTime();
    var d = document.getElementById('cd-d'),
        h = document.getElementById('cd-h'),
        m = document.getElementById('cd-m'),
        s = document.getElementById('cd-s');
    var pad = function (n) { return String(n).padStart(2, '0'); };
    var tick = function () {
      var diff = target - Date.now();
      if (diff <= 0) {
        d.textContent = '00'; h.textContent = '00';
        m.textContent = '00'; s.textContent = '00';
        return;
      }
      d.textContent = pad(Math.floor(diff / 86400000));
      h.textContent = pad(Math.floor(diff / 3600000) % 24);
      m.textContent = pad(Math.floor(diff / 60000) % 60);
      s.textContent = pad(Math.floor(diff / 1000) % 60);
      setTimeout(tick, 1000 - (Date.now() % 1000));
    };
    tick();
  }
})();
