"""
Tenant Specification Validator
Performs pre-flight syntax, schema, and operational validation on tenant configuration files.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
import yaml
from pydantic import ValidationError

from tenant_manager.models import TenantSpecification


class ValidationResult:
    def __init__(self, is_valid: bool, errors: List[str], warnings: List[str], spec: Any = None):
        self.is_valid = is_valid
        self.errors = errors
        self.warnings = warnings
        self.spec: Optional[TenantSpecification] = spec

    def __repr__(self) -> str:
        status = "PASSED" if self.is_valid else "FAILED"
        return f"<ValidationResult status={status} errors={len(self.errors)} warnings={len(self.warnings)}>"


class SpecValidator:
    @staticmethod
    def load_yaml(file_path: str | Path) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"File {file_path} does not contain a valid YAML dictionary mapping.")
        return data

    @classmethod
    def validate_file(cls, file_path: str | Path) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        try:
            raw_data = cls.load_yaml(file_path)
        except Exception as e:
            return ValidationResult(is_valid=False, errors=[f"YAML parsing error: {e}"], warnings=[])

        try:
            spec = TenantSpecification.model_validate(raw_data)
        except ValidationError as e:
            for err in e.errors():
                loc = " -> ".join(str(p) for p in err["loc"])
                errors.append(f"[{loc}]: {err['msg']}")
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

        # Operational heuristics and best practices checks
        if spec.governance.max_budget_usd < 10.0:
            warnings.append(
                f"Configured maxBudgetUsd (${spec.governance.max_budget_usd:.2f}) is very low. "
                "The tenant may quickly hit budget cutoff."
            )

        if spec.identity.ldap_enabled and not spec.identity.use_tls:
            warnings.append(
                "LDAP useTls is set to false. Transmitting enterprise credentials over unencrypted LDAP is strongly discouraged."
            )

        if spec.identity.ldap_enabled and spec.identity.server_port == 389 and spec.identity.use_tls:
            warnings.append(
                "LDAP port is set to 389 with useTls=true. If using LDAPS (SSL/TLS direct), the standard port is 636."
            )

        if spec.compute.replicas > 1:
            warnings.append(
                f"Configured replicas={spec.compute.replicas} with default SQLite storage. "
                "SQLite with multiple active replicas can experience database lock contention. "
                "Consider external PostgreSQL if active-active replicas are required."
            )

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings, spec=spec)
