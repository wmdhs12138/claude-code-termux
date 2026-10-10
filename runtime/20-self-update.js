// Intercept the official updater before Claude's CLI sees it. The official
// download is a glibc standalone, so updating a bionic graft means building
// a new graft locally and atomically replacing this executable. The work is
// done by self-update.sh, which tools/assemble_runtime.py inlines below.
//
// Claude's own auto-updater installs through installLatest(), which
// adapt_graph.py (native_updater) points at __claudeTermuxInstallLatest below:
// the same update, run in the background with the TUI's usual status line.
// With the auto-updater turned off, an interactive start prints a notice.
(function () {
  if (typeof Bun === "undefined" || typeof process === "undefined") return;
  if (process.platform !== "android") return;
  var argv = process.argv || [];
  var updateScript = __include__("self-update.sh");
  globalThis.__claudeTermuxInstallLatest = installLatest;
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

  function cacheBase() {
    var env = process.env;
    return env.XDG_CACHE_HOME || (env.HOME ? env.HOME + "/.cache" : "");
  }

  // installLatest(channel, force, storage) for Claude's auto-updater (TUI
  // mount, then every 30 minutes), `claude update` and `claude install`:
  // check, and build the latest approved release when this binary is not it.
  // Output goes to a log, never the terminal. A failed check (offline) is
  // "nothing to do"; a failed build is an error the TUI reports.
  async function installLatest() {
    var fs = process.getBuiltinModule("fs");
    var base = cacheBase() || "/data/local/tmp";
    var dir = base + "/claude-code-termux/self-update";
    fs.mkdirSync(dir, { recursive: true });
    var log = dir + "/auto-update.log";
    // One build at a time across sessions; a lock older than an hour is stale.
    var lock = dir + "/auto-update.lock";
    try {
      fs.mkdirSync(lock);
    } catch (e) {
      if (Date.now() - fs.statSync(lock).mtimeMs < 3600 * 1000) {
        return { latestVersion: null, wasUpdated: false, lockFailed: true };
      }
      fs.rmSync(lock, { recursive: true, force: true });
      fs.mkdirSync(lock);
    }
    try {
      var check = await selfUpdate("1", base, log);
      var line = check.stdout.trim().split("\n").pop() || "";
      var tag = /\bv(\d+\.\d+\.\d+)(?:-r\d+)?\b/.exec(line);
      if (check.code !== 0 || !tag) return { latestVersion: null, wasUpdated: false, lockFailed: false };
      if (!/^Update available: /.test(line)) {
        return { latestVersion: tag[1], wasUpdated: false, lockFailed: false };
      }
      var update = await selfUpdate("0", base, log);
      if (update.code !== 0) throw new Error("claude update failed; see " + log);
      return { latestVersion: tag[1], wasUpdated: true, lockFailed: false };
    } finally {
      fs.rmSync(lock, { recursive: true, force: true });
    }
  }

  // self-update.sh in check ("1") or update ("0") mode; stderr, and in update
  // mode stdout too, appended to log.
  async function selfUpdate(check, base, log) {
    var fs = process.getBuiltinModule("fs");
    var fd = fs.openSync(log, "a");
    try {
      var env = Object.assign({}, process.env);
      delete env.CLAUDE_CODE_TERMUX_PROGRESS_FD;
      var proc = Bun.spawn({
        cmd: ["bash", "-c", updateScript, "claude-self-update", process.execPath, "0", check, base],
        stdin: "ignore",
        stdout: check === "1" ? "pipe" : fd,
        stderr: fd,
        env: env,
      });
      var stdout = check === "1" ? await new Response(proc.stdout).text() : "";
      var code = await proc.exited;
      if (stdout) fs.writeSync(fd, stdout);
      return { code: code, stdout: stdout };
    } finally {
      fs.closeSync(fd);
    }
  }

  // With the auto-updater off (DISABLE_AUTOUPDATER), an interactive start
  // prints one line when the last check found another approved build: a new
  // version or a re-cut of this one. The check is self-update.sh in check
  // mode, run detached at most every 20 hours, or at once after this binary
  // was replaced; it never installs anything. Claude's fullscreen TUI covers
  // the line until it exits.
  function updateNotice() {
    var env = process.env;
    // Truthy as Claude parses it: "0" or "false" leaves the auto-updater on.
    if (!/^(1|true|yes|on)$/i.test(env.DISABLE_AUTOUPDATER || "") ||
        env.CLAUDE_CODE_TERMUX_UPDATE_NOTICE === "0" ||
        env.DISABLE_UPDATES || env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC) return;
    for (var i = 1; i < argv.length; i++) {
      if (/^(-p|--print|-v|--version|-h|--help)$/.test(argv[i])) return;
    }
    if (!process.stdout.isTTY || !process.stderr.isTTY) return;
    var base = cacheBase();
    if (!base) return;
    var fs = process.getBuiltinModule("fs");
    var dir = base + "/claude-code-termux";
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
      "claude-update-notice", updateScript, process.execPath, base, identity, file,
    ], { detached: true, stdio: "ignore", env: env }).unref();
  }
})();
