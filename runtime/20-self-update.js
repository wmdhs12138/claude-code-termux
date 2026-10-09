// Intercept the official updater before Claude's CLI sees it. The official
// download is a glibc standalone, so updating a bionic graft means building
// a new graft locally and atomically replacing this executable. The work is
// done by self-update.sh, which tools/assemble_runtime.py inlines below.
(function () {
  if (typeof Bun === "undefined" || typeof process === "undefined") return;
  if (process.platform !== "android") return;
  var argv = process.argv || [];
  var updateIndex = -1;
  for (var ai = 1; ai < argv.length && ai <= 2; ai++) {
    if (argv[ai] === "update" || argv[ai] === "upgrade") {
      updateIndex = ai;
      break;
    }
  }
  if (updateIndex === -1) return;
  var updateArgs = argv.slice(updateIndex + 1);
  var updateCheck = updateArgs.indexOf("--check") !== -1;
  var updateForce = updateArgs.indexOf("--force") !== -1;
  var updateScript = __include__("self-update.sh");
  var updateResult = Bun.spawnSync({
    cmd: [
      "bash",
      "-c",
      updateScript,
      "claude-self-update",
      process.execPath,
      updateForce ? "1" : "0",
      updateCheck ? "1" : "0",
      process.env.XDG_CACHE_HOME || (process.env.HOME ? process.env.HOME + "/.cache" : "/data/local/tmp"),
    ],
    stdin: "inherit",
    stdout: "inherit",
    stderr: "inherit",
    env: process.env,
  });
  process.exit(updateResult.exitCode === 0 ? 0 : updateResult.exitCode || 1);
})();
