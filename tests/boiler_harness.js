// Запускає shelly/boiler.js із заглушками Shelly API (для tests/test_boiler_script.py).
// Аргументи: шлях до скрипта, JSON сценарію:
//   myUptime      — uptime розетки на момент старту скрипта, с
//   initialOutput — чи ввімкнене реле на старті
//   responses     — відповіді модуля по черзі: {uptime: N} | {err: -104} | {body: "..."}
//   steps         — "timer" (спрацювати наступному таймеру) | {userSet: true/false}
const fs = require("fs");
const vm = require("vm");

const [scriptPath, scenarioJson] = process.argv.slice(2);
const scenario = JSON.parse(scenarioJson);
const calls = [];
const logs = [];
const timers = [];
const handlers = [];
const responses = (scenario.responses || []).slice();
let uptime = scenario.myUptime;
let output = Boolean(scenario.initialOutput);

function setOutput(on) {
  if (on === output) return;
  output = on;
  handlers.forEach((h) => h({ component: "switch:0", id: 0, delta: { id: 0, output: on } }));
}

const Shelly = {
  call(method, params, cb) {
    calls.push({ method, params });
    if (method === "HTTP.GET") {
      const r = responses.length ? responses.shift() : { err: -104 };
      if (r.err) {
        cb(undefined, r.err, "Deadline exceeded");
      } else {
        const body = r.body !== undefined ? r.body : JSON.stringify({ uptime: r.uptime });
        cb({ code: r.code || 200, body: body }, 0, "");
      }
      return;
    }
    if (method === "Switch.Set") setOutput(params.on);
    if (cb) cb({}, 0, "");
  },
  getComponentStatus(key) {
    if (key === "sys") return { uptime: uptime };
    if (key === "switch:0") return { id: 0, output: output };
    return null;
  },
  addStatusHandler(h) {
    handlers.push(h);
  },
};
const Timer = {
  set(ms, repeat, cb) {
    timers.push({ ms: ms, cb: cb });
    return timers.length;
  },
};

const context = vm.createContext({ Shelly: Shelly, Timer: Timer, print: (t) => logs.push(String(t)) });
vm.runInContext(fs.readFileSync(scriptPath, "utf8"), context, { filename: scriptPath });

for (const step of scenario.steps || []) {
  if (step === "timer") {
    const t = timers.shift();
    if (t) {
      uptime += t.ms / 1000;
      t.cb();
    }
  } else if (step && step.userSet !== undefined) {
    setOutput(step.userSet);
  }
}

console.log(JSON.stringify({ calls: calls, logs: logs, output: output, pendingTimers: timers.length }));
