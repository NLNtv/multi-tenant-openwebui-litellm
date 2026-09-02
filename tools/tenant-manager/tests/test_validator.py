"""
Unit tests for SpecValidator.
"""

from pathlib import Path
from tenant_manager.validator import SpecValidator


def test_validate_acme_corp_example():
    path = Path("tenants/examples/acme-corp.yaml")
    result = SpecValidator.validate_file(path)
    assert result.is_valid is True
    assert len(result.errors) == 0
    assert result.spec.metadata.tenant_id == "acme-corp"


def test_validate_globex_pharma_example():
    path = Path("tenants/examples/globex-pharma.yaml")
    result = SpecValidator.validate_file(path)
    assert result.is_valid is True
    assert len(result.errors) == 0
    assert result.spec.metadata.tenant_id == "globex-pharma"


def test_validate_stark_industries_example():
    path = Path("tenants/examples/stark-industries.yaml")
    result = SpecValidator.validate_file(path)
    assert result.is_valid is True
    # Should catch warning about replicas=2 with sqlite
    assert any("replicas=2" in w for w in result.warnings)


def test_validate_nonexistent_file():
    result = SpecValidator.validate_file("tenants/examples/does-not-exist.yaml")
    assert result.is_valid is False
    assert any("File not found" in err or "YAML parsing error" in err for err in result.errors)
