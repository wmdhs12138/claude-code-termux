// Cross-session messaging (SendMessage, peer receipts and idle notices)
// refuses to write to a local socket unless Bun.ant.getPeerPid(fd) and
// Bun.ant.getPeerUid(fd) name the process and user on the other end. Read
// the kernel's SO_PEERCRED record through Bionic libc. bun:ffi is opened on
// the first lookup, so sessions that never message a peer pay nothing.
//
// memoryPressureLevel() stays absent on purpose: Claude only reads it on
// macOS, and the Linux path it takes here uses os.freemem(), which Bun
// already derives from MemAvailable.
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
  if (typeof Bun.ant.getPeerPid === "function" && typeof Bun.ant.getPeerUid === "function") return;

  var SOL_SOCKET = 1;
  var SO_PEERCRED = 17;
  var peerCredLibc = null;
  var peerCred = function (fd) {
    if (!Number.isInteger(fd) || fd < 0) throw new TypeError("not a socket fd: " + fd);
    var ffi = process.getBuiltinModule("bun:ffi");
    if (peerCredLibc === null) {
      peerCredLibc = ffi.dlopen("libc.so", {
        getsockopt: { args: ["i32", "i32", "i32", "ptr", "ptr"], returns: "i32" },
      });
    }
    // struct ucred { pid_t pid; uid_t uid; gid_t gid; }
    var cred = new Uint32Array(3);
    var length = new Uint32Array([cred.byteLength]);
    var rc = peerCredLibc.symbols.getsockopt(fd, SOL_SOCKET, SO_PEERCRED, ffi.ptr(cred), ffi.ptr(length));
    if (rc !== 0 || length[0] !== cred.byteLength) {
      throw new Error("getsockopt(SO_PEERCRED) failed on fd " + fd);
    }
    return cred;
  };
  if (typeof Bun.ant.getPeerPid !== "function") {
    Bun.ant.getPeerPid = function (fd) {
      return peerCred(fd)[0];
    };
  }
  if (typeof Bun.ant.getPeerUid !== "function") {
    Bun.ant.getPeerUid = function (fd) {
      return peerCred(fd)[1];
    };
  }
})();
