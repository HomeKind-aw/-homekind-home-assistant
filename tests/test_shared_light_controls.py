import copy
from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "homekind_bridge"))
from device_capabilities import capabilities, attributes, action_request, action_matches
from light_group import EXTENDED_PROTOCOL, PROTOCOL, execute_light_group, GroupRejected
from light_group_adapter import LightGroupAdapter


class SharedLightControls(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sent = []
        self.states = {e: {"entity_id": e, "state": "on", "last_updated": "old", "context": {"id": "old"}, "attributes": {"supported_color_modes": ["rgb", "color_temp"], "brightness": 100, "color_mode": "rgb", "rgb_color": [255, 0, 0], "min_color_temp_kelvin": 2700, "max_color_temp_kelvin": 6500}} for e in ("light.one", "light.two")}
        self.command = {"protocol": EXTENDED_PROTOCOL, "id": "00000000-0000-4000-8000-000000000001", "lease_id": "00000000-0000-4000-8000-000000000002", "household_id": "00000000-0000-4000-8000-000000000003", "group_id": "00000000-0000-4000-8000-000000000004", "revision": 1, "desired": "brightness", "parameters": {"value": 65}, "entities": list(self.states), "expires_at": 20}
        self.authorisations = 0

    def authorise(self, command):
        self.authorisations += 1
        return {**copy.deepcopy(command), "status": "dispatched"}

    def sleep(self, seconds):
        self.now += seconds

    def dispatch(self, domain, service, data):
        self.sent.append((domain, service, copy.deepcopy(data)))
        for state in self.states.values():
            state.update(state="off" if data.get("brightness_pct") == 0 else "on", last_updated="new", context={"id": "new"})
            if "brightness_pct" in data:
                state["attributes"]["brightness"] = round(data["brightness_pct"] * 255 / 100)
            if "rgb_color" in data:
                state["attributes"].update(rgb_color=data["rgb_color"], color_mode="rgb")
            if "color_temp_kelvin" in data:
                state["attributes"].update(color_temp_kelvin=data["color_temp_kelvin"], color_mode="color_temp")

    def run_action(self, **options):
        args = dict(read_states=lambda entities, timeout: copy.deepcopy(self.states), locally_enabled=lambda e: e in self.states, authorise=self.authorise, dispatch=self.dispatch, clock=lambda: self.now, wall_clock=lambda: self.now, sleep=self.sleep)
        args.update(options)
        return execute_light_group(self.command, **args)

    def test_brightness_colour_and_white_tone_each_use_one_exact_ha_action(self):
        for action, value, field, expected in [("brightness", 65, "brightness_pct", 65), ("colour", "#123456", "rgb_color", [18, 52, 86]), ("colour_temperature", 4000, "color_temp_kelvin", 4000)]:
            with self.subTest(action=action):
                self.setUp()
                self.command.update(desired=action, parameters={"value": value})
                self.assertEqual(self.run_action()["status"], "confirmed")
                self.assertEqual(self.sent, [("light", "turn_on", {"entity_id": list(self.states), field: expected})])

    def test_zero_brightness_confirms_off_instead_of_relying_on_retained_brightness(self):
        self.command["parameters"]["value"] = 0
        self.assertEqual(self.run_action()["status"], "confirmed")
        self.assertTrue(all(s["state"] == "off" for s in self.states.values()))

    def test_one_unsupported_or_incompatible_member_prevents_whole_dispatch(self):
        for action, value, attrs in [("colour", "#123456", {"supported_color_modes": ["brightness"]}), ("colour_temperature", 2800, {"min_color_temp_kelvin": 3000})]:
            with self.subTest(action=action):
                self.setUp()
                self.command.update(desired=action, parameters={"value": value})
                self.states["light.two"]["attributes"].update(attrs)
                with self.assertRaises(GroupRejected):
                    self.run_action()
                self.assertEqual((self.authorisations, self.sent), (0, []))

    def test_capability_loss_after_authorisation_prevents_dispatch(self):
        def auth(command):
            result = self.authorise(command)
            self.states["light.two"]["attributes"]["supported_color_modes"] = ["onoff"]
            return result
        with self.assertRaises(GroupRejected):
            self.run_action(authorise=auth)
        self.assertEqual((self.authorisations, self.sent), (1, []))

    def test_changed_parameters_after_authorisation_are_rejected(self):
        with self.assertRaises(GroupRejected):
            self.run_action(authorise=lambda c: {**c, "status": "dispatched", "parameters": {"value": 70}})
        self.assertEqual(self.sent, [])

    def test_v1_and_malformed_v2_cannot_smuggle_new_actions_or_extra_fields(self):
        for update in [{"protocol": PROTOCOL}, {"parameters": {"value": True}}, {"parameters": {"value": 101}}, {"parameters": {"value": 50, "service": "lock"}}, {"desired": "colour", "parameters": {"value": "red"}}]:
            with self.subTest(update=update):
                self.setUp()
                self.command.update(update)
                with self.assertRaises(GroupRejected):
                    self.run_action()
                self.assertEqual(self.sent, [])

    def test_capabilities_are_finite_and_report_only_actual_current_colour_mode(self):
        state = self.states["light.one"]
        self.assertEqual(capabilities(state)["actions"], ["on", "off", "brightness", "colour", "colour_temperature"])
        self.assertEqual(attributes(state)["colour"], "#ff0000")
        state["attributes"].update(color_mode="color_temp", color_temp_kelvin=3500)
        self.assertNotIn("colour", attributes(state))
        self.assertEqual(attributes(state)["colour_temperature"], 3500)
        self.assertEqual(action_request(state, "colour_temperature", {"value": 3500}), ("light", "turn_on", {"color_temp_kelvin": 3500}))
        self.assertTrue(action_matches(state, "colour_temperature", {"value": 3500}))
        state["state"] = "off"
        self.assertFalse(action_matches(state, "colour_temperature", {"value": 3500}))

    def test_v2_uncertain_dispatch_is_not_replayed_after_adapter_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            self.command.update(status="leased")
            def request(url, token, data=None, timeout=2):
                if url.endswith('group-advertise'):
                    return {"protocol": data['protocol'], "generation": 1}
                if url.endswith('group-poll'):
                    return {"command": copy.deepcopy(self.command)}
                if url.endswith('group-authorise'):
                    return self.authorise(self.command)
                if url.endswith('group-ack'):
                    return {"id": self.command['id'], "status": "unknown"}
                if url.endswith('/states'):
                    return copy.deepcopy(list(self.states.values()))
                if url.endswith('/services/light/turn_on'):
                    self.dispatch('light', 'turn_on', data)
                    raise TimeoutError('Synthetic uncertain response')
                raise AssertionError(url)
            bridge = SimpleNamespace(directory=Path(directory), credentials={"bridge_id": "synthetic", "token": "a" * 64}, read_only=False, stopping=lambda: False, allowlist=set(self.states), tracked=[{"entity_id": e, "control_enabled": True} for e in self.states], clock=lambda: self.now, sleep=self.sleep, ha_url='http://synthetic/core/api', ha_token='synthetic', request=request)
            adapter = LightGroupAdapter(bridge, monotonic=lambda: self.now)
            self.assertTrue(adapter.advertise())
            self.assertEqual(adapter.run_once()['status'], 'unknown')
            restarted = LightGroupAdapter(bridge, monotonic=lambda: self.now)
            self.assertTrue(restarted.advertise())
            self.assertEqual(restarted.run_once()['status'], 'already-recorded')
            self.assertEqual(len(self.sent), 1)
