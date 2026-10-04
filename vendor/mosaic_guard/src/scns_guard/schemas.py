from __future__ import annotations

import json
from pathlib import Path

from .causal import AgentTrace, CausalAttributionReport
from .dataset import PredicateTrainingRow, ResearchScenario
from .controlled_replay import ReplayOutcome
from .task_suite import TransferTaskSpec
from .planning import ModelPlanningOutput, PlanningResult, PlanningTurn
from .outcomes import PublicOutcome
from .predicates import LinearPredicateSpec
from .models import (
    ActionProposal,
    DecisionReceipt,
    DetectorSignal,
    ObligationToken,
    TransferParams,
    TrustedFact,
)
from .policy import PolicySpec
from .auth import IdentityClaims, ControlClaims, SignedCredential
from .gateway import ReviewRequest
from .http_service import HandleRequest, ProofRequest, ResetRequest, RevokeSessionRequest, TextRequest, CodeRequest
from .deployment import ServiceConfig
from .sandbox import SandboxResult


SCHEMA_MODELS = {
    "action_proposal": ActionProposal,
    "transfer_params": TransferParams,
    "trusted_fact": TrustedFact,
    "obligation_token": ObligationToken,
    "detector_signal": DetectorSignal,
    "decision_receipt": DecisionReceipt,
    "policy_spec": PolicySpec,
    "agent_trace": AgentTrace,
    "causal_attribution_report": CausalAttributionReport,
    "research_scenario": ResearchScenario,
    "predicate_training_row": PredicateTrainingRow,
    "linear_predicate": LinearPredicateSpec,
    "replay_outcome": ReplayOutcome,
    "transfer_task_spec": TransferTaskSpec,
    "model_planning_output": ModelPlanningOutput,
    "planning_result": PlanningResult,
    "planning_turn": PlanningTurn,
    "public_outcome": PublicOutcome,
    "gateway_review_request": ReviewRequest,
    "gateway_handle_request": HandleRequest,
    "gateway_proof_request": ProofRequest,
    "gateway_reset_request": ResetRequest,
    "gateway_revoke_session_request": RevokeSessionRequest,
    "gateway_text_request": TextRequest,
    "gateway_code_request": CodeRequest,
    "identity_claims": IdentityClaims,
    "control_claims": ControlClaims,
    "signed_credential": SignedCredential,
    "service_config": ServiceConfig,
    "sandbox_result": SandboxResult,
}


def export_schemas(directory: str | Path) -> tuple[Path, ...]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, model in SCHEMA_MODELS.items():
        path = target / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        paths.append(path)
    return tuple(paths)
