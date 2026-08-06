from __future__ import annotations

from kronika.evidence import EvidenceRecord, Recommendation
from kronika.ports import LLMAdapter


class LocalLLMAdapter(LLMAdapter):
    def explain(self, evidence: EvidenceRecord, audience: str = "ENGINEER") -> str:
        if not evidence.outcomes:
            return (
                f"[{audience.upper()}] All data assets are healthy. "
                "No pipeline containment actions required."
            )

        halted = [
            urn for urn, o in evidence.outcomes.items() if o.recommendation == Recommendation.HALT
        ]
        monitored = [
            urn
            for urn, o in evidence.outcomes.items()
            if o.recommendation == Recommendation.MONITOR
        ]

        if audience.upper() == "EXECUTIVE":
            return (
                f"[EXECUTIVE SUMMARY] Data quality incident detected on {evidence.source_urn} "
                f"at {evidence.occurred_at}. Recommended containment: halt {len(halted)} asset(s) "
                f"({', '.join(halted) if halted else 'none'}) to protect downstream analytics."
            )

        if audience.upper() == "OWNER":
            return (
                f"[DATA OWNER NOTICE] Quality event on {evidence.source_urn} affects your domain. "
                f"Assets recommended for halt: {', '.join(halted) if halted else 'None'}. "
                f"Assets under monitoring: {', '.join(monitored) if monitored else 'None'}."
            )

        details: list[str] = []
        for urn, o in sorted(evidence.outcomes.items()):
            path_str = " → ".join(o.evidence_path)
            details.append(f"- {urn}: {o.recommendation.value} (path: {path_str})")

        return (
            f"[ENGINEERING DIAGNOSTIC]\n"
            f"Event ID: {evidence.event_id}\n"
            f"Source URN: {evidence.source_urn}\n"
            f"Impact Containment Objective: {evidence.containment.objective}\n"
            f"Affected Assets:\n" + "\n".join(details)
        )
