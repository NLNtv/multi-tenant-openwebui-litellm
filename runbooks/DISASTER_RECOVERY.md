# Operational Runbook: Disaster Recovery, Backup & Restore

## Purpose
This runbook details disaster recovery strategies, automated backup procedures, and restoration runbooks for self-contained tenant workspaces.

---

## 1. System Recovery Objectives (RPO & RTO)

| Component | Recovery Point Objective (RPO) | Recovery Time Objective (RTO) | Strategy |
| :--- | :--- | :--- | :--- |
| **Tenant SQLite PVCs** | 24 hours | 15 minutes | Daily CSI VolumeSnapshot |
| **Cluster Manifests** | 0 minutes | 5 minutes | GitOps / Declarative YAML specifications |
| **LLM Proxy Configurations**| 0 minutes | 5 minutes | Rendered from declarative `tenant-spec.yaml` |

---

## 2. Backup Procedures

### 2.1 Tenant SQLite Workspace Snapshots
Each tenant has an isolated PersistentVolumeClaim containing `/app/backend/data/webui.db` and uploaded documents.

Automated daily CSI VolumeSnapshot:
```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: acme-corp-daily-snapshot
  namespace: tenant-acme-corp
spec:
  volumeSnapshotClassName: csi-aws-vsc
  source:
    persistentVolumeClaimName: openwebui-data-pvc
```

---

## 3. Restoration Procedures

### 3.1 Restoring a Single Tenant from Snapshot
In the event of accidental data corruption or user file deletion inside a tenant instance:

1. **Scale down tenant deployment**:
   ```bash
   kubectl scale deployment/openwebui -n tenant-acme-corp --replicas=0
   ```

2. **Restore PVC from VolumeSnapshot**:
   ```yaml
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: openwebui-data-pvc-restored
     namespace: tenant-acme-corp
   spec:
     storageClassName: gp3
     dataSource:
       name: acme-corp-daily-snapshot
       kind: VolumeSnapshot
       apiGroup: snapshot.storage.k8s.io
     accessModes:
       - ReadWriteOnce
     resources:
       requests:
         storage: 20Gi
   ```

3. **Switch PVC and scale up**:
   Update `openwebui-data-pvc` reference in deployment or patch claimName, then:
   ```bash
   kubectl scale deployment/openwebui -n tenant-acme-corp --replicas=1
   ```

### 3.2 Total Regional Cluster Failover
If the primary Kubernetes cluster or region suffers a catastrophic outage:

1. **Provision New Kubernetes Cluster** in secondary region (e.g., `us-west-2`).
2. **Re-provision All Active Tenants Directly** (Zero central control plane database needed!):
   ```bash
   for spec in tenants/active/*.yaml; do
     python -m tenant_manager.cli provision "$spec"
   done
   ```
3. **Restore Data Volumes**:
   Re-attach replicated cross-region snapshots to tenant PVCs.
4. **Update Anycast DNS / Route53**:
   Switch `*.ai.saasdomain.com` DNS alias to new region Ingress Load Balancer IP.
