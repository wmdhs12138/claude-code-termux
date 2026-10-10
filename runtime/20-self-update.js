// Intercept the official updater before Claude's CLI sees it. The official
// download is a glibc standalone, so updating a bionic graft means building
// a new graft locally and atomically replacing this executable. The work is
// done by self-update.sh, which tools/assemble_runtime.py inlines below.
//
// Every other start may print an update notice instead: with the official
// auto-updater off (10-android-defaults.js), Claude's own update check and
// notice are off too.
(function () {
  if (typeof Bun === "undefined" || typeof process === "undefined") return;
  if (process.platform !== "android") return;
  var argv = process.argv || [];
  var updateScript = __include__("self-update.sh");
  var updateIndex = -1;
  for (var ai = 1; ai < argv.length && ai <= 2; ai++) {
    if (argv[ai] === "update" || argv[ai] === "upgrade") {
      updateIndex = ai;
      break;
    }
  }
  if (updateIndex === -1) {
    try {
      updateNotice();
    } catch (e) {}
    return;
  }
  var updateArgs = argv.slice(updateIndex + 1);
  var updateCheck = updateArgs.indexOf("--check") !== -1;
  var updateForce = updateArgs.indexOf("--force") !== -1;
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

  // An interactive start prints one line when the last check found another
  // approved build: a new version or a re-cut of this one. The check is
  // self-update.sh in check mode, run detached at most every 20 hours, or at
  // once after this binary was replaced; it never installs anything. Claude's
  // fullscreen TUI covers the line until it exits.
  function updateNotice() {
    var env = process.env;
    if (env.CLAUDE_CODE_TERMUX_UPDATE_NOTICE === "0" || env.DISABLE_UPDATES ||
        env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC) return;
    for (var i = 1; i < argv.length; i++) {
      if (/^(-p|--print|-v|--version|-h|--help)$/.test(argv[i])) return;
    }
    if (!process.stdout.isTTY || !process.stderr.isTTY) return;
    var cacheBase = env.XDG_CACHE_HOME || (env.HOME ? env.HOME + "/.cache" : "");
    if (!cacheBase) return;
    var fs = process.getBuiltinModule("fs");
    var dir = cacheBase + "/claude-code-termux";
    var file = dir + "/update-notice";
    // The cache's first line is the binary it was checked for: a replaced
    // binary (`claude update`) makes the cached result stale.
    var stat = fs.statSync(process.execPath);
    var identity = stat.size + ":" + Math.round(stat.mtimeMs);
    var lines = [];
    var age = Infinity;
    try {
      age = Date.now() - fs.statSync(file).mtimeMs;
      lines = fs.readFileSync(file, "utf8").split("\n");
    } catch (e) {}
    var current = lines[0] === identity;
    if (current && /^Update available: /.test(lines[1] || "")) {
      process.stderr.write(lines[1] + "\n");
    }
    if (current && age < 20 * 3600 * 1000) return;
    var marker = file + ".checking";
    try {
      if (Date.now() - fs.statSync(marker).mtimeMs < 10 * 60 * 1000) return;
    } catch (e) {}
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(marker, "");
    process.getBuiltinModule("child_process").spawn("bash", [
      "-c",
      'trap \'rm -f "$5.checking"\' EXIT; ' +
        'out="$(bash -c "$1" claude-self-update "$2" 0 1 "$3" 2>/dev/null)" || exit 0; ' +
        'printf "%s\\n%s\\n" "$4" "$out" > "$5.$$" && mv -f "$5.$$" "$5"',
      "claude-update-notice", updateScript, process.execPath, cacheBase, identity, file,
    ], { detached: true, stdio: "ignore", env: env }).unref();
  }
})();
