// Background sessions under the daemon (CLAUDE_BG_BACKEND=daemon) make their
// pty the controlling terminal with Bun.ant.loginTty(0): SIGHUP then shuts the
// worker down, and the commands it runs can open /dev/tty. Up to 2.1.301
// Claude called login_tty() itself through bun:ffi, with libc.so as the last
// candidate, which is where Bionic exports it; 2.1.302 moved the call into the
// private runtime. Without it the worker logs "ffi_unavailable" and runs with
// no controlling terminal. bun:ffi is opened on the first call.
(function () {
  if (typeof Bun === "undefined" || typeof process === "undefined") return;
  if (process.platform !== "android") return;
  if (!Bun.ant) {
    try {
      Bun.ant = {};
    } catch {
      return;
    }
  }
  if (typeof Bun.ant.loginTty === "function") return;

  var loginTtyLibc = null;
  Bun.ant.loginTty = function (fd) {
    if (!Number.isInteger(fd) || fd < 0) throw new TypeError("not a file descriptor: " + fd);
    if (loginTtyLibc === null) {
      loginTtyLibc = process.getBuiltinModule("bun:ffi").dlopen("libc.so", {
        login_tty: { args: ["i32"], returns: "i32" },
      });
    }
    // setsid(), TIOCSCTTY on fd, then fd is duplicated onto 0, 1 and 2.
    return loginTtyLibc.symbols.login_tty(fd) === 0;
  };
})();
