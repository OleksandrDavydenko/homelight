"""Перевірка скрипта бойлера shelly/boiler.js у Node.js із заглушками Shelly API."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "shelly" / "boiler.js"
HARNESS = ROOT / "tests" / "boiler_harness.js"
NODE = shutil.which("node")

NO_ANSWER = {"err": -104}


@unittest.skipUnless(NODE, "потрібен Node.js")
class BoilerScriptTest(unittest.TestCase):
    def run_script(self, my_uptime, responses, steps=(), initial_output=False):
        scenario = {"myUptime": my_uptime, "responses": list(responses), "steps": list(steps),
                    "initialOutput": initial_output}
        result = subprocess.run([NODE, str(HARNESS), str(SCRIPT), json.dumps(scenario)],
                                capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    @staticmethod
    def switch_sets(result):
        return [c["params"]["on"] for c in result["calls"] if c["method"] == "Switch.Set"]

    def test_power_on_default_is_off(self):
        result = self.run_script(20, [{"uptime": 18}])
        config_calls = [c for c in result["calls"] if c["method"] == "Switch.SetConfig"]
        self.assertEqual(config_calls[0]["params"], {"id": 0, "config": {"initial_state": "off"}})

    def test_city_power_turns_boiler_on_and_allows_manual_control(self):
        # Модуль і розетка увімкнулися разом — живлення від міста
        result = self.run_script(20, [{"uptime": 18}], steps=[{"userSet": False}])
        self.assertEqual(self.switch_sets(result), [True])
        self.assertFalse(result["output"])  # вручну вимкнули — скрипт не заважає
        result = self.run_script(20, [{"uptime": 18}], steps=[{"userSet": False}, {"userSet": True}])
        self.assertTrue(result["output"])
        self.assertEqual(result["pendingTimers"], 0)

    def test_plug_rebooted_later_than_module_is_city(self):
        # Ви перейшли з резерву на місто: модуль працює давно, розетка — щойно
        result = self.run_script(15, [{"uptime": 5000}])
        self.assertTrue(result["output"])

    def test_reserve_keeps_boiler_off_even_manually(self):
        # Модуль знеструмлений (резерв) — не відповідає
        result = self.run_script(30, [NO_ANSWER, NO_ANSWER],
                                 steps=["timer", {"userSet": True}, "timer"])
        self.assertFalse(result["output"])
        self.assertNotIn(True, [c for c in self.switch_sets(result)])
        self.assertEqual(result["pendingTimers"], 1)  # і далі питає модуль
        self.assertTrue(any("на резерві бойлер не вмикаємо" in line for line in result["logs"]))

    def test_city_returned_but_still_on_reserve(self):
        # Розетка на резерві годину, місто повернулося 100 с тому
        result = self.run_script(3600, [NO_ANSWER, NO_ANSWER, {"uptime": 100}],
                                 steps=["timer", "timer", {"userSet": True}])
        self.assertFalse(result["output"])
        self.assertEqual(self.switch_sets(result), [False, False])
        self.assertEqual(result["pendingTimers"], 0)  # рішення ухвалено до перезавантаження
        self.assertTrue(any("ще на резерві" in line for line in result["logs"]))

    def test_module_answers_after_retries_on_city(self):
        # Розетка увімкнулася від міста, а модуль ще вмикав Wi-Fi
        result = self.run_script(5, [NO_ANSWER, {"code": 500, "body": "err"}, {"body": "{oops"},
                                     {"uptime": 33}], steps=["timer", "timer", "timer"])
        self.assertTrue(result["output"])

    def test_tolerance(self):
        self.assertTrue(self.run_script(50, [{"uptime": 25}])["output"])   # 25 + 30 ≥ 50
        self.assertFalse(self.run_script(50, [{"uptime": 15}])["output"])  # 15 + 30 < 50

    def test_relay_on_at_install_is_turned_off_until_decided(self):
        result = self.run_script(4000, [NO_ANSWER], initial_output=True)
        self.assertFalse(result["output"])
        result = self.run_script(4000, [{"uptime": 9000}], initial_output=True)
        self.assertTrue(result["output"])


if __name__ == "__main__":
    unittest.main()
