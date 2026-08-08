from __future__ import annotations

from typing import Any

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


def get_healthcare_dataset() -> dict[str, list[dict[str, Any]]]:
    return {
        "datasets": [
            {
                "urn": _urn("raw_patients"),
                "tags": ["pii", "internal"],
                "owner_urn": None,
                "domain_urn": "urn:li:domain:healthcare",
            },
            {
                "urn": _urn("staging_patients"),
                "tags": ["pii"],
                "owner_urn": "urn:li:corpuser:clinical_team",
                "domain_urn": "urn:li:domain:healthcare",
            },
            {
                "urn": _urn("mart_billing"),
                "tags": ["critical"],
                "owner_urn": "urn:li:corpuser:finance_team",
                "domain_urn": "urn:li:domain:finance",
            },
            {
                "urn": _urn("mart_demographics"),
                "tags": ["internal"],
                "owner_urn": "urn:li:corpuser:clinical_team",
                "domain_urn": "urn:li:domain:healthcare",
            },
        ],
        "edges": [
            {
                "src": _urn("raw_patients"),
                "dst": _urn("staging_patients"),
                "kind": "IDENTITY",
                "columns": None,
            },
            {
                "src": _urn("staging_patients"),
                "dst": _urn("mart_billing"),
                "kind": "PROJECTION",
                "columns": ["billing_amount"],
            },
            {
                "src": _urn("staging_patients"),
                "dst": _urn("mart_demographics"),
                "kind": "PROJECTION",
                "columns": ["patient_id"],
            },
        ],
        "glossary_terms": [
            {"urn": "urn:li:glossaryTerm:BillingAmount", "name": "BillingAmount"},
            {"urn": "urn:li:glossaryTerm:PatientId", "name": "PatientId"},
        ],
        "policy_rules": [
            {
                "rule_id": "pii_must_have_owner",
                "dimension": 3,
                "predicate": "asset.has_owner",
                "scope_urn": _urn("raw_patients"),
                "glossary_urn": None,
            },
            {
                "rule_id": "billing_must_be_critical",
                "dimension": 3,
                "predicate": "asset.has_tag('critical')",
                "scope_urn": _urn("mart_billing"),
                "glossary_urn": "urn:li:glossaryTerm:BillingAmount",
            },
        ],
    }
