import argparse
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import quipctl as q

WALLET = "0x" + "1" * 40
OTHER = "0x" + "2" * 40


def args(state, *extra):
    return q.parser().parse_args(["install", "--state-dir", str(state), "--wallet", WALLET,
                                 "--public-host", "203.0.113.10", *extra])


class DeploymentBoundaryTests(unittest.TestCase):
    def test_invalid_wallet_rejected_before_state_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "new"
            a = args(state)
            a.wallet = "not-a-wallet-or-private-key"
            with self.assertRaises(q.KitError):
                q.make_plan(a)
            self.assertFalse(state.exists())

    def test_authenticated_rpc_is_not_advertised_on_chain(self):
        for url in ["wss://user:password@example.com/rpc", "https://example.com/rpc?api_key=abc", "wss://example.com/#token"]:
            with self.subTest(url=url), self.assertRaises(q.KitError):
                q.rpc_url(url)

    def test_physical_cores_before_smt_and_explicit_oversubscription(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(q, "cpu_order", return_value=([0, 2, 4, 6], 2)):
            with self.assertRaises(q.KitError):
                q.make_plan(args(Path(tmp) / "state", "--instances", "3"))
            plan = q.make_plan(args(Path(tmp) / "state", "--instances", "3", "--allow-smt"))
            self.assertEqual(plan["cpus"], [0, 2, 4])

    def test_dry_run_has_no_disk_docker_or_network_effects(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(q, "docker_ready") as docker, patch.object(q, "verify_chain") as rpc, patch("builtins.print"):
            state = Path(tmp) / "untouched"
            q.install(args(state, "--dry-run"))
            self.assertFalse(state.exists())
            docker.assert_not_called()
            rpc.assert_not_called()

    def test_nonempty_foreign_directory_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "foreign"
            state.mkdir()
            existing = state / "important.txt"
            existing.write_text("keep", encoding="utf-8")
            with patch.object(q, "docker_ready"), self.assertRaises(q.KitError):
                q.install(args(state))
            self.assertEqual(existing.read_text(), "keep")
            self.assertEqual([x.name for x in state.iterdir()], ["important.txt"])

    def test_repeat_install_keeps_existing_test_identity_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            plan = q.make_plan(args(state))
            m = q.prepare(plan)
            # Deliberately synthetic fixture, never a real node key.
            identity = state / "instances/1/keystore.json"
            identity.write_text("TEST_ONLY_DO_NOT_USE", encoding="utf-8")
            m["startedAtUTC"] = q.now()
            q.write_json(state / "manifest.json", m)
            before = identity.read_bytes()
            q.prepare(q.make_plan(args(state)))
            self.assertEqual(identity.read_bytes(), before)
            with self.assertRaises(q.KitError):
                changed = copy.deepcopy(plan)
                changed["wallet"] = OTHER
                q.prepare(changed)
            self.assertEqual(identity.read_bytes(), before)

    def test_missing_identity_on_existing_install_does_not_regenerate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            m = q.prepare(q.make_plan(args(state)))
            m["startedAtUTC"] = q.now()
            q.write_json(state / "manifest.json", m)
            with self.assertRaisesRegex(q.KitError, "密钥"):
                q.prepare(q.make_plan(args(state)))
            self.assertFalse((state / "instances/1/keystore.json").exists())

    def test_container_name_collision_never_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = q.make_plan(args(Path(tmp) / "state"))
            foreign = {"Config": {"Labels": {"com.docker.compose.project": "another-project"}}}
            with patch.object(q, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps([foreign]), "")):
                with self.assertRaises(q.KitError):
                    q.owned_container(m, 1)

    def test_restart_missing_key_refuses_before_any_docker_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            q.prepare(q.make_plan(args(state)))
            command = q.parser().parse_args(["restart", "--state-dir", str(state)])
            with patch.object(q, "docker_ready"), patch.object(q, "operation_lock", return_value=q.contextlib.nullcontext()), patch.object(q, "owned_container"), patch.object(q, "run") as run:
                with self.assertRaisesRegex(q.KitError, "密钥"):
                    q.lifecycle(command)
                run.assert_not_called()

    def test_rpc_wrong_genesis_is_refused(self):
        with patch.object(q, "get_json", return_value={"result": "0xwrong"}):
            with self.assertRaisesRegex(q.KitError, "创世"):
                q.verify_chain(["wss://example.com/rpc"])

    def test_rpc_fails_over_and_only_reads_genesis(self):
        with patch.object(q, "get_json", side_effect=[OSError("offline"), {"result": q.GENESIS}]) as call:
            endpoint = q.verify_chain(["wss://a.example/rpc", "wss://b.example/rpc"])
            self.assertEqual(endpoint, "wss://b.example/rpc")
            for invocation in call.call_args_list:
                self.assertEqual(invocation.args[1]["method"], "chain_getBlockHash")

    def test_same_helper_accepts_state_option_in_both_positions(self):
        self.assertEqual(q.parser().parse_args(["--state-dir", "/tmp/qnk-test", "status"]).state_dir,
                         q.parser().parse_args(["status", "--state-dir", "/tmp/qnk-test"]).state_dir)


class PointsEvidenceTests(unittest.TestCase):
    @staticmethod
    def record(date, node, address=WALLET, found=True):
        return {"checkedAtUTC": date, "wallet": address,
                "data": {"found": found, "balance": {"node": node, "total": node + 3}, "projection": {"node": 999999}}}

    def test_under_24_hours_never_extrapolates_daily_yield(self):
        a = self.record("2026-10-20T00:00:00+00:00", 100)
        b = self.record("2026-10-20T01:00:00+00:00", 110)
        result = q.compare_records(a, b)
        self.assertEqual(result["deltaQP"]["node"], 10)
        self.assertNotIn("observedNodeQPPer24Hours", result)

    def test_two_day_window_normalizes_only_observed_delta(self):
        result = q.compare_records(self.record("2026-10-20T00:00:00Z", 100), self.record("2026-10-22T00:00:00Z", 160))
        self.assertEqual(result["observedNodeQPPer24Hours"], 30)
        self.assertNotIn("projection", result["deltaQP"])

    def test_wrong_wallet_or_unrecognized_user_cannot_be_compared(self):
        a = self.record("2026-10-20T00:00:00Z", 100)
        for b in [self.record("2026-10-22T00:00:00Z", 200, address=OTHER), self.record("2026-10-22T00:00:00Z", 0, found=False)]:
            with self.assertRaises(q.KitError):
                q.compare_records(a, b)

    def test_point_correction_is_not_reported_as_negative_daily_income(self):
        result = q.compare_records(self.record("2026-10-20T00:00:00Z", 100), self.record("2026-10-22T00:00:00Z", 90))
        self.assertEqual(result["deltaQP"]["node"], -10)
        self.assertNotIn("observedNodeQPPer24Hours", result)


if __name__ == "__main__":
    unittest.main()
