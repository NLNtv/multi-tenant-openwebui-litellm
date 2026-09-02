"""
Integration tests for LiteLLMClient against MockLiteLLMServer.
"""

import pytest
from verification.mocks.mock_litellm_server import MockLiteLLMServer
from tenant_manager.litellm_client import LiteLLMClient, LiteLLMClientError


@pytest.fixture(scope="module")
def mock_server():
    server = MockLiteLLMServer(port=14001)
    server.start()
    yield server
    server.stop()


def test_virtual_key_lifecycle(mock_server):
    client = LiteLLMClient(
        base_url=mock_server.url,
        master_key="sk-litellm-master-super-secret-key-change-in-prod"
    )

    # 1. Generate Virtual Key
    key_resp = client.generate_virtual_key(
        tenant_id="test-tenant",
        max_budget=250.0,
        budget_duration="30d",
        models=["gpt-4o", "claude-3-5-sonnet"],
        rpm_limit=60,
        tpm_limit=50000,
        metadata={"tier": "enterprise"}
    )
    virtual_key = key_resp.get("key")
    assert virtual_key is not None
    assert virtual_key.startswith("sk-tenant-")

    # 2. Get Key Info
    info_resp = client.get_key_info(virtual_key)
    info = info_resp["info"]
    assert info["max_budget"] == 250.0
    assert info["spend"] == 0.0
    assert "gpt-4o" in info["models"]

    # 3. Update Virtual Key
    update_resp = client.update_virtual_key(
        key=virtual_key,
        max_budget=350.0,
        rpm_limit=100
    )
    assert update_resp["info"]["max_budget"] == 350.0
    assert update_resp["info"]["rpm_limit"] == 100

    # 4. Delete Virtual Key
    deleted = client.delete_virtual_key(virtual_key)
    assert deleted is True

    # 5. Query Deleted Key -> Should raise 404
    with pytest.raises(LiteLLMClientError) as exc:
        client.get_key_info(virtual_key)
    assert "404" in str(exc.value)


def test_invalid_master_key_raises_error(mock_server):
    client = LiteLLMClient(
        base_url=mock_server.url,
        master_key="sk-wrong-master-key"
    )
    with pytest.raises(LiteLLMClientError) as exc:
        client.generate_virtual_key(
            tenant_id="bad-tenant",
            max_budget=100.0,
            budget_duration="30d",
            models=["gpt-4o"],
            rpm_limit=10,
            tpm_limit=1000
        )
    assert "401" in str(exc.value)
