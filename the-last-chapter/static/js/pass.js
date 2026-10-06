/* the last chapter — digital pass: share / copy */
(function () {
  'use strict';
  document.querySelectorAll('[data-share]').forEach(function (a) {
    a.addEventListener('click', function () {
      var url = a.getAttribute('data-share');
      var title = 'the last chapter — my pass';
      if (navigator.share) {
        navigator.share({ title: title, text: 'my pass for the final gathering 🐈‍⬛', url: url }).catch(function () {});
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(function () {
          var old = a.textContent;
          a.textContent = 'link copied ✓';
          setTimeout(function () { a.textContent = old; }, 1800);
        }).catch(function () {});
      }
    });
  });
  document.querySelectorAll('[data-copy]').forEach(function (a) {
    a.addEventListener('click', function () {
      var url = a.getAttribute('data-copy');
      if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(function () {
          var old = a.textContent;
          a.textContent = 'copied ✓';
          setTimeout(function () { a.textContent = old; }, 1800);
        }).catch(function () {});
      }
    });
  });
})();
