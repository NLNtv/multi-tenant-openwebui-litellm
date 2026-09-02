# Multi-Tenant B2B SaaS Platform on Kubernetes
### Isolated OpenWebUI Instances Backed by Dedicated Per-Tenant LiteLLM Proxy Gateways

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Kubernetes 1.28+](https://img.shields.io/badge/kubernetes-1.28+-326ce5.svg)](https://kubernetes.io/)
[![Helm 3.12+](https://img.shields.io/badge/helm-3.12+-0f1689.svg)](https://helm.sh/)
[![Tests](https://img.shields.io/badge/tests-19%2F19%20passing-brightgreen.svg)]()
[![Security](https://img.shields.io/badge/security-zero--trust%20netpol-success.svg)]()
[![BYOK](https://img.shields.io/badge/feature-BYOK%20native-informational.svg)]()

---

## 1. Executive Summary

This repository delivers a production-grade, multi-tenant B2B SaaS platform that deploys isolated, branded **OpenWebUI** instances for corporate enterprise tenants, backed by **dedicated per-tenant LiteLLM Proxy** gateways on Kubernetes.

Rather than funneling all corporate traffic through a shared centralized proxy gateway, this platform adopts a **Decentralized Per-Tenant Gateway** topology. Each tenant organization resides in an isolated Kubernetes namespace (`tenant-<tenant-id>`) containing both OpenWebUI and its own localized LiteLLM proxy.

```
                       [ Corporate Client Browser ]
                                    │
                                    ▼
                 [ Ingress Controller (TLS Termination) ]
                        acme.ai.saasdomain.com
                                    │
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │ Namespace: tenant-acme (Isolated Perimeter)            │
       │  ┌──────────────────────────────────────────────────┐  │
       │  │ OpenWebUI Pod (UID 1000, Non-root)               │  │
       │  │  - LDAPS Auth -> Corporate Active Directory:636  │  │
       │  │  - Dedicated PVC -> Isolated webui.db storage    │  │
       │  └──────────────────────────┬───────────────────────┘  │
       │                             │ Intra-Namespace HTTP     │
       │                             │ (:4000)                  │
       │                             ▼                          │
       │  ┌──────────────────────────────────────────────────┐  │
       │  │ Dedicated LiteLLM Proxy (tenant-acme:4000)       │  │
       │  │  - Local Rate Limits & Hard Dollar Budgets       │  │
       │  │  - Model Whitelisting & Fallback Router          │  │
       │  │  - Acme BYOK / Scoped Provider Keys              │  │
       │  └──────────────────────────┬───────────────────────┘  │
       └─────────────────────────────┼──────────────────────────┘
                                     │ Outbound HTTPS 443
                                     ▼
              [ Upstream LLMs (OpenAI / Anthropic / Azure / Bedrock) ]
```

### Key Capabilities:
* **Zero Blast Radius**: An outage, memory spike, or misconfiguration in Tenant A has zero impact on Tenant B.
* **Native Bring Your Own Key (BYOK)**: Upstream provider credentials (OpenAI, Anthropic, Azure OpenAI, AWS Bedrock) live strictly in the tenant's namespace secrets and never touch a shared control plane.
* **Complete Tenant Isolation**: Every corporate tenant resides in a dedicated Kubernetes namespace (`tenant-<tenant-id>`) with dedicated persistent storage (PVC), independent SQLite database, and zero cross-tenant data co-mingling.
* **Zero Prompt Cache Bleed**: Caches and logs are physically separated per namespace; cross-tenant prompt cache hit risks are completely eliminated.
* **Granular Governance & Hard Budget Cutoffs**: Dedicated LiteLLM proxies enforce sliding-window rate limits (RPM / TPM) and hard dollar budget cutoffs (`max_budget`). When a tenant's budget is exhausted, requests are immediately cut off locally with HTTP 400/429 without contacting upstream cloud providers.
* **Enterprise Identity (LDAPS / Active Directory)**: Unique directory configuration per tenant (host, port, base DN, bind credentials, CA certs) enabling corporate single-sign-on alongside break-glass local administrator recovery.
* **Zero-Trust Network Policies**: Strict Kubernetes `NetworkPolicy` enforcement: OpenWebUI only communicates with its local LiteLLM proxy and LDAPS server; LiteLLM only egresses to DNS and outbound HTTPS (443); link-local cloud metadata (`169.254.169.254`) is explicitly blocked.
* **Automated Lifecycle Orchestration (`tenant-manager`)**: Python-based transactional provisioning engine with Pydantic v2 validation, pre-flight checks, and automatic atomic rollback on failure.

---

## 2. Repository Layout

```
.
├── README.md
├── architecture/
│   ├── ARCHITECTURE.md                  # Detailed architectural specification
│   ├── trade-off-analysis.md            # Tenancy, gateway, & storage trade-offs
│   ├── threat-model.md                  # STRIDE threat model & defense-in-depth security
│   └── diagrams/
│       ├── request-flow.mermaid         # Localized request sequence & budget cutoff
│       ├── network-topology.mermaid     # Zero-trust network boundaries
│       └── onboarding-sequence.mermaid  # Transactional onboarding & rollback flow
├── charts/
│   ├── openwebui-tenant/                # Helm chart for complete tenant workspace
│   │   ├── Chart.yaml                   # Chart definition (v2.0.0)
│   │   ├── values.yaml                  # Default values with LiteLLM & BYOK config
│   │   └── templates/
│   │       ├── deployment.yaml          # OpenWebUI with non-root securityContext
│   │       ├── service.yaml             # OpenWebUI ClusterIP service (port 8080)
│   │       ├── litellm-deployment.yaml  # Dedicated LiteLLM proxy deployment
│   │       ├── litellm-service.yaml     # LiteLLM ClusterIP service (port 4000)
│   │       ├── litellm-configmap.yaml   # Local model list & router settings
│   │       ├── ingress.yaml             # Ingress with TLS & subdomain routing
│   │       ├── pvc.yaml                 # Dedicated PersistentVolumeClaim
│   │       ├── configmap-branding.yaml  # Custom CSS, logo, & portal title
│   │       ├── secret-credentials.yaml  # BYOK keys, LDAPS secret, admin credentials
│   │       ├── networkpolicy.yaml       # Strict zero-trust egress/ingress filtering
│   │       ├── resourcequota.yaml       # Hard CPU, RAM, & storage caps
│   │       └── limitrange.yaml          # Default container resource limits
│   └── litellm-gateway/                 # Optional standalone central gateway chart
├── tenants/
│   ├── templates/
│   │   └── tenant-spec.example.yaml     # Gold standard tenant specification template
│   ├── examples/
│   │   ├── acme-corp.yaml               # Enterprise tenant with Active Directory & BYOK
│   │   ├── globex-pharma.yaml           # Regulated tenant with Azure OpenAI BYOK
│   │   └── stark-industries.yaml        # Premium tier tenant with AWS Bedrock BYOK
│   └── active/
│       └── registry.json                # Active tenant state registry
├── tools/
│   └── tenant-manager/                  # Automated tenant lifecycle engine
│       ├── pyproject.toml
│       ├── tenant_manager/
│       │   ├── cli.py                   # Command-line interface
│       │   ├── models.py                # Pydantic v2 schemas & BYOK models
│       │   ├── litellm_client.py        # LiteLLM REST API client
│       │   ├── k8s_provisioner.py       # Manifest generator & K8s manager (14 resources)
│       │   ├── validator.py             # Pre-flight and syntax validator
│       │   └── transaction.py           # Atomic transaction manager with rollback
│       └── tests/
│           ├── test_models.py           # Schema & BYOK parsing unit tests
│           ├── test_validator.py        # Pre-flight validation tests
│           ├── test_k8s_provisioner.py  # 14-resource manifest & NetworkPolicy tests
│           ├── test_litellm_client.py   # Gateway client unit tests
│           └── test_transaction_rollback.py # Rollback verification tests
├── verification/
│   ├── mocks/
│   │   └── mock_litellm_server.py       # Mock LiteLLM proxy for local verification
│   ├── test-scenarios/
│   │   ├── test_budget_cutoff.py        # Functional hard budget cutoff test
│   │   ├── test_tenant_isolation.py     # NetworkPolicy isolation, BYOK, & SSRF test
│   │   └── test_ldaps_auth.py           # Directory auth injection test
│   └── run_all_tests.py                 # Master test runner
└── runbooks/
    ├── ONBOARDING_RUNBOOK.md            # SRE onboarding guide
    ├── DEPROVISIONING_RUNBOOK.md        # Offboarding & GDPR data disposal
    ├── BUDGET_AND_RATE_LIMITS.md        # Quotas, alerts, & model management
    ├── DISASTER_RECOVERY.md             # Backups, snapshots, & regional failover
    └── TROUBLESHOOTING.md               # Diagnostics & runbook tree
```

---

## 3. Quickstart Guide

### 3.1 Prerequisites
* Kubernetes 1.28+ cluster
* Python 3.10+
* Ingress-Nginx Controller + cert-manager

### 3.2 Validate Tenant Specification
```bash
python -m tenant_manager.cli validate tenants/examples/acme-corp.yaml
```

### 3.3 Render Tenant Kubernetes Manifests
```bash
python -m tenant_manager.cli render tenants/examples/acme-corp.yaml -o rendered-acme.yaml
```

### 3.4 Provision Self-Contained Tenant Stack
```bash
python -m tenant_manager.cli provision tenants/examples/acme-corp.yaml
```

### 3.5 Execute Automated Verification Suite
```bash
python verification/run_all_tests.py
```
Expected output:
```
================================================================================
ALL SECURITY, ISOLATION, BUDGET, AND LIFECYCLE TESTS PASSED (100%)
================================================================================
19 passed in 1.88s
```
