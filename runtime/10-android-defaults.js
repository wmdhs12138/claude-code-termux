// A directly-executed Android standalone has no launcher to provide these
// safety defaults. Preserve explicit user choices, but otherwise avoid the
// embedded Linux/glibc ripgrep. (The auto-updater stays on: adapt_graph.py
// routes its installs to 20-self-update.js instead of the glibc download.)
(function () {
  if (typeof Bun === "undefined" || typeof process === "undefined") return;
  if (process.platform !== "android") return;
  if (process.env.USE_BUILTIN_RIPGREP === undefined) process.env.USE_BUILTIN_RIPGREP = "0";
})();
