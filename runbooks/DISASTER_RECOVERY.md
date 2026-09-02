# Operational Runbook: Disaster Recovery, Backup & Restore

## Purpose
This runbook details disaster recovery strategies, automated backup procedures, and restoration runbooks for both the central control plane (LiteLLM) and tenant workspaces (OpenWebUI).

---

## 1. System Recovery Objectives (RPO & RTO)

| Component | Recovery Point Objective (RPO) | Recovery Time Objective (RTO) | Strategy |
| :--- | :--- | :--- | :--- |
| **Central LiteLLM DB** | 1 hour | 15 minutes | Continuous WAL archiving + Hourly snapshot |
| **Tenant SQLite PVCs** | 24 hours | 30 minutes | Daily CSI VolumeSnapshot |
| **Cluster Manifests** | 0 minutes | 10 minutes | GitOps / Declarative YAML specifications |

---

## 2. Backup Procedures

### 2.1 Central Gateway PostgreSQL Backup
The LiteLLM database stores virtual keys, spend records, and audit logs.
```bash
# Automated cron backup script:
kubectl exec -it statefulset/litellm-postgres -n litellm -- \
  pg_dump -U litellm_admin -d litellm_db -Fc > /backups/litellm_db_$(date +%Y%m%d_%H%M%S).dump

# Encrypt and upload to cold storage (e.g. AWS S3 Glacier / GCP Coldline):
aws s3 cp /backups/litellm_db_*.dump s3://enterprise-saas-backups/gateway/ --sse aws:kms
```

### 2.2 Tenant SQLite Workspace Snapshots
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
2. **Restore LiteLLM Gateway**:
   ```bash
   helm install litellm charts/litellm-gateway -n litellm --create-namespace
   # Restore PostgreSQL dump
   cat litellm_db_latest.dump | kubectl exec -i statefulset/litellm-postgres -n litellm -- pg_restore -U litellm_admin -d litellm_db -c
   ```
3. **Re-provision All Active Tenants**:
   ```bash
   for spec in tenants/active/*.yaml; do
     python -m tenant_manager.cli provision "$spec"
   done
   ```
4. **Update Anycast DNS / Route53**:
   Switch `*.ai.saasdomain.com` DNS alias to new region Ingress Load Balancer IP.
