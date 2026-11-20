// Бойлер — лише від міського живлення.
//
// Скрипт для розетки бойлера (Shelly Gen2/Gen3), що стоїть у квартирі після
// ручного перемикача «місто — резерв». Модуль у щитку стоїть на лінії міста
// (до перемикача): коли міста немає, він знеструмлений і не відповідає.
//
// Кожне перемикання знеструмлює розетку, тож після кожного її увімкнення
// джерело живлення вже не зміниться до наступного перезавантаження. Скрипт
// один раз визначає, від чого розетка живиться:
//   - модуль не відповідає — міста немає (резерв або місто ще не з'явилося):
//     бойлер вимкнений, скрипт питає модуль далі;
//   - модуль увімкнувся раніше за розетку (або разом із нею) — розетка
//     увімкнулася від міста: бойлер вмикається, далі ним можна керувати вручну;
//   - модуль увімкнувся пізніше за розетку — місто повернулося, а розетка
//     ще на резерві: бойлер вимкнений до перемикання на місто.
//
// Поки джерело не з'ясовано або це резерв, будь-яке ввімкнення бойлера
// (з застосунку, за розкладом) скрипт одразу скасовує.
//
// Встановлення: веб-сторінка розетки → Scripts → Create script → вставити цей
// код → Save → Start, і ввімкнути «Run on startup». Модулю в щитку потрібна
// постійна IP-адреса (резервування DHCP у роутері) і веб-сторінка без пароля.

let CITY_URL = "http://192.168.31.215/rpc/Sys.GetStatus"; // модуль на лінії міста
let SWITCH_ID = 0;
let BOOT_TOLERANCE = 30; // с; модуль і розетка, увімкнені одночасно, стартують із різницею в кілька секунд
let RETRY_MS = 10000;    // як часто питати модуль, поки джерело невідоме
let HTTP_TIMEOUT = 5;    // с

let mode = "unknown"; // unknown | city | reserve

function log(text) {
  print("[бойлер] " + text);
}

function turn(on) {
  Shelly.call("Switch.Set", { id: SWITCH_ID, on: on });
}

function myUptime() {
  return Shelly.getComponentStatus("sys").uptime;
}

function decide(cityUptime) {
  let mine = myUptime();
  if (cityUptime + BOOT_TOLERANCE >= mine) {
    mode = "city";
    log("живлення від міста — вмикаю бойлер (модуль: " + cityUptime + " с, розетка: " + mine + " с)");
    turn(true);
  } else {
    mode = "reserve";
    log("місто повернулося, але розетка ще на резерві — бойлер вимкнений до перемикання " +
        "(модуль: " + cityUptime + " с, розетка: " + mine + " с)");
    turn(false);
  }
}

function check() {
  Shelly.call("HTTP.GET", { url: CITY_URL, timeout: HTTP_TIMEOUT }, function (res, errCode) {
    if (mode !== "unknown") {
      return;
    }
    let uptime = null;
    if (errCode === 0 && res && res.code === 200) {
      try {
        let status = JSON.parse(res.body);
        if (status && typeof status.uptime === "number") {
          uptime = status.uptime;
        }
      } catch (e) {
        log("незрозуміла відповідь модуля");
      }
    }
    if (uptime === null) {
      // Немає струму в модулі (або він ще вмикається) — бойлер вимкнений
      Timer.set(RETRY_MS, false, check);
      return;
    }
    decide(uptime);
  });
}

// Поки джерело невідоме або це резерв, бойлер не вмикається навіть вручну
Shelly.addStatusHandler(function (event) {
  if (mode === "city") {
    return;
  }
  if (event.component === "switch:" + SWITCH_ID && event.delta && event.delta.output === true) {
    log("на резерві бойлер не вмикаємо — вимикаю");
    turn(false);
  }
});

// Після будь-якої появи живлення бойлер вимкнений, доки скрипт не вирішить інакше
Shelly.call("Switch.SetConfig", { id: SWITCH_ID, config: { initial_state: "off" } });
let current = Shelly.getComponentStatus("switch:" + SWITCH_ID);
if (current && current.output === true) {
  turn(false);
}
log("перевіряю, чи є живлення від міста");
check();
