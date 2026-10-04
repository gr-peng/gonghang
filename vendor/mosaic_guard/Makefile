.PHONY: test check demo experiment schemas examples verify manifest verify-bundle reproduce security-review security-stress clean

PYTHON ?= python

# The package can be exercised directly from the repository without installation.
test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

check:
	PYTHONPATH=src $(PYTHON) -m compileall -q src tests scripts
	PYTHONPATH=src $(PYTHON) -m pytest -q

demo:
	rm -f artifacts/demo_receipts.jsonl
	PYTHONPATH=src $(PYTHON) -m scns_guard.cli demo \
		--policy configs/transfer_policy.yaml \
		--receipts artifacts/demo_receipts.jsonl

experiment:
	PYTHONPATH=src $(PYTHON) -m scns_guard.cli experiment \
		--policy configs/transfer_policy.yaml \
		--repeats 20 \
		--seed 7 \
		--output artifacts/synthetic_results.json

schemas:
	PYTHONPATH=src $(PYTHON) -m scns_guard.cli schemas --output-dir schemas

examples:
	PYTHONPATH=src $(PYTHON) scripts/export_examples.py

verify:
	PYTHONPATH=src $(PYTHON) -m scns_guard.cli verify-receipts artifacts/demo_receipts.jsonl

manifest:
	PYTHONPATH=src $(PYTHON) scripts/build_manifest.py

verify-bundle:
	PYTHONPATH=src $(PYTHON) scripts/verify_manifest.py

reproduce:
	PYTHONPATH=src $(PYTHON) scripts/reproduce.py

clean:
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info src/*.egg-info
	rm -f artifacts/demo_receipts.jsonl artifacts/demo_output.json

# Offline, local mock only; no real financial or LLM service.
security-review:
	PYTHONPATH=src $(PYTHON) -m pytest -q tests/test_independent_security_review.py tests/test_independent_runtime_limits.py tests/test_independent_crash_boundaries.py

security-stress:
	PYTHONPATH=src $(PYTHON) scripts/run_security_stress.py
