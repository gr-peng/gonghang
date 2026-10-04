import pytest

from scns_guard.lineage import LineageAuthority
from scns_guard.task_suite import build_cases, build_task_specs, validate_task_isolation
from scns_guard.task_suite import TransferTaskSpec


def test_suite_is_reproducible_and_groups_and_templates_are_separated():
    specs = build_task_specs(data_seed=71)
    assert specs == build_task_specs(data_seed=71)
    assert len(specs) == 6
    assert len({s.group_id for s in specs}) == 6
    assert {s.field for s in specs} == {"to_account", "amount_minor"}
    validate_task_isolation(specs)
    leaked = specs[0].model_copy(update={"group_id": "new-group", "split": "test"})
    with pytest.raises(ValueError, match="template leakage"):
        validate_task_isolation([*specs, leaked])


def test_real_summary_callback_and_delegated_reference():
    authority = LineageAuthority({"mosaic-runtime": b"test"})
    spec = build_task_specs(data_seed=71)[0]
    calls = []
    def summary(parents):
        calls.append(parents)
        return "generated: " + parents[0].text, {}
    cases = build_cases(spec, authority, summary)
    assert len(calls) == 1
    assert {c.condition for c in cases} == {"direct", "summary", "delegated"}
    assert len({c.scenario.group_id for c in cases}) == 1
    for case in cases:
        for source in case.scenario.trace.source_catalog().values():
            assert authority.verify(source, catalog=case.scenario.trace.source_catalog())
    summarized = next(c for c in cases if c.condition == "summary")
    assert summarized.scenario.trace.lineage_messages
    assert summarized.scenario.trace.messages[0].text.startswith("generated:")
    delegated = next(c for c in cases if c.condition == "delegated")
    user = next(m for m in delegated.scenario.trace.messages if m.source.kind.value == "user")
    assert spec.field not in user.payload
    assert spec.value not in user.text if isinstance(spec.value, str) else True
    assert delegated.scenario.protected_effect[spec.field] == spec.value


@pytest.mark.parametrize("bad_amount", [1.0, True, 0, -1, 2**63])
def test_task_specs_preserve_exact_minor_unit_contract(bad_amount):
    values = build_task_specs(data_seed=71)[0].model_dump()
    values["amount_minor"] = bad_amount
    with pytest.raises(ValueError):
        TransferTaskSpec.model_validate(values)
