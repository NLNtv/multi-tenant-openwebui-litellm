"""
Unit and integration tests for ProvisioningTransactionManager and atomic rollback.
"""

from pathlib import Path
import pytest
from unittest.mock import MagicMock

from verification.mocks.mock_litellm_server import MockLiteLLMServer
from tenant_manager.litellm_client import LiteLLMClient
from tenant_manager.k8s_provisioner import K8sProvisioner, K8sProvisionerError
from tenant_manager.models import TenantSpecification
from tenant_manager.transaction import ProvisioningTransactionManager
from tenant_manager.validator import SpecValidator


@pytest.fixture(scope="module")
def mock_server():
    server = MockLiteLLMServer(port=14002)
    server.start()
    yield server
    server.stop()


def test_successful_provision_and_deprovision(mock_server, tmp_path):
    result = SpecValidator.validate_file("tenants/examples/acme-corp.yaml")
    spec = result.spec

    litellm_client = LiteLLMClient(
        base_url=mock_server.url,
        master_key="sk-litellm-master-super-secret-key-change-in-prod"
    )
    # Mock K8sProvisioner to simulate successful kubectl operations
    mock_k8s = MagicMock(spec=K8sProvisioner)
    mock_k8s.render_manifests.return_value = [{"kind": "Namespace"}]
    mock_k8s.apply_manifests.return_value = True
    mock_k8s.delete_namespace.return_value = True

    reg_file = tmp_path / "registry.json"
    tx_manager = ProvisioningTransactionManager(litellm_client, mock_k8s, registry_path=reg_file)

    # 1. Provision
    success, msg, vkey = tx_manager.provision_tenant(spec)
    assert success is True
    assert vkey is not None
    assert vkey.startswith("sk-tenant-")

    # Verify key actually exists in mock LiteLLM
    key_info = litellm_client.get_key_info(vkey)
    assert key_info["info"]["max_budget"] == spec.governance.max_budget_usd

    # Verify recorded in registry
    assert reg_file.exists()

    # 2. Deprovision
    deprov_success, deprov_msg = tx_manager.deprovision_tenant(spec.metadata.tenant_id, vkey)
    assert deprov_success is True

    # Verify key was revoked in mock LiteLLM
    with pytest.raises(Exception):
        litellm_client.get_key_info(vkey)


def test_rollback_on_k8s_failure(mock_server, tmp_path):
    result = SpecValidator.validate_file("tenants/examples/acme-corp.yaml")
    spec = result.spec

    litellm_client = LiteLLMClient(
        base_url=mock_server.url,
        master_key="sk-litellm-master-super-secret-key-change-in-prod"
    )
    
    # Simulate a failure during kubectl apply
    mock_k8s = MagicMock(spec=K8sProvisioner)
    mock_k8s.render_manifests.return_value = [{"kind": "Namespace"}]
    mock_k8s.apply_manifests.side_effect = K8sProvisionerError("Simulated Ingress Admission Webhook Failure")
    mock_k8s.delete_namespace.return_value = True

    reg_file = tmp_path / "registry.json"
    tx_manager = ProvisioningTransactionManager(litellm_client, mock_k8s, registry_path=reg_file)

    success, msg, vkey = tx_manager.provision_tenant(spec)
    assert success is False
    assert "Simulated Ingress Admission Webhook Failure" in msg
    assert "Transaction rolled back" in msg

    # Verify rollback was called:
    mock_k8s.delete_namespace.assert_called_once_with(spec.metadata.tenant_id)
