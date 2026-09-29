import subprocess
import sys
import pytest
from pathlib import Path

# Paths to the test plans
TEST_PLANS_DIR = Path(__file__).parent.parent / "test-plans"

def run_cli(*args, stdin=None):
    cmd = [sys.executable, "-m", "costguard"] + list(args)
    if stdin:
        return subprocess.run(cmd, input=stdin.encode("utf-8"), capture_output=True)
    else:
        return subprocess.run(cmd, capture_output=True)

def test_exit_0_within_budget():
    plan = TEST_PLANS_DIR / "plan_c_hostile_noise.json"
    result = run_cli("--plan", str(plan), "--max-increase", "10")
    assert result.returncode == 0
    assert b"PASSED" in result.stdout

def test_exit_1_over_budget():
    plan = TEST_PLANS_DIR / "plan_a_small_add.json"
    result = run_cli("--plan", str(plan), "--max-increase", "5")
    assert result.returncode == 1
    assert b"FAILED" in result.stdout
    assert b"CIRCUIT BREAKER" in result.stdout

def test_exit_2_missing_file():
    result = run_cli("--plan", "does_not_exist.json")
    assert result.returncode == 2
    assert b"No such file or directory" in result.stderr or b"not found" in result.stderr

def test_exit_2_corrupt_json():
    plan = TEST_PLANS_DIR / "plan_e_corrupt.json"
    result = run_cli("--plan", str(plan))
    assert result.returncode == 2
    assert b"Invalid JSON" in result.stderr
    assert b"Traceback" not in result.stderr

def test_no_budget_set():
    plan = TEST_PLANS_DIR / "plan_c_hostile_noise.json"
    result = run_cli("--plan", str(plan))
    assert result.returncode == 0
    assert b"No budget threshold set" in result.stdout
