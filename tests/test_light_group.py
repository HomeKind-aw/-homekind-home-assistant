import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "homekind_bridge"))
from light_group import execute_light_group, GroupRejected, PROTOCOL


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.claim = {"protocol": PROTOCOL, "id": "00000000-0000-4000-8000-000000000001", "lease_id": "00000000-0000-4000-8000-000000000002", "household_id": "00000000-0000-4000-8000-000000000003", "group_id": "00000000-0000-4000-8000-000000000004", "revision": 1, "entities": ["light.one", "light.two"], "desired": "on", "expires_at": 20}
        self.states = {e: {"entity_id": e, "state": "off", "last_updated": "before", "context": {"id": "before"}, "attributes": {}} for e in self.claim["entities"]}
        self.enabled = set(self.states)
        self.calls = []
        self.auth_count = 0

    def authorise(self, claim):
        self.auth_count += 1
        if self.auth_count > 1:
            raise GroupRejected("Already dispatched.")
        return {**claim, "status": "dispatched"}

    def dispatch(self, *args):
        self.calls.append(args)
        for state in self.states.values():
            state.update(state="on", last_updated="after", context={"id": "after"})

    def sleep(self, amount):
        self.now += amount

    def run_group(self, **overrides):
        options = dict(read_states=lambda _, timeout: copy.deepcopy(self.states), locally_enabled=lambda e: e in self.enabled, authorise=self.authorise, dispatch=self.dispatch, clock=lambda: self.now, wall_clock=lambda: self.now, sleep=self.sleep)
        options.update(overrides)
        return execute_light_group(self.claim, **options)

    def test_all_ready_dispatches_once_with_all_exact_targets_and_confirms_every_member(self):
        result = self.run_group()
        self.assertEqual(self.calls, [("light", "turn_on", {"entity_id": ["light.one", "light.two"]})])
        self.assertEqual(result["status"], "confirmed")
        with self.assertRaises(GroupRejected):
            self.run_group()
        self.assertEqual(len(self.calls), 1)

    def test_unavailable_member_holds_every_light_then_fails_without_authorisation_or_dispatch(self):
        self.states["light.two"]["state"] = "unavailable"
        with self.assertRaises(GroupRejected):
            self.run_group()
        self.assertLessEqual(self.now, 3.01)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.auth_count, 0)

    def test_recovered_member_releases_whole_group_together(self):
        self.states["light.two"]["state"] = "unavailable"
        def sleep(amount):
            self.sleep(amount)
            self.states["light.two"]["state"] = "off"
        self.assertEqual(self.run_group(sleep=sleep)["status"], "confirmed")
        self.assertEqual(len(self.calls), 1)

    def test_revocation_during_wait_or_authorisation_prevents_any_switch(self):
        self.states["light.two"]["state"] = "unavailable"
        def sleep(amount):
            self.sleep(amount)
            self.enabled.clear()
        with self.assertRaises(GroupRejected):
            self.run_group(sleep=sleep)
        self.assertEqual(self.calls, [])
        self.setUp()
        def authorise(claim):
            self.enabled.clear()
            return self.authorise(claim)
        with self.assertRaises(GroupRejected):
            self.run_group(authorise=authorise)
        self.assertEqual(self.calls, [])

    def test_changed_authorised_group_or_offline_after_authorisation_cannot_dispatch(self):
        def authorise(claim):
            return {**self.authorise(claim), "revision": 2}
        with self.assertRaises(GroupRejected):
            self.run_group(authorise=authorise)
        self.assertEqual(self.calls, [])
        self.setUp()
        def offline(claim):
            self.states["light.two"]["state"] = "unavailable"
            return self.authorise(claim)
        with self.assertRaises(GroupRejected):
            self.run_group(authorise=offline)
        self.assertEqual(self.calls, [])

    def test_lost_dispatch_response_and_partial_execution_never_retry_or_claim_success(self):
        def lost(*args):
            self.calls.append(args)
            raise TimeoutError()
        self.assertEqual(self.run_group(dispatch=lost)["status"], "unknown")
        self.assertEqual(len(self.calls), 1)
        self.setUp()
        def partial(*args):
            self.calls.append(args)
            self.states["light.one"].update(state="on", last_updated="after")
        result = self.run_group(dispatch=partial)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(result["members"]["light.two"], "unconfirmed")
        self.assertEqual(len(self.calls), 1)

    def test_unsupported_scope_expiry_stop_and_local_opt_out_fail_closed(self):
        for change in ({"protocol": "future"}, {"desired": "toggle"}, {"entities": ["light.one", "lock.front"]}, {"entities": ["light.one", "light.one"]}, {"expires_at": float("nan")}, {"expires_at": 0}, {"revision": True}):
            self.setUp()
            self.claim.update(change)
            with self.assertRaises(GroupRejected):
                self.run_group()
            self.assertEqual(self.calls, [])
        self.setUp()
        with self.assertRaises(GroupRejected):
            self.run_group(stopped=lambda: True)

    def test_assumed_matching_state_is_not_group_confirmation(self):
        def assumed(*args):
            self.dispatch(*args)
            self.states["light.two"]["attributes"]["assumed_state"] = True
        result = self.run_group(dispatch=assumed)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(result["members"]["light.two"], "reported")


if __name__ == "__main__":
    unittest.main()
