"""
Master Verification Runner
Executes all unit, integration, and security verification scenarios,
generating an enterprise validation report.
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "tools" / "tenant-manager"))
os.environ["PYTHONPATH"] = f"{root_dir};{root_dir / 'tools' / 'tenant-manager'};{os.environ.get('PYTHONPATH', '')}"

import pytest


def main():
    print("=" * 80)
    print("ENTERPRISE B2B MULTI-TENANT OPENWEBUI & LITELLM PLATFORM")
    print("AUTOMATED VERIFICATION & TEST SUITE")
    print("=" * 80)

    pytest_args = [
        "-v",
        "--tb=short",
        "-o", "pythonpath=.",
        "tools/tenant-manager/tests",
        "verification/test-scenarios",
    ]

    exit_code = pytest.main(pytest_args)
    if exit_code == 0:
        print("\n" + "=" * 80)
        print("ALL SECURITY, ISOLATION, BUDGET, AND LIFECYCLE TESTS PASSED (100%)")
        print("=" * 80)
    else:
        print(f"\n[ERROR] Test suite exited with failure code: {exit_code}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
