from app.config import get_settings
from app.models import Claim


def evaluate_review_rules(claim: Claim, has_policy_evidence: bool) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if claim.requested_amount >= get_settings().review_amount_threshold:
        reasons.append("Requested amount meets or exceeds the configured human-review threshold.")
    if not has_policy_evidence:
        reasons.append("No policy evidence was retrieved for this claim.")
    if reasons:
        return "human_review", reasons
    return "human_review", ["All claims require an authorized adjuster to make the final decision."]
