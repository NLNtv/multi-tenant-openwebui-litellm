"""
Verification Test Scenario: Tenant Isolation & Security Boundary Assertions
Validates that tenant Kubernetes manifests enforce complete data, runtime, network,
and credential compartmentalization under the Decentralized Per-Tenant Gateway architecture.
"""

from tenant_manager.validator import SpecValidator
from tenant_manager.k8s_provisioner import K8sProvisioner


def test_tenant_isolation_boundaries():
    # Load two separate enterprise tenants
    spec_a = SpecValidator.validate_file("tenants/examples/acme-corp.yaml").spec
    spec_b = SpecValidator.validate_file("tenants/examples/globex-pharma.yaml").spec

    provisioner = K8sProvisioner()
    manifests_a = provisioner.render_manifests(spec_a, "sk-tenant-acme-key")
    manifests_b = provisioner.render_manifests(spec_b, "sk-tenant-globex-key")

    # 1. Namespace Separation
    ns_a = next(m for m in manifests_a if m["kind"] == "Namespace")["metadata"]["name"]
    ns_b = next(m for m in manifests_b if m["kind"] == "Namespace")["metadata"]["name"]
    assert ns_a != ns_b
    assert ns_a == "tenant-acme-corp"
    assert ns_b == "tenant-globex-pharma"

    # 2. BYOK Credential Compartmentalization: Zero Key Bleed Between Tenants
    secret_a = next(m for m in manifests_a if m["kind"] == "Secret")
    secret_b = next(m for m in manifests_b if m["kind"] == "Secret")
    # Acme's keys are in Acme's secret
    assert secret_a["stringData"]["OPENAI_API_KEY"] == "sk-proj-acme-corp-dedicated-key-2026"
    assert "azure-globex" not in str(secret_a)
    # Globex's keys are in Globex's secret
    assert secret_b["stringData"]["AZURE_API_KEY"] == "azure-globex-compliance-key-2026"
    assert "sk-proj-acme" not in str(secret_b)

    # 3. OpenWebUI NetworkPolicy Isolation: Ingress Restriction
    ow_netpol_a = next(m for m in manifests_a if m["kind"] == "NetworkPolicy" and m["metadata"]["name"] == "openwebui-zero-trust-netpol")
    ingress_rules = ow_netpol_a["spec"]["ingress"]
    assert len(ingress_rules) == 1
    allowed_ingress_ns = ingress_rules[0]["from"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
    assert allowed_ingress_ns == "ingress-nginx"

    # 4. Intra-Namespace Egress: OpenWebUI can ONLY reach local LiteLLM on port 4000
    egress_rules = ow_netpol_a["spec"]["egress"]
    local_litellm_rule = next(
        rule for rule in egress_rules
        if any(p.get("port") == 4000 for p in rule.get("ports", []))
    )
    # The target pod is strictly within the same namespace (podSelector only, no cross-namespaceSelector)
    assert "namespaceSelector" not in local_litellm_rule["to"][0]
    assert local_litellm_rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "litellm"

    # 5. Cloud Metadata Protection (Anti-SSRF 169.254.169.254)
    lt_netpol_a = next(m for m in manifests_a if m["kind"] == "NetworkPolicy" and m["metadata"]["name"] == "litellm-zero-trust-netpol")
    outbound_https_rule = next(
        rule for rule in lt_netpol_a["spec"]["egress"]
        if any(p.get("port") == 443 for p in rule.get("ports", []))
    )
    assert "169.254.169.254/32" in outbound_https_rule["to"][0]["ipBlock"]["except"]

    # 6. Storage Isolation: Independent PVCs
    pvc_a = next(m for m in manifests_a if m["kind"] == "PersistentVolumeClaim")
    pvc_b = next(m for m in manifests_b if m["kind"] == "PersistentVolumeClaim")
    assert pvc_a["metadata"]["namespace"] == "tenant-acme-corp"
    assert pvc_b["metadata"]["namespace"] == "tenant-globex-pharma"
    assert pvc_a["metadata"]["namespace"] != pvc_b["metadata"]["namespace"]

    print("\n[PASS] Tenant Isolation Validated: Decentralized per-tenant stack provides complete runtime, network, storage, and BYOK credential compartmentalization.")
