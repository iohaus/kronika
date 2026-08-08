import logging
import os

from kronika.evidence import EvidenceRecord, Recommendation
from kronika.ports import LLMAdapter

log = logging.getLogger("kronika.application.llm")


class LocalLLMAdapter(LLMAdapter):
    def explain(self, evidence: EvidenceRecord, audience: str = "ENGINEER") -> str:
        if not evidence.outcomes:
            return (
                f"[{audience.upper()} ASSESSMENT] All monitored data assets remain nominal. "
                "No pipeline containment actions or active mitigations are currently required."
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

        if audience.upper() == "EXECUTIVE":
            return (
                "EXECUTIVE SUMMARY:\n"
                f"A critical data quality anomaly was detected from source dataset "
                f"'{evidence.source_urn}' at {evidence.occurred_at}.\n"
                f"Event ID: {evidence.event_id}\n"
                "To protect downstream financial and operational reporting, Kronika calculated "
                f"a minimal vertex cut and recommends containment for {len(halted)} asset(s): "
                f"{halt_str}.\n"
                "Unaffected business domains remain fully operational under active monitoring."
            )

        if audience.upper() == "OWNER":
            return (
                "DATA OWNER NOTICE:\n"
                f"An upstream quality observation event (ID: {evidence.event_id}) occurred "
                f"on '{evidence.source_urn}'. Kronika's decision engine evaluated downstream "
                f"lineage impact and flagged assets for containment: {halt_str}.\n"
                f"Assets operating under telemetry monitoring: {mon_str}.\n"
                "Please review the pending action queue to approve or override the plan."
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
            f"'{evidence.source_urn}' occurred at {evidence.occurred_at}.\n"
            f"Containment Objective: {evidence.containment.objective}\n\n"
            "Detailed Asset Impact Breakdown:\n" + "\n".join(details)
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

    def explain(self, evidence: EvidenceRecord, audience: str = "ENGINEER") -> str:
        if not self.api_key:
            return self._fallback.explain(evidence, audience)

        try:
            import openai

            client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
                timeout=self.timeout,
            )
            prompt = (
                "You are Kronika, an autonomous data-quality decision agent for DataHub. "
                "Your task is to explain, in clear and cohesive prose, what happened in "
                "this event, why it matters, and what should be done next.\n\n"
                f"Write for a {audience.upper()} audience: be precise but avoid "
                "unnecessary jargon. Focus on the decision logic, the evidence you "
                "used, and the implications of your recommendation. Do not use bullet "
                "points or section headings; produce a single, flowing narrative.\n\n"
                "Event context:\n"
                f"- Event ID: {evidence.event_id}\n"
                f"- Source URN: {evidence.source_urn}\n"
                f"- Occurred At: {evidence.occurred_at}\n"
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
            return content.strip() if content else self._fallback.explain(evidence, audience)
        except Exception as exc:
            log.warning("OpenAILLMAdapter error, falling back to LocalLLMAdapter: %s", exc)
            return self._fallback.explain(evidence, audience)
