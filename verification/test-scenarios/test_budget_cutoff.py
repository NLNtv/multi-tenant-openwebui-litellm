"""
Verification Test Scenario: Budget Cutoff and Metering Governance
Simulates chat completion requests using a tenant virtual key until budget exhaustion
and asserts that the hard cutoff mechanism immediately rejects further prompts.
"""

import requests
import pytest
from verification.mocks.mock_litellm_server import MockLiteLLMServer
from tenant_manager.litellm_client import LiteLLMClient


@pytest.fixture(scope="module")
def gateway():
    server = MockLiteLLMServer(port=14003)
    server.start()
    yield server
    server.stop()


def test_hard_budget_cutoff_enforcement(gateway):
    client = LiteLLMClient(
        base_url=gateway.url,
        master_key="sk-litellm-master-super-secret-key-change-in-prod"
    )

    # 1. Provision a tenant virtual key with a small $0.10 budget
    # In MockLiteLLMServer, each request incurs simulated $0.05 cost.
    key_resp = client.generate_virtual_key(
        tenant_id="metered-corp",
        max_budget=0.10,
        budget_duration="30d",
        models=["gpt-4o"],
        rpm_limit=60,
        tpm_limit=50000
    )
    virtual_key = key_resp["key"]
    chat_url = f"{gateway.url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {virtual_key}",
        "Content-Type": "application/json"
    }

    # Request 1: Spend becomes $0.05 (Budget remaining: $0.05)
    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Query 1"}]}
    r1 = requests.post(chat_url, headers=headers, json=payload)
    assert r1.status_code == 200
    data1 = r1.json()
    assert "choices" in data1
    assert data1["model"] == "gpt-4o"

    # Request 2: Spend becomes $0.10 (Budget remaining: $0.00)
    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Query 2"}]}
    r2 = requests.post(chat_url, headers=headers, json=payload)
    assert r2.status_code == 200

    # Verify spend in LiteLLM
    info = client.get_key_info(virtual_key)["info"]
    assert info["spend"] == 0.10
    assert info["max_budget"] == 0.10

    # Request 3: Budget is exhausted! Gateway MUST reject with 429/400 BudgetExceededError
    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Query 3 - Over budget"}]}
    r3 = requests.post(chat_url, headers=headers, json=payload)
    assert r3.status_code in (400, 429)
    err_body = r3.json()
    assert "error" in err_body
    assert "BudgetExceededError" in err_body["error"]["message"]
    print(f"\n[PASS] Verified Budget Cutoff: {err_body['error']['message']}")

    # 4. Dynamically increase budget by $0.10 (new budget: $0.20)
    client.update_virtual_key(virtual_key, max_budget=0.20)

    # Request 4: Should now succeed again!
    r4 = requests.post(chat_url, headers=headers, json=payload)
    assert r4.status_code == 200
    print("[PASS] Verified Service Restored after Budget Top-up!")
