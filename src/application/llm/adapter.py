import logging
import os

from kronika.evidence import EvidenceRecord, Recommendation
from kronika.ports import LLMAdapter

log = logging.getLogger("kronika.application.llm")


class LocalLLMAdapter(LLMAdapter):
    """Hand-written templated text — always reports itself as `degraded=True`,
    since by definition it is not a real model call, regardless of quality."""

    def explain(self, evidence: EvidenceRecord, audience: str = "ENGINEER") -> tuple[str, bool]:
        if not evidence.outcomes:
            return (
                f"[{audience.upper()} ASSESSMENT] All monitored data assets remain nominal. "
                "No pipeline containment actions or active mitigations are currently required.",
                True,
            )

        halted = [
            urn for urn, o in evidence.outcomes.items() if o.recommendation == Recommendation.HALT
        ]
        monitored = [
            urn
            for urn, o in evidence.outcomes.items()
            if o.recommendation == Recommendation.MONITOR
        ]

        halt_str = ", ".join(f"'{u}'" for u in halted) if halted else "none"
        mon_str = ", ".join(f"'{u}'" for u in monitored) if monitored else "none"
        trigger_str = (
            f"column(s) {', '.join(sorted(evidence.trigger_columns))}"
            if evidence.trigger_columns
            else "an unspecified column"
        )
        detail_str = (
            f" ({evidence.trigger_detail})"
            if evidence.trigger_detail
            else " (no further detail reported)"
        )

        if audience.upper() == "EXECUTIVE":
            return (
                "EXECUTIVE SUMMARY:\n"
                f"A data quality anomaly was detected on {trigger_str} of source dataset "
                f"'{evidence.source_urn}' at {evidence.occurred_at}{detail_str}.\n"
                f"Event ID: {evidence.event_id}\n"
                "To protect downstream financial and operational reporting, Kronika calculated "
                f"a minimal vertex cut and recommends containment for {len(halted)} asset(s): "
                f"{halt_str}.\n"
                "Unaffected business domains remain fully operational under active monitoring.",
                True,
            )

        if audience.upper() == "OWNER":
            return (
                "DATA OWNER NOTICE:\n"
                f"An upstream quality observation event (ID: {evidence.event_id}) occurred "
                f"on '{evidence.source_urn}', affecting {trigger_str}{detail_str}. Kronika's "
                "decision engine evaluated downstream lineage impact and flagged assets for "
                f"containment: {halt_str}.\n"
                f"Assets operating under telemetry monitoring: {mon_str}.\n"
                "Please review the pending action queue to approve or override the plan.",
                True,
            )

        details: list[str] = []
        for urn, o in sorted(evidence.outcomes.items()):
            path_str = " -> ".join(o.evidence_path)
            details.append(
                f"  * Asset: {urn}\n"
                f"    Recommendation: {o.recommendation.value}\n"
                f"    Lineage Path: {path_str}"
            )

        return (
            "ENGINEERING DIAGNOSTIC REPORT:\n"
            f"Kronika decision engine evaluated Event '{evidence.event_id}' from source "
            f"'{evidence.source_urn}' occurred at {evidence.occurred_at}, triggered by "
            f"{trigger_str}{detail_str}.\n"
            f"Containment Objective: {evidence.containment.objective}\n\n"
            "Detailed Asset Impact Breakdown:\n" + "\n".join(details),
            True,
        )


class OpenAILLMAdapter(LLMAdapter):
    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.timeout = timeout
        self._fallback = LocalLLMAdapter()

    def explain(self, evidence: EvidenceRecord, audience: str = "ENGINEER") -> tuple[str, bool]:
        if not self.api_key:
            return self._fallback.explain(evidence, audience)

        try:
            import openai

            client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
                timeout=self.timeout,
            )
            trigger_cols = (
                ", ".join(sorted(evidence.trigger_columns))
                if evidence.trigger_columns
                else "unknown (not reported by the source event)"
            )
            trigger_detail = (
                evidence.trigger_detail or "no further detail was reported by the source event"
            )
            prompt = (
                "You are Kronika, an autonomous data-quality decision agent for DataHub. "
                "Your task is to explain, in clear and cohesive prose, what happened in "
                "this event, why it matters, and what should be done next.\n\n"
                f"Write for a {audience.upper()} audience: be precise but avoid "
                "unnecessary jargon. Focus on the decision logic, the evidence you "
                "used, and the implications of your recommendation. Do not use bullet "
                "points or section headings; produce a single, flowing narrative. "
                "Only state facts given in the event context below — never invent a "
                "root cause, metric, or column that isn't listed here; if a detail "
                "isn't provided, say plainly that it wasn't reported.\n\n"
                "Event context:\n"
                f"- Event ID: {evidence.event_id}\n"
                f"- Source URN: {evidence.source_urn}\n"
                f"- Occurred At: {evidence.occurred_at}\n"
                f"- Triggering column(s): {trigger_cols}\n"
                f"- Trigger detail (from the DataHub assertion, if any): {trigger_detail}\n"
                f"- Halt Set: {list(evidence.containment.halt_set)}\n"
                f"- Objective: {evidence.containment.objective}\n"
                "- Outcomes: "
                f"{[(u, o.recommendation.value) for u, o in evidence.outcomes.items()]}\n\n"
                "Using this information, explain:\n"
                "1) what the event was and why it triggered your decision logic;\n"
                "2) how the halt set and objective shaped your reasoning;\n"
                "3) what each outcome's recommendation means in practice;\n"
                "4) what the overall data-quality impact is and what actions, if any, "
                "should follow.\n"
            )
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.2,
            )
            content = resp.choices[0].message.content
            if content:
                return content.strip(), False
            return self._fallback.explain(evidence, audience)
        except Exception as exc:
            log.warning("OpenAILLMAdapter error, falling back to LocalLLMAdapter: %s", exc)
            return self._fallback.explain(evidence, audience)
