"""
Verification Test Scenario: Enterprise LDAPS Configuration & Auth Integration
Validates that tenant directory authentication settings and fallback admin pathways
are properly generated and injected into Kubernetes secrets and pod environments.
"""

from tenant_manager.validator import SpecValidator
from tenant_manager.k8s_provisioner import K8sProvisioner


def test_ldaps_configuration_injection():
    spec = SpecValidator.validate_file("tenants/examples/acme-corp.yaml").spec
    provisioner = K8sProvisioner()
    manifests = provisioner.render_manifests(spec, "sk-tenant-acme-mock")

    # 1. Verify Secret contains LDAPS Bind Password and Admin Fallback Credentials
    secret = next(m for m in manifests if m["kind"] == "Secret")
    assert "LDAP_APP_PASSWORD" in secret["stringData"]
    assert secret["stringData"]["LDAP_APP_PASSWORD"] == "AcmeSecureBindPassword2026!"
    assert "ADMIN_EMAIL" in secret["stringData"]
    assert secret["stringData"]["ADMIN_EMAIL"] == "breakglass-admin@acme.com"
    assert "ADMIN_PASSWORD" in secret["stringData"]

    # 2. Verify Deployment Environment Maps LDAPS Correctly
    deployment = next(m for m in manifests if m["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_dict = {}
    for env in container["env"]:
        if "value" in env:
            env_dict[env["name"]] = env["value"]
        elif "valueFrom" in env:
            env_dict[env["name"]] = env["valueFrom"]

    # Assert Active Directory / LDAPS configuration
    assert env_dict.get("ENABLE_LDAP") == "true"
    assert env_dict.get("LDAP_SERVER_HOST") == "ldaps.corp.acme.com"
    assert env_dict.get("LDAP_SERVER_PORT") == "636"
    assert env_dict.get("LDAP_USE_TLS") == "true"
    assert env_dict.get("LDAP_ATTRIBUTE_FOR_USERNAME") == "sAMAccountName"
    assert "OU=Employees,OU=Users,DC=corp,DC=acme,DC=com" in env_dict.get("LDAP_SEARCH_BASE")

    # Assert password is securely referenced from Secret
    password_ref = env_dict.get("LDAP_APP_PASSWORD")
    assert "secretKeyRef" in password_ref
    assert password_ref["secretKeyRef"]["name"] == "openwebui-credentials"
    assert password_ref["secretKeyRef"]["key"] == "LDAP_APP_PASSWORD"

    print("\n[PASS] LDAPS Configuration & Admin Fallback Path Validated Successfully.")
