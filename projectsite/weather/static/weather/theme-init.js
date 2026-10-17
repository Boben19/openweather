/* Runs before first paint so the page never flashes the wrong theme. */
(function () {
  var pref = 'system';
  try { pref = localStorage.getItem('ow-theme') || 'system'; } catch (e) {}
  if (pref !== 'light' && pref !== 'dark') pref = 'system';
  var dark = pref === 'dark' || (pref === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  var root = document.documentElement;
  root.setAttribute('data-theme', dark ? 'dark' : 'light');
  root.setAttribute('data-theme-pref', pref);
})();
