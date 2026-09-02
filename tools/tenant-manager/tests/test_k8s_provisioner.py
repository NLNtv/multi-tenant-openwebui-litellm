"""
Unit tests for K8sProvisioner manifest generation and security policy verification.
"""

from pathlib import Path
from tenant_manager.validator import SpecValidator
from tenant_manager.k8s_provisioner import K8sProvisioner


def test_render_all_manifests_for_acme():
    result = SpecValidator.validate_file("tenants/examples/acme-corp.yaml")
    assert result.is_valid is True

    provisioner = K8sProvisioner()
    virtual_key = "sk-tenant-acme-mock-key-12345"
    manifests = provisioner.render_manifests(result.spec, virtual_key)

    kinds = [m["kind"] for m in manifests]
    assert "Namespace" in kinds
    assert "ResourceQuota" in kinds
    assert "LimitRange" in kinds
    assert "PersistentVolumeClaim" in kinds
    assert kinds.count("ConfigMap") == 2
    assert "Secret" in kinds
    assert kinds.count("Deployment") == 2
    assert kinds.count("Service") == 2
    assert kinds.count("NetworkPolicy") == 2
    assert "Ingress" in kinds
    assert len(manifests) == 14

    # 1. Inspect Namespace
    ns_m = next(m for m in manifests if m["kind"] == "Namespace")
    assert ns_m["metadata"]["name"] == "tenant-acme-corp"
    assert ns_m["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"

    # 2. Inspect Secret
    sec_m = next(m for m in manifests if m["kind"] == "Secret")
    assert sec_m["stringData"]["LITELLM_MASTER_KEY"] == virtual_key
    assert "LDAP_APP_PASSWORD" in sec_m["stringData"]
    assert "OPENAI_API_KEY" in sec_m["stringData"]

    # 3. Inspect OpenWebUI NetworkPolicy (Zero-Trust)
    ow_np = next(m for m in manifests if m["kind"] == "NetworkPolicy" and m["metadata"]["name"] == "openwebui-zero-trust-netpol")
    spec = ow_np["spec"]
    assert "Ingress" in spec["policyTypes"]
    assert "Egress" in spec["policyTypes"]
    
    # Ingress allows ingress-nginx
    assert any("ingress-nginx" in str(rule) for rule in spec["ingress"])

    # Egress allows CoreDNS (port 53), local LiteLLM (port 4000), and LDAPS (port 636)
    egress_ports = [
        port_dict["port"]
        for rule in spec["egress"]
        for port_dict in rule.get("ports", [])
    ]
    assert 53 in egress_ports
    assert 4000 in egress_ports
    assert 636 in egress_ports

    # 4. Inspect LiteLLM NetworkPolicy (Zero-Trust)
    lt_np = next(m for m in manifests if m["kind"] == "NetworkPolicy" and m["metadata"]["name"] == "litellm-zero-trust-netpol")
    lt_spec = lt_np["spec"]
    # Ingress only from local OpenWebUI
    assert lt_spec["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "openwebui"
    # Egress to DNS and outbound HTTPS (443) with metadata service blocked
    outbound_https_rule = next(
        rule for rule in lt_spec["egress"]
        if any(p.get("port") == 443 for p in rule.get("ports", []))
    )
    assert "169.254.169.254/32" in outbound_https_rule["to"][0]["ipBlock"]["except"]

    # 5. Inspect Deployments (Both OpenWebUI and LiteLLM)
    dep_names = [m["metadata"]["name"] for m in manifests if m["kind"] == "Deployment"]
    assert "openwebui" in dep_names
    assert "litellm" in dep_names

    # 6. Inspect Ingress
    ing_m = next(m for m in manifests if m["kind"] == "Ingress")
    assert ing_m["spec"]["rules"][0]["host"] == "acme.ai.saasdomain.com"
    assert ing_m["spec"]["tls"][0]["hosts"][0] == "acme.ai.saasdomain.com"
