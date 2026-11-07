// Since 2.1.319 Claude refuses to create a node:vm context (Code Mode,
// workflows, function hooks) unless it can recognize the context's global
// object, and it treats any such global as "a Proxy or a vm context" when it
// walks data that comes out of a sandbox:
//
//   r = Bun.ant?.isVmContextGlobal            (read once, when the module loads)
//   if (e !== void 0 && !vm.isContext(e) && r?.(e) !== true) throw Error("Refusing to make a node:vm context: ...")
//   fd(t) = isProxy(t) || registered(t) || vm.isContext(t) || r?.(t) === true
//
// Bun 1.4.4's vm.isContext() also accepts a context's global, which Node's
// does not; this interface exists so that nothing depends on that. Answer
// from the object itself: seen from outside, the global of every node:vm
// context (createContext, runInNewContext, Script#runInNewContext) is a JSC
// cell of class NodeVMGlobalProxy, which no script can construct and whose
// class never changes. bun:jsc's describe() names a cell's class. Its
// describeArray() shows the butterfly, which a global proxy never has (every
// property lives on the global behind it), so an object that has one is ruled
// out before describe() would print its whole property table.
//
// The modules that read Bun.ant.isVmContextGlobal are loaded by dynamic
// import() after this file has run. tools/check_native_abi.py fails the build
// if a Bun.ant read ever moves into the entry module's static imports.
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
  if (typeof Bun.ant.isVmContextGlobal === "function") return;

  // Pointers are printed with the libc's %p, which spells a null one as 0x0,
  // (nil) or a padded 0x000...0 depending on the platform. Only a butterfly
  // that is plainly there rules an object out; an unreadable line falls
  // through to describe(), which is slower but not wrong.
  var BUTTERFLY = /^<Butterfly: ([^;]*);/;
  var NULL_POINTER = /^(?:\(nil\)|\(null\)|(?:0x)?0+)$/i;
  var CELL_CLASS = /\(Structure [^:]*:\[[^,]*, (\w+),/;
  var getPrototypeOf = Object.getPrototypeOf;
  var objectPrototype = Object.prototype;
  var arrayPrototype = Array.prototype;
  var answers = new WeakMap();
  var isProxy = null;
  var jsc = null;

  Bun.ant.isVmContextGlobal = function (value) {
    if (typeof value !== "object" || value === null) return false;
    if (isProxy === null) {
      isProxy = process.getBuiltinModule("util").types.isProxy;
      jsc = process.getBuiltinModule("bun:jsc");
    }
    // A Proxy is a ProxyObject cell, and anything else asked of it could run
    // one of its traps.
    if (isProxy(value)) return false;
    // This realm's plain objects and arrays, most of what Claude asks about. A
    // context's global inherits from its own realm's Object.prototype; code
    // inside the context cannot reach this realm's to swap it in.
    var proto = getPrototypeOf(value);
    if (proto === objectPrototype || proto === arrayPrototype) return false;
    var answer = answers.get(value);
    if (answer === undefined) {
      var butterfly = BUTTERFLY.exec(jsc.describeArray(value));
      answer = !(butterfly && !NULL_POINTER.test(butterfly[1].trim())) &&
        (CELL_CLASS.exec(jsc.describe(value)) || [])[1] === "NodeVMGlobalProxy";
      answers.set(value, answer);
    }
    return answer;
  };
})();
