"""
Kubernetes Tenant Provisioner
Renders production manifests and manages self-contained tenant Kubernetes namespaces,
secrets, OpenWebUI deployments, and dedicated LiteLLM proxies.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional
import yaml

from tenant_manager.models import TenantSpecification

logger = logging.getLogger("tenant_manager.k8s")


class K8sProvisionerError(Exception):
    """Exception raised for Kubernetes resource provisioning errors."""
    pass


class K8sProvisioner:
    def __init__(
        self,
        ingress_namespace: str = "ingress-nginx",
        kubectl_bin: str = "kubectl"
    ):
        self.ingress_namespace = ingress_namespace
        self.kubectl_bin = kubectl_bin

    def get_namespace_name(self, tenant_id: str) -> str:
        return f"tenant-{tenant_id}"

    def render_manifests(self, spec: TenantSpecification, virtual_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Renders all Kubernetes manifests for a self-contained tenant stack.
        """
        ns = self.get_namespace_name(spec.metadata.tenant_id)
        tenant_id = spec.metadata.tenant_id
        local_master_key = virtual_key or f"sk-tenant-{tenant_id}-{hashlib.sha256(tenant_id.encode()).hexdigest()[:16]}"

        # 1. Namespace
        namespace_manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": ns,
                "labels": {
                    "saas.platform.io/tenant": "true",
                    "saas.platform.io/tenant-id": tenant_id,
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                }
            }
        }

        # 2. ResourceQuota (Sized for both OpenWebUI and LiteLLM pods)
        resource_quota_manifest = {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": "tenant-quota",
                "namespace": ns,
                "labels": {"saas.platform.io/tenant-id": tenant_id}
            },
            "spec": {
                "hard": {
                    "requests.cpu": "2",
                    "requests.memory": "4Gi",
                    "limits.cpu": spec.compute.cpu_limit,
                    "limits.memory": spec.compute.memory_limit,
                    "requests.storage": "20Gi",
                    "persistentvolumeclaims": "2",
                    "pods": "4"
                }
            }
        }

        # 3. LimitRange
        limit_range_manifest = {
            "apiVersion": "v1",
            "kind": "LimitRange",
            "metadata": {
                "name": "tenant-limits",
                "namespace": ns,
                "labels": {"saas.platform.io/tenant-id": tenant_id}
            },
            "spec": {
                "limits": [
                    {
                        "type": "Container",
                        "default": {
                            "cpu": spec.compute.cpu_limit,
                            "memory": spec.compute.memory_limit
                        },
                        "defaultRequest": {
                            "cpu": spec.compute.cpu_request,
                            "memory": spec.compute.memory_request
                        }
                    }
                ]
            }
        }

        # 4. OpenWebUI Dedicated PVC
        pvc_manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": "openwebui-data-pvc",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {
                    "requests": {
                        "storage": spec.compute.storage_size
                    }
                }
            }
        }
        if spec.compute.storage_class:
            pvc_manifest["spec"]["storageClassName"] = spec.compute.storage_class

        # 5. OpenWebUI Branding ConfigMap
        branding_config_manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "openwebui-branding",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "data": {
                "WEBUI_NAME": spec.branding.portal_title,
                "WEBUI_URL": f"https://{spec.routing.fqdn}",
                "DEFAULT_MODELS": spec.branding.default_model,
                "MODEL_FILTER_LIST": ",".join(spec.governance.allowed_models),
                "ENABLE_MODEL_FILTER": "true",
                "ENABLE_SIGNUP": "false",
                "ENABLE_COMMUNITY_SHARING": "false",
                "SHOW_ADMIN_DETAILS": "false",
                "ENABLE_ADMIN_EXPORT": "false",
                "custom.css": spec.branding.custom_css or ""
            }
        }

        # 6. Dedicated LiteLLM ConfigMap
        model_list = []
        for model_name in spec.governance.allowed_models:
            provider = "openai"
            if "claude" in model_name:
                provider = "anthropic"
            elif "gemini" in model_name:
                provider = "gemini"

            model_list.append({
                "model_name": model_name,
                "litellm_params": {
                    "model": f"{provider}/{model_name}",
                    "rpm": spec.governance.rpm_limit,
                    "tpm": spec.governance.tpm_limit
                }
            })

        litellm_config_dict = {
            "model_list": model_list,
            "litellm_settings": {
                "drop_params": True,
                "set_verbose": False,
                "max_budget": spec.governance.max_budget_usd,
                "budget_duration": spec.governance.budget_duration,
                "fallbacks": [
                    {"gpt-4o": ["claude-3-5-sonnet"]}
                ]
            },
            "general_settings": {
                "master_key": "os.environ/LITELLM_MASTER_KEY"
            }
        }

        litellm_configmap_manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "openwebui-litellm-config",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "litellm",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "data": {
                "config.yaml": yaml.dump(litellm_config_dict, sort_keys=False)
            }
        }

        # 7. Unified Credentials Secret (OpenWebUI + Local LiteLLM + BYOK Upstream Keys)
        cred = spec.upstream_credentials
        secret_data: Dict[str, str] = {
            "LITELLM_MASTER_KEY": local_master_key,
            "OPENAI_API_KEY": cred.openai_api_key or f"sk-platform-mock-key-{tenant_id}",
            "ANTHROPIC_API_KEY": cred.anthropic_api_key or "",
            "AZURE_API_KEY": cred.azure_openai_api_key or "",
            "AZURE_API_BASE": cred.azure_openai_endpoint or "",
            "AWS_ACCESS_KEY_ID": cred.aws_access_key_id or "",
            "AWS_SECRET_ACCESS_KEY": cred.aws_secret_access_key or "",
            "AWS_REGION_NAME": cred.aws_region_name or "us-east-1",
            "WEBUI_SECRET_KEY": hashlib.sha256(f"{tenant_id}-secret-{local_master_key}".encode()).hexdigest(),
        }
        if spec.identity.ldap_enabled:
            secret_data["LDAP_APP_PASSWORD"] = spec.identity.bind_password
        if spec.admin_fallback.enabled:
            secret_data["ADMIN_EMAIL"] = spec.admin_fallback.email
            secret_data["ADMIN_PASSWORD"] = spec.admin_fallback.password
        if spec.identity.ca_cert_pem:
            secret_data["ldap_ca.crt"] = spec.identity.ca_cert_pem

        secret_manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "openwebui-credentials",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "type": "Opaque",
            "stringData": secret_data
        }

        # 8. OpenWebUI Deployment (Directs API to local in-namespace LiteLLM)
        openwebui_env = [
            {"name": "PORT", "value": "8080"},
            {"name": "DATA_DIR", "value": "/app/backend/data"},
            {"name": "WEBUI_AUTH", "value": "true"},
            {"name": "ENABLE_OPENAI_API", "value": "true"},
            {"name": "ENABLE_OLLAMA_API", "value": "false"},
            {"name": "OPENAI_API_BASE_URL", "value": "http://litellm:4000/v1"},
            {
                "name": "OPENAI_API_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "openwebui-credentials",
                        "key": "LITELLM_MASTER_KEY"
                    }
                }
            },
            {
                "name": "WEBUI_SECRET_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "openwebui-credentials",
                        "key": "WEBUI_SECRET_KEY"
                    }
                }
            },
            {
                "name": "WEBUI_NAME",
                "valueFrom": {
                    "configMapKeyRef": {
                        "name": "openwebui-branding",
                        "key": "WEBUI_NAME"
                    }
                }
            },
            {
                "name": "WEBUI_URL",
                "valueFrom": {
                    "configMapKeyRef": {
                        "name": "openwebui-branding",
                        "key": "WEBUI_URL"
                    }
                }
            },
            {
                "name": "DEFAULT_MODELS",
                "valueFrom": {
                    "configMapKeyRef": {
                        "name": "openwebui-branding",
                        "key": "DEFAULT_MODELS"
                    }
                }
            },
            {
                "name": "MODEL_FILTER_LIST",
                "valueFrom": {
                    "configMapKeyRef": {
                        "name": "openwebui-branding",
                        "key": "MODEL_FILTER_LIST"
                    }
                }
            },
            {
                "name": "ENABLE_MODEL_FILTER",
                "valueFrom": {
                    "configMapKeyRef": {
                        "name": "openwebui-branding",
                        "key": "ENABLE_MODEL_FILTER"
                    }
                }
            }
        ]

        if spec.identity.ldap_enabled:
            openwebui_env.extend([
                {"name": "ENABLE_LDAP", "value": "true"},
                {"name": "LDAP_SERVER_LABEL", "value": f"{spec.metadata.tenant_name} Active Directory"},
                {"name": "LDAP_SERVER_HOST", "value": spec.identity.server_host},
                {"name": "LDAP_SERVER_PORT", "value": str(spec.identity.server_port)},
                {"name": "LDAP_USE_TLS", "value": str(spec.identity.use_tls).lower()},
                {"name": "LDAP_VALIDATE_CERT", "value": str(spec.identity.validate_cert).lower()},
                {"name": "LDAP_SEARCH_BASE", "value": spec.identity.search_base},
                {"name": "LDAP_APP_DN", "value": spec.identity.bind_dn},
                {
                    "name": "LDAP_APP_PASSWORD",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": "openwebui-credentials",
                            "key": "LDAP_APP_PASSWORD"
                        }
                    }
                },
                {"name": "LDAP_ATTRIBUTE_FOR_USERNAME", "value": spec.identity.username_attribute},
                {"name": "LDAP_SEARCH_FILTERS", "value": spec.identity.search_filter or ""}
            ])
            if spec.identity.ca_cert_pem:
                openwebui_env.append({"name": "LDAP_CA_CERT_FILE", "value": "/app/backend/certs/ldap_ca.crt"})

        openwebui_deployment_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "openwebui",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "replicas": spec.compute.replicas,
                "selector": {
                    "matchLabels": {"app.kubernetes.io/name": "openwebui"}
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": "openwebui",
                            "app.kubernetes.io/part-of": "openwebui-tenant",
                            "saas.platform.io/tenant-id": tenant_id
                        }
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "fsGroup": 1000
                        },
                        "containers": [
                            {
                                "name": "openwebui",
                                "image": "ghcr.io/open-webui/open-webui:main",
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": 8080, "name": "http"}],
                                "env": openwebui_env,
                                "volumeMounts": [
                                    {"name": "data-volume", "mountPath": "/app/backend/data"}
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": spec.compute.cpu_request,
                                        "memory": spec.compute.memory_request
                                    },
                                    "limits": {
                                        "cpu": spec.compute.cpu_limit,
                                        "memory": spec.compute.memory_limit
                                    }
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]}
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": 8080},
                                    "initialDelaySeconds": 45,
                                    "periodSeconds": 20
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/health", "port": 8080},
                                    "initialDelaySeconds": 20,
                                    "periodSeconds": 10
                                }
                            }
                        ],
                        "volumes": [
                            {
                                "name": "data-volume",
                                "persistentVolumeClaim": {"claimName": "openwebui-data-pvc"}
                            }
                        ]
                    }
                }
            }
        }

        # 9. OpenWebUI Service
        openwebui_service_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "openwebui",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "type": "ClusterIP",
                "ports": [{"port": 8080, "targetPort": 8080, "protocol": "TCP", "name": "http"}],
                "selector": {"app.kubernetes.io/name": "openwebui"}
            }
        }

        # 10. Dedicated LiteLLM Deployment
        litellm_deployment_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "litellm",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "litellm",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {"app.kubernetes.io/name": "litellm"}
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": "litellm",
                            "app.kubernetes.io/part-of": "openwebui-tenant",
                            "saas.platform.io/tenant-id": tenant_id
                        }
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "fsGroup": 1000
                        },
                        "containers": [
                            {
                                "name": "litellm",
                                "image": "ghcr.io/berriai/litellm:main-v1.40.0",
                                "imagePullPolicy": "IfNotPresent",
                                "args": ["--config", "/app/config.yaml", "--port", "4000"],
                                "ports": [{"containerPort": 4000, "name": "http", "protocol": "TCP"}],
                                "env": [
                                    {
                                        "name": "LITELLM_MASTER_KEY",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "openwebui-credentials",
                                                "key": "LITELLM_MASTER_KEY"
                                            }
                                        }
                                    },
                                    {
                                        "name": "OPENAI_API_KEY",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "openwebui-credentials",
                                                "key": "OPENAI_API_KEY"
                                            }
                                        }
                                    },
                                    {
                                        "name": "ANTHROPIC_API_KEY",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "openwebui-credentials",
                                                "key": "ANTHROPIC_API_KEY",
                                                "optional": True
                                            }
                                        }
                                    }
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "config-volume",
                                        "mountPath": "/app/config.yaml",
                                        "subPath": "config.yaml",
                                        "readOnly": True
                                    },
                                    {"name": "tmp-volume", "mountPath": "/tmp"}
                                ],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "500m", "memory": "512Mi"}
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]}
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": 4000},
                                    "initialDelaySeconds": 30,
                                    "periodSeconds": 15
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/health", "port": 4000},
                                    "initialDelaySeconds": 15,
                                    "periodSeconds": 10
                                }
                            }
                        ],
                        "volumes": [
                            {
                                "name": "config-volume",
                                "configMap": {"name": "openwebui-litellm-config"}
                            },
                            {"name": "tmp-volume", "emptyDir": {}}
                        ]
                    }
                }
            }
        }

        # 11. Dedicated LiteLLM Service
        litellm_service_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "litellm",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "litellm",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "type": "ClusterIP",
                "ports": [{"port": 4000, "targetPort": 4000, "protocol": "TCP", "name": "http"}],
                "selector": {"app.kubernetes.io/name": "litellm"}
            }
        }

        # 12. OpenWebUI Zero-Trust NetworkPolicy
        openwebui_netpol_manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "openwebui-zero-trust-netpol",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {"app.kubernetes.io/name": "openwebui"}
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": self.ingress_namespace
                                    }
                                }
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 8080}]
                    }
                ],
                "egress": [
                    # CoreDNS in kube-system
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"k8s-app": "kube-dns"}
                                }
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53}
                        ]
                    },
                    # Egress ONLY to local LiteLLM proxy in this namespace
                    {
                        "to": [
                            {
                                "podSelector": {
                                    "matchLabels": {"app.kubernetes.io/name": "litellm"}
                                }
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 4000}]
                    }
                ]
            }
        }

        if spec.identity.ldap_enabled:
            openwebui_netpol_manifest["spec"]["egress"].append({
                "to": [
                    {
                        "ipBlock": {
                            "cidr": "0.0.0.0/0",
                            "except": [
                                "10.0.0.0/8",
                                "172.16.0.0/12",
                                "192.168.0.0/16",
                                "169.254.169.254/32"
                            ]
                        }
                    }
                ],
                "ports": [{"protocol": "TCP", "port": spec.identity.server_port}]
            })

        # 13. LiteLLM Zero-Trust NetworkPolicy
        litellm_netpol_manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "litellm-zero-trust-netpol",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "litellm",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                }
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {"app.kubernetes.io/name": "litellm"}
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        # Only allow local OpenWebUI
                        "from": [
                            {
                                "podSelector": {
                                    "matchLabels": {"app.kubernetes.io/name": "openwebui"}
                                }
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 4000}]
                    }
                ],
                "egress": [
                    # CoreDNS
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"k8s-app": "kube-dns"}
                                }
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53}
                        ]
                    },
                    # Outbound HTTPS to external Cloud LLM Providers
                    {
                        "to": [
                            {
                                "ipBlock": {
                                    "cidr": "0.0.0.0/0",
                                    "except": [
                                        "169.254.169.254/32",
                                        "10.0.0.0/8",
                                        "172.16.0.0/12",
                                        "192.168.0.0/16"
                                    ]
                                }
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 443}]
                    }
                ]
            }
        }

        # 14. Ingress
        ingress_manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "openwebui-ingress",
                "namespace": ns,
                "labels": {
                    "app.kubernetes.io/name": "openwebui",
                    "app.kubernetes.io/part-of": "openwebui-tenant",
                    "saas.platform.io/tenant-id": tenant_id
                },
                "annotations": {
                    "cert-manager.io/cluster-issuer": spec.routing.tls_cluster_issuer,
                    "nginx.ingress.kubernetes.io/proxy-body-size": "50m",
                    "nginx.ingress.kubernetes.io/proxy-read-timeout": "600",
                    "nginx.ingress.kubernetes.io/proxy-send-timeout": "600",
                    "nginx.ingress.kubernetes.io/ssl-redirect": "true"
                }
            },
            "spec": {
                "ingressClassName": "nginx",
                "tls": [
                    {
                        "hosts": [spec.routing.fqdn],
                        "secretName": f"{tenant_id}-tls"
                    }
                ],
                "rules": [
                    {
                        "host": spec.routing.fqdn,
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "openwebui",
                                            "port": {"number": 8080}
                                        }
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        return [
            namespace_manifest,
            resource_quota_manifest,
            limit_range_manifest,
            pvc_manifest,
            branding_config_manifest,
            litellm_configmap_manifest,
            secret_manifest,
            openwebui_deployment_manifest,
            openwebui_service_manifest,
            litellm_deployment_manifest,
            litellm_service_manifest,
            openwebui_netpol_manifest,
            litellm_netpol_manifest,
            ingress_manifest
        ]

    def dump_yaml(self, manifests: List[Dict[str, Any]]) -> str:
        """Returns multi-document YAML string for all manifests."""
        return yaml.dump_all(manifests, sort_keys=False)

    def apply_manifests(self, manifests: List[Dict[str, Any]]) -> bool:
        """
        Applies manifests via kubectl apply -f -
        """
        yaml_content = self.dump_yaml(manifests)
        try:
            process = subprocess.run(
                [self.kubectl_bin, "apply", "-f", "-"],
                input=yaml_content,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Successfully applied Kubernetes manifests:\n{process.stdout}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"kubectl apply failed: {e.stderr}")
            raise K8sProvisionerError(f"kubectl apply failed: {e.stderr}") from e
        except FileNotFoundError:
            logger.warning("kubectl binary not found in PATH; running in offline simulation mode.")
            return True

    def delete_namespace(self, tenant_id: str) -> bool:
        """
        Tears down the tenant namespace and all contained resources.
        """
        ns = self.get_namespace_name(tenant_id)
        try:
            process = subprocess.run(
                [self.kubectl_bin, "delete", "namespace", ns, "--ignore-not-found=true"],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Namespace {ns} deleted: {process.stdout}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"kubectl delete namespace failed: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.warning("kubectl binary not found in PATH; simulated namespace deletion.")
            return True
