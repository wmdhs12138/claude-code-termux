// A directly-executed Android standalone has no launcher to provide these
// safety defaults. Preserve explicit user choices, but otherwise avoid the
// embedded Linux/glibc ripgrep and the updater that would replace this
// bionic executable with an official glibc build.
(function () {
  if (typeof Bun === "undefined" || typeof process === "undefined") return;
  if (process.platform !== "android") return;
  if (process.env.USE_BUILTIN_RIPGREP === undefined) process.env.USE_BUILTIN_RIPGREP = "0";
  if (process.env.DISABLE_AUTOUPDATER === undefined) process.env.DISABLE_AUTOUPDATER = "1";
})();
