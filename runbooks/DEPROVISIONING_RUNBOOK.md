# Operational Runbook: Tenant Deprovisioning & Offboarding

## Purpose
This runbook establishes standard operating procedures for gracefully deprovisioning a corporate tenant, tearing down the dedicated tenant namespace (OpenWebUI + local LiteLLM proxy), enforcing GDPR / SOC2 data retention or disposal, and safely reclaiming Kubernetes cluster resources.

---

## 1. Pre-Deprovisioning Approvals

Before initiating tenant termination, verify:
1. Written offboarding ticket signed by Account Management and Tenant Representative.
2. Compliance retention mode:
   * **Immediate Hard Purge (GDPR Article 17 "Right to be Forgotten")**: PVC deleted immediately without archiving.
   * **Regulatory Archive (SOC2 / HIPAA)**: Snapshot PVC taken and stored in cold immutable storage for 90 days.

---

## 2. Archival Snapshot (Optional / Regulated Tenants)

If compliance requires a pre-deletion backup:
```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: acme-corp-final-snapshot
  namespace: tenant-acme-corp
spec:
  volumeSnapshotClassName: csi-aws-vsc
  source:
    persistentVolumeClaimName: openwebui-data-pvc
```
Apply the snapshot and verify readiness:
```bash
kubectl apply -f final-snapshot.yaml
kubectl get volumesnapshot -n tenant-acme-corp acme-corp-final-snapshot
```

---

## 3. Deprovisioning Execution

Run the `tenant-manager deprovision` command:

```bash
python -m tenant_manager.cli deprovision acme-corp
```

### What Happens Automatically:
1. **Total Namespace Teardown**:
   * The namespace `tenant-acme-corp` is deleted.
   * Kubernetes terminates all OpenWebUI pods and dedicated LiteLLM pods.
   * The localized secrets (including any BYOK credentials) are permanently destroyed.
   * Ingress routing rules are purged from the ingress controller.
   * The PVC is released and reclaimed according to storage class policy.
2. **Registry Update**:
   * The tenant record is removed from `tenants/active/registry.json`.

---

## 4. Post-Deprovisioning Audit & Verification

1. **Verify Namespace Teardown**:
   ```bash
   kubectl get namespaces tenant-acme-corp
   # Must return NotFound
   ```

2. **Verify Subdomain DNS & Routing**:
   ```bash
   curl -Iv https://acme.ai.saasdomain.com
   # Must return 404 Not Found from Ingress Controller
   ```

3. **Sign Off Offboarding Ticket**:
   Record timestamp, deprovisioning log, and snapshot ID in the compliance audit ticket.
