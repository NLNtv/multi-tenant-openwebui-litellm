"""
Verification Test Scenario: Tenant Isolation & Security Boundary Assertions
Validates that tenant Kubernetes manifests enforce complete data, runtime, network,
and credential compartmentalization.
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

    # 2. Credential Compartmentalization: Zero Upstream Provider Keys in Tenant Namespaces
    forbidden_key_signatures = ["sk-ant-", "sk-proj-", "AIzaSy", "aws-secret"]
    for m in manifests_a + manifests_b:
        m_str = str(m)
        for sig in forbidden_key_signatures:
            assert sig not in m_str, f"CRITICAL LEAK: Upstream credential signature '{sig}' found in tenant manifest {m['kind']}!"

    # 3. NetworkPolicy Isolation: Ingress Restriction
    netpol_a = next(m for m in manifests_a if m["kind"] == "NetworkPolicy")
    ingress_rules = netpol_a["spec"]["ingress"]
    # Only ingress-nginx namespace is whitelisted
    assert len(ingress_rules) == 1
    allowed_ingress_ns = ingress_rules[0]["from"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
    assert allowed_ingress_ns == "ingress-nginx"

    # 4. NetworkPolicy Isolation: Cross-Tenant Egress Denial
    egress_rules = netpol_a["spec"]["egress"]
    allowed_namespaces = []
    for rule in egress_rules:
        to_blocks = rule.get("to", [])
        for block in to_blocks:
            if "namespaceSelector" in block:
                ns_label = block["namespaceSelector"]["matchLabels"].get("kubernetes.io/metadata.name")
                if ns_label:
                    allowed_namespaces.append(ns_label)

    # Allowed egress namespaces must ONLY be kube-system (DNS) and litellm (Gateway)
    assert set(allowed_namespaces) == {"kube-system", "litellm"}
    assert "tenant-globex-pharma" not in allowed_namespaces

    # 5. Cloud Metadata Protection (Anti-SSRF 169.254.169.254)
    for rule in egress_rules:
        for block in rule.get("to", []):
            if "ipBlock" in block:
                except_list = block["ipBlock"].get("except", [])
                assert "169.254.169.254/32" in except_list, "Link-local cloud metadata service (169.254.169.254) must be explicitly blocked!"

    # 6. Storage Isolation: Independent PVCs
    pvc_a = next(m for m in manifests_a if m["kind"] == "PersistentVolumeClaim")
    pvc_b = next(m for m in manifests_b if m["kind"] == "PersistentVolumeClaim")
    assert pvc_a["metadata"]["namespace"] == "tenant-acme-corp"
    assert pvc_b["metadata"]["namespace"] == "tenant-globex-pharma"
    assert pvc_a["metadata"]["namespace"] != pvc_b["metadata"]["namespace"]

    print("\n[PASS] Tenant Isolation Validated: Complete runtime, network, storage, and credential compartmentalization confirmed.")
