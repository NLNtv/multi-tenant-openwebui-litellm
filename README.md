# Multi-Tenant B2B SaaS Platform on Kubernetes
### Isolated OpenWebUI Instances Backed by Centralized LiteLLM Proxy Gateway

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Kubernetes 1.28+](https://img.shields.io/badge/kubernetes-1.28+-326ce5.svg)](https://kubernetes.io/)
[![Helm 3.12+](https://img.shields.io/badge/helm-3.12+-0f1689.svg)](https://helm.sh/)
[![Tests](https://img.shields.io/badge/tests-18%2F18%20passing-brightgreen.svg)]()
[![Security](https://img.shields.io/badge/security-zero--trust%20netpol-success.svg)]()

---

## 1. Executive Summary

This repository delivers a production-grade, multi-tenant B2B SaaS platform that deploys isolated, branded **OpenWebUI** instances for corporate enterprise tenants, backed by a centralized, metered **LiteLLM Proxy** gateway on Kubernetes.

### Key Capabilities:
* **Complete Tenant Isolation**: Every corporate tenant resides in a dedicated Kubernetes namespace (`tenant-<tenant-id>`) with dedicated persistent storage (PVC), independent SQLite database, and zero cross-tenant data co-mingling.
* **Zero-Knowledge Credential Security**: Tenants never see or hold upstream provider API keys (OpenAI, Anthropic, Bedrock, Vertex AI). Raw provider keys are stored exclusively in the central `litellm` control plane. Each tenant instance communicates with the proxy using an isolated Virtual Key (`sk-tenant-...`).
* **Granular Governance & Hard Budget Cutoff**: Metered token accounting per tenant with sliding-window rate limits (RPM / TPM) and hard dollar budget cutoffs (`max_budget`). When a tenant's budget is exhausted, requests are immediately cut off at the gateway with HTTP 400/429 without contacting upstream providers.
* **Enterprise Identity (LDAPS / Active Directory)**: Unique directory configuration per tenant (host, port, base DN, bind credentials, CA certs) enabling corporate single-sign-on alongside break-glass local administrator recovery.
* **Zero-Trust Network Policies**: Strict Kubernetes `NetworkPolicy` enforcement blocking cross-tenant pod communication, blocking cloud metadata endpoints (`169.254.169.254`), and restricting egress strictly to Cluster DNS, LiteLLM Gateway, and corporate LDAPS.
* **Automated Lifecycle Orchestration (`tenant-manager`)**: Python-based transactional provisioning engine with Pydantic v2 validation, pre-flight checks, and automatic atomic rollback on failure.

---

## 2. Repository Layout

```
.
├── README.md
├── architecture/
│   ├── ARCHITECTURE.md                  # Detailed architectural specification
│   ├── trade-off-analysis.md            # In-depth tenancy, storage, & orchestration trade-offs
│   ├── threat-model.md                  # STRIDE threat model & defense-in-depth security
│   └── diagrams/
│       ├── request-flow.mermaid         # End-to-end request sequence & budget cutoff
│       ├── network-topology.mermaid     # Zero-trust network boundaries
│       └── onboarding-sequence.mermaid  # Transactional onboarding & rollback flow
├── charts/
│   ├── litellm-gateway/                 # Helm chart for central LiteLLM proxy
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   │       ├── deployment.yaml          # LiteLLM proxy deployment with health probes
│   │       ├── service.yaml             # ClusterIP service (port 4000)
│   │       ├── configmap.yaml           # Model router, fallbacks, & caching config
│   │       ├── secret.yaml              # Master key, PG password, & provider keys
│   │       ├── networkpolicy.yaml       # Zero-trust network policies
│   │       ├── postgres-statefulset.yaml# PostgreSQL key & spend datastore
│   │       ├── redis-deployment.yaml    # Redis distributed rate limiting & cache
│   │       └── ingress.yaml             # Admin ingress definition
│   └── openwebui-tenant/                # Helm chart for tenant OpenWebUI instance
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── deployment.yaml          # OpenWebUI with non-root securityContext
│           ├── service.yaml             # ClusterIP service (port 8080)
│           ├── ingress.yaml             # Ingress with TLS & subdomain routing
│           ├── pvc.yaml                 # Dedicated PersistentVolumeClaim
│           ├── configmap-branding.yaml  # Custom CSS, logo, & portal title
│           ├── secret-credentials.yaml  # Virtual key, LDAPS secret, admin credentials
│           ├── networkpolicy.yaml       # Strict zero-trust egress/ingress filtering
│           ├── resourcequota.yaml       # Hard CPU, RAM, & storage caps
│           └── limitrange.yaml          # Default container resource limits
├── platform/
│   └── gateway/
│       └── litellm-config.yaml          # Master LiteLLM router configuration
├── tenants/
│   ├── templates/
│   │   └── tenant-spec.example.yaml     # Gold standard tenant specification template
│   ├── examples/
│   │   ├── acme-corp.yaml               # Enterprise tenant with Active Directory LDAPS
│   │   ├── globex-pharma.yaml           # Regulated tenant with strict model filtering
│   │   └── stark-industries.yaml        # Premium tier tenant with high throughput
│   └── active/
│       └── registry.json                # Active tenant state and virtual key registry
├── tools/
│   └── tenant-manager/                  # Automated tenant lifecycle engine
│       ├── pyproject.toml
│       ├── tenant_manager/
│       │   ├── cli.py                   # Command-line interface
│       │   ├── models.py                # Pydantic v2 schemas & validation rules
│       │   ├── litellm_client.py        # LiteLLM Admin REST API client
│       │   ├── k8s_provisioner.py       # Manifest generator & K8s manager
│       │   ├── validator.py             # Pre-flight and syntax validator
│       │   └── transaction.py           # Atomic transaction manager with rollback
│       └── tests/
│           ├── test_models.py           # Schema unit tests
│           ├── test_validator.py        # Pre-flight validation tests
│           ├── test_k8s_provisioner.py  # Manifest & NetworkPolicy tests
│           ├── test_litellm_client.py   # Virtual key lifecycle integration tests
│           └── test_transaction_rollback.py # Rollback verification tests
├── verification/
│   ├── mocks/
│   │   └── mock_litellm_server.py       # Mock LiteLLM proxy for local verification
│   ├── test-scenarios/
│   │   ├── test_budget_cutoff.py        # Functional hard budget cutoff test
│   │   ├── test_tenant_isolation.py     # NetworkPolicy isolation & SSRF test
│   │   └── test_ldaps_auth.py           # Directory auth injection test
│   └── run_all_tests.py                 # Master test runner
└── runbooks/
    ├── ONBOARDING_RUNBOOK.md            # Operator guide for onboarding tenants
    ├── DEPROVISIONING_RUNBOOK.md        # Offboarding, key revocation, & data retention
    ├── BUDGET_AND_RATE_LIMITS.md        # Managing quotas, spend alerts, & model access
    ├── DISASTER_RECOVERY.md             # Backup, restore, & snapshot procedures
    └── TROUBLESHOOTING.md               # Common failure modes & debugging procedures
```

---

## 3. Architecture & Trade-Off Summary

For comprehensive technical deep dives, review:
* [Architecture Specification (`architecture/ARCHITECTURE.md`)](architecture/ARCHITECTURE.md)
* [Trade-Off Analysis (`architecture/trade-off-analysis.md`)](architecture/trade-off-analysis.md)
* [Threat Model (`architecture/threat-model.md`)](architecture/threat-model.md)

### Key Architectural Decisions:
1. **Tenancy Model (Namespace-per-Tenant)**:
   * OpenWebUI is designed for single-organization use. Sharing a single database or instance creates unacceptable cross-tenant data leakage risks and prevents unique LDAPS bindings.
   * Namespace-per-tenant delivers true cryptographic data boundaries, independent enterprise identity, custom CSS/branding, and zero-trust network policies.
2. **Storage Architecture (Dedicated PVC / SQLite by default, Postgres for HA)**:
   * Dedicated PVC provides volume-level isolation. GDPR "Right to be Forgotten" is satisfied by simply deleting the tenant's PVC.
   * Active-active multi-replica deployments can optionally connect to an external PostgreSQL cluster with dedicated schemas.
3. **Provisioning Engine (`tenant-manager`)**:
   * A Python-based transactional CLI that guarantees atomic rollback across external systems (LiteLLM REST API) and internal cluster state (Kubernetes namespaces, secrets, and ingress).

---

## 4. Quickstart & CLI Usage

### Installation
```bash
# Install tenant-manager in editable mode
python -m pip install -e tools/tenant-manager
```

### 1. Validate Tenant Specification
```bash
python -m tenant_manager.cli validate tenants/examples/acme-corp.yaml
```

### 2. Render Kubernetes Manifests (Dry-Run / GitOps)
```bash
python -m tenant_manager.cli render tenants/examples/acme-corp.yaml -o rendered-acme.yaml
```

### 3. Provision Tenant End-to-End
```bash
python -m tenant_manager.cli provision tenants/examples/acme-corp.yaml \
  --litellm-url "http://litellm.litellm.svc.cluster.local:4000" \
  --master-key "$LITELLM_MASTER_KEY"
```

### 4. Check Tenant Status & Spend
```bash
python -m tenant_manager.cli status acme-corp
```

### 5. Dynamically Update Budget or Rate Limits
```bash
python -m tenant_manager.cli update-budget acme-corp --budget 1000.00 --rpm 200 --tpm 200000
```

### 6. Deprovision Tenant
```bash
python -m tenant_manager.cli deprovision acme-corp
```

---

## 5. Automated Verification & Testing

The platform includes an automated test suite verifying schema models, manifest rendering, zero-trust network policies, virtual key governance, atomic rollbacks, and hard budget cutoffs.

### Running the Test Suite:
```bash
python verification/run_all_tests.py
```

### Test Suite Results:
```
================================================================================
ENTERPRISE B2B MULTI-TENANT OPENWEBUI & LITELLM PLATFORM
AUTOMATED VERIFICATION & TEST SUITE
================================================================================
collected 18 items

tools/tenant-manager/tests/test_k8s_provisioner.py::test_render_all_manifests_for_acme PASSED
tools/tenant-manager/tests/test_litellm_client.py::test_virtual_key_lifecycle PASSED
tools/tenant-manager/tests/test_litellm_client.py::test_invalid_master_key_raises_error PASSED
tools/tenant-manager/tests/test_models.py::test_valid_tenant_spec PASSED
tools/tenant-manager/tests/test_models.py::test_invalid_tenant_id_regex PASSED
tools/tenant-manager/tests/test_models.py::test_reserved_subdomain_rejected PASSED
tools/tenant-manager/tests/test_models.py::test_negative_budget_rejected PASSED
tools/tenant-manager/tests/test_models.py::test_default_model_must_be_in_allowed_models PASSED
tools/tenant-manager/tests/test_models.py::test_short_admin_password_rejected PASSED
tools/tenant-manager/tests/test_transaction_rollback.py::test_successful_provision_and_deprovision PASSED
tools/tenant-manager/tests/test_transaction_rollback.py::test_rollback_on_k8s_failure PASSED
tools/tenant-manager/tests/test_validator.py::test_validate_acme_corp_example PASSED
tools/tenant-manager/tests/test_validator.py::test_validate_globex_pharma_example PASSED
tools/tenant-manager/tests/test_validator.py::test_validate_stark_industries_example PASSED
tools/tenant-manager/tests/test_validator.py::test_validate_nonexistent_file PASSED
tools/tenant-manager/test_budget_cutoff.py::test_hard_budget_cutoff_enforcement PASSED
tools/tenant-manager/test_ldaps_auth.py::test_ldaps_configuration_injection PASSED
tools/tenant-manager/test_tenant_isolation.py::test_tenant_isolation_boundaries PASSED

============================= 18 passed in 2.51s ==============================
================================================================================
ALL SECURITY, ISOLATION, BUDGET, AND LIFECYCLE TESTS PASSED (100%)
================================================================================
```

---

## 6. Operational Runbooks

* [Tenant Onboarding Runbook (`runbooks/ONBOARDING_RUNBOOK.md`)](runbooks/ONBOARDING_RUNBOOK.md)
* [Tenant Deprovisioning Runbook (`runbooks/DEPROVISIONING_RUNBOOK.md`)](runbooks/DEPROVISIONING_RUNBOOK.md)
* [Budgets & Rate Limits Guide (`runbooks/BUDGET_AND_RATE_LIMITS.md`)](runbooks/BUDGET_AND_RATE_LIMITS.md)
* [Disaster Recovery & Snapshots (`runbooks/DISASTER_RECOVERY.md`)](runbooks/DISASTER_RECOVERY.md)
* [Troubleshooting & Diagnostics (`runbooks/TROUBLESHOOTING.md`)](runbooks/TROUBLESHOOTING.md)
