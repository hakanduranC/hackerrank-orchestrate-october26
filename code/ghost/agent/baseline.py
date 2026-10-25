"""Deterministic baseline: portfolio -> verdict without a model.

This is the parity baseline and the fallback when the agent cannot produce a
guard-passing verdict. It takes the linker's picks as the subject's records,
rejects window alternatives, and reads status and treatment straight from the
portfolio. Intent comes from a multilingual keyword rule.
"""

from __future__ import annotations

import re

from ghost.agent.schemas import (
    CaseVerdict,
    Intent,
    Membership,
    ResourceVerdict,
    Status,
    Treatment,
)
from ghost.portfolio import Portfolio, ResourceView

# A request on this intake is a deletion request unless it narrows itself to something else
# (access, export, correction, marketing opt-out) or explicitly says it is not a deletion.
_SCOPED_RE = re.compile(
    r"marketing lists only|stop the promotional|still (need|use|want) (the|this|my) account|"
    r"(send|give|need|want|get|receive)\w* (me )?a copy|"
    r"copy of (my |the |last )?(information|data|invoices)|"
    r"download my|what personal information|information request|"
    r"misspelled|out of date|change the email|reset(ting)? the login|invoices|"
    r"correct it|replace it",
    re.IGNORECASE,
)
_NEGATED_RE = re.compile(
    r"not asking for deletion|not a request to (erase|delete)|"
    r"(don't|do not|dont) (delete|close|remove|erase)|without (removing|deleting|closing)|"
    r"leave my account|keep (my|the) account",
    re.IGNORECASE,
)


# A deletion request names the act or the audit subject in some language. "Confirm that the
# profile is gone" (sample case_26) has no deletion verb, so audit vocabulary counts too.
# Measured on all 550 request texts: every deletion request matches.
_DELETION_VERB_RE = re.compile(
    r"delet|eras|remov|clos|forget|wipe|purg|destroy|get rid|exclu|elimin|suprim|supprim|suppress"
    r"|apag|borr|cancel|löschen|entfern|삭제|削除|удал|清除|删除|verwijder|slet|radera|poista|usuń"
    r"|\bsil\b|\bşil",
    re.I,
)
_AUDIT_CONTEXT_RE = re.compile(
    r"gone|no longer|still (?:exist|appear|recogni|point|identif|show)|identif|anonym"
    r"|personal (?:data|information|details)|profile|account|compte|cuenta|conta|konto"
    r"|données|datos|dados|개인정보|계정|個人情報|アカウント|персональн|аккаунт",
    re.I,
)
# "do not delete" in the languages of the dataset; a negated verb is not a deletion request.
_MULTILINGUAL_NEGATION_RE = re.compile(
    r"삭제하지\s*마|삭제하지\s*말|削除しないで|削除せず|no\s+(?:borren|eliminen|borrar|eliminar)"
    r"|ne\s+(?:supprimez|pas supprimer)|n[ãa]o\s+(?:apaguem|excluam|apagar|excluir)"
    r"|nicht\s+löschen|не\s+удаляйте",
    re.I,
)


def classify_intent(text: str, intake: str | None = None) -> Intent:
    """NOT_DELETION when the text scopes itself away from deletion or negates it, or when
    the privacy intake classified the request as non-deletion.

    The text rule is English-only; the intake tag is language-independent. On the dataset
    the two agree on all 10 non-deletion requests and never fire on a deletion request.
    """
    if _SCOPED_RE.search(text) or _NEGATED_RE.search(text):
        return Intent.NOT_DELETION
    if _MULTILINGUAL_NEGATION_RE.search(text):
        return Intent.NOT_DELETION
    if intake and "non-deletion" in intake.lower():
        return Intent.NOT_DELETION
    if not (_DELETION_VERB_RE.search(text) or _AUDIT_CONTEXT_RE.search(text)):
        return Intent.NOT_DELETION  # neither deletion nor its subject is mentioned: out of scope
    return Intent.DELETION


def verdict_for_view(view: ResourceView) -> ResourceVerdict:
    """The deterministic verdict for one plan record: linked records are the subject's, batch
    candidates are not; status and evidence come from the plan.
    """
    membership = Membership.NO if view.link_kind == "window-alternative" else Membership.YES
    return ResourceVerdict(
        system_id=view.system,
        resource_id=view.resource_id,
        is_subject=membership,
        status=Status(view.status) if view.status in Status.__members__ else Status.UNKNOWN,
        treatment=Treatment(view.treatment.action),
        evidence_id=view.proving_evidence,
        reason=f"{view.link_kind} link; status {view.status} from {view.proving_evidence or 'n/a'}",
    )


def baseline_verdict(portfolio: Portfolio) -> CaseVerdict:
    """The parity floor: intent from the text rules and the intake tag, one verdict per plan record.
    Used as fallback and as the regression reference.
    """
    intake = portfolio.intake_classification()
    intent = classify_intent(portfolio.request.request_text, intake.details if intake else None)
    return CaseVerdict(
        case_id=portfolio.request.case_id,
        intent=intent,
        reason="deterministic baseline",
        resources=[verdict_for_view(v) for v in portfolio.resources]
        if intent == Intent.DELETION
        else [],
    )
