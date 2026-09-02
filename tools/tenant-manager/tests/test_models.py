"""
Unit tests for TenantSpecification Pydantic v2 models.
"""

import pytest
from pydantic import ValidationError
from tenant_manager.models import (
    TenantSpecification,
    MetadataConfig,
    RoutingConfig,
    GovernanceConfig,
    BrandingConfig,
    IdentityConfig,
    AdminFallbackConfig,
    ComputeConfig,
)


def get_valid_tenant_data():
    return {
        "metadata": {
            "tenantId": "acme-corp",
            "tenantName": "Acme Corporation",
            "contactEmail": "admin@acme.com",
            "environment": "production"
        },
        "routing": {
            "subdomain": "acme",
            "baseDomain": "ai.saasdomain.com",
            "tlsClusterIssuer": "letsencrypt-prod"
        },
        "governance": {
            "maxBudgetUsd": 500.0,
            "budgetDuration": "30d",
            "rpmLimit": 120,
            "tpmLimit": 120000,
            "allowedModels": ["gpt-4o", "claude-3-5-sonnet"]
        },
        "branding": {
            "portalTitle": "Acme Corp AI Workspace",
            "defaultModel": "gpt-4o",
            "customLogoUrl": "https://acme.com/logo.png"
        },
        "identity": {
            "ldapEnabled": True,
            "serverHost": "ldaps.corp.acme.com",
            "serverPort": 636,
            "useTls": True,
            "validateCert": True,
            "searchBase": "OU=Users,DC=acme,DC=com",
            "bindDn": "CN=svc,DC=acme,DC=com",
            "bindPassword": "SecureLdapPassword123!",
            "usernameAttribute": "sAMAccountName"
        },
        "adminFallback": {
            "enabled": True,
            "email": "breakglass@acme.com",
            "password": "EmergencySecretPassword2026!"
        },
        "compute": {
            "replicas": 1,
            "storageSize": "20Gi"
        }
    }


def test_valid_tenant_spec():
    data = get_valid_tenant_data()
    spec = TenantSpecification.model_validate(data)
    assert spec.metadata.tenant_id == "acme-corp"
    assert spec.routing.fqdn == "acme.ai.saasdomain.com"
    assert spec.governance.max_budget_usd == 500.0
    assert "gpt-4o" in spec.governance.allowed_models


def test_invalid_tenant_id_regex():
    data = get_valid_tenant_data()
    data["metadata"]["tenantId"] = "INVALID_UPPERCASE_ID"
    with pytest.raises(ValidationError) as exc:
        TenantSpecification.model_validate(data)
    assert "tenantId" in str(exc.value)


def test_reserved_subdomain_rejected():
    data = get_valid_tenant_data()
    data["routing"]["subdomain"] = "admin"
    with pytest.raises(ValidationError) as exc:
        TenantSpecification.model_validate(data)
    assert "reserved platform keyword" in str(exc.value)


def test_negative_budget_rejected():
    data = get_valid_tenant_data()
    data["governance"]["maxBudgetUsd"] = -50.0
    with pytest.raises(ValidationError) as exc:
        TenantSpecification.model_validate(data)
    assert "maxBudgetUsd" in str(exc.value)


def test_default_model_must_be_in_allowed_models():
    data = get_valid_tenant_data()
    data["branding"]["defaultModel"] = "unapproved-secret-model"
    with pytest.raises(ValidationError) as exc:
        TenantSpecification.model_validate(data)
    assert "must be included in governance.allowedModels" in str(exc.value)


def test_short_admin_password_rejected():
    data = get_valid_tenant_data()
    data["adminFallback"]["password"] = "short"
    with pytest.raises(ValidationError) as exc:
        TenantSpecification.model_validate(data)
    assert "String should have at least 12 characters" in str(exc.value)
