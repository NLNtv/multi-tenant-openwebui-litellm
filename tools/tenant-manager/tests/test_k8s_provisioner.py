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

    # Assert all 10 core manifests are rendered
    kinds = [m["kind"] for m in manifests]
    assert "Namespace" in kinds
    assert "ResourceQuota" in kinds
    assert "LimitRange" in kinds
    assert "PersistentVolumeClaim" in kinds
    assert "ConfigMap" in kinds
    assert "Secret" in kinds
    assert "NetworkPolicy" in kinds
    assert "Deployment" in kinds
    assert "Service" in kinds
    assert "Ingress" in kinds
    assert len(manifests) == 10

    # 1. Inspect Namespace
    ns_m = next(m for m in manifests if m["kind"] == "Namespace")
    assert ns_m["metadata"]["name"] == "tenant-acme-corp"
    assert ns_m["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"

    # 2. Inspect Secret
    sec_m = next(m for m in manifests if m["kind"] == "Secret")
    assert sec_m["stringData"]["OPENAI_API_KEY"] == virtual_key
    assert "LDAP_APP_PASSWORD" in sec_m["stringData"]

    # 3. Inspect NetworkPolicy (Zero-Trust)
    np_m = next(m for m in manifests if m["kind"] == "NetworkPolicy")
    spec = np_m["spec"]
    assert "Ingress" in spec["policyTypes"]
    assert "Egress" in spec["policyTypes"]
    
    # Ingress allows ingress-nginx
    assert any(
        "ingress-nginx" in str(rule) for rule in spec["ingress"]
    )

    # Egress allows CoreDNS (port 53) and LiteLLM (port 4000)
    egress_ports = [
        port_dict["port"]
        for rule in spec["egress"]
        for port_dict in rule.get("ports", [])
    ]
    assert 53 in egress_ports
    assert 4000 in egress_ports
    assert 636 in egress_ports  # LDAPS port

    # Metadata service 169.254.169.254 is explicitly excepted in LDAPS rule
    ldaps_egress = next(
        rule for rule in spec["egress"]
        if any(p.get("port") == 636 for p in rule.get("ports", []))
    )
    assert "169.254.169.254/32" in ldaps_egress["to"][0]["ipBlock"]["except"]

    # 4. Inspect Deployment
    dep_m = next(m for m in manifests if m["kind"] == "Deployment")
    pod_spec = dep_m["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["runAsUser"] == 1000

    container = pod_spec["containers"][0]
    assert container["ports"][0]["containerPort"] == 8080
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health"

    # 5. Inspect Ingress
    ing_m = next(m for m in manifests if m["kind"] == "Ingress")
    assert ing_m["spec"]["rules"][0]["host"] == "acme.ai.saasdomain.com"
    assert ing_m["spec"]["tls"][0]["hosts"][0] == "acme.ai.saasdomain.com"
