# Makefile for the public demo copy.
#
# Rewritten for this copy. The original Makefile at the source repository is
# stale V1-era and its `setup` / `demo-data` / `test` targets do not work here:
#   * setup      initialised the V1 `data/credit.db` and pre-downloaded a model
#   * demo-data  called a stub that raises NotImplementedError
#   * test       ran `pytest tests/`, but every test in this project lives in
#                `evals/` and is run through the project's own runner
# Only commands actually verified in a clean venv are listed below.

.PHONY: help install lock demo-ui eval

PY ?= python

help:
	@echo "Targets in this copy (all verified in a clean venv, Python 3.12):"
	@echo "  make install   - pip install -r requirements.txt (~2 GB, includes torch)"
	@echo "  make lock      - regenerate requirements.lock.txt from the current venv"
	@echo "  make demo-ui   - start the read-only three-screen demo app (headless)"
	@echo "  make eval      - run the project's offline eval suite (no model calls)"
	@echo ""
	@echo "There is no target for a real generation run: it needs your own inputs,"
	@echo "model credentials and a per-run authorization. See README.md."

install:
	$(PY) -m pip install -r requirements.txt

lock:
	$(PY) -m pip freeze > requirements.lock.txt

# The demo app reads already-persisted runs under evaluation/results.
# This copy ships none, so the page will refuse to render a run until you have one.
demo-ui:
	$(PY) -m streamlit run scripts/cited_demo_app.py --server.headless true --browser.gatherUsageStats false

# Offline eval suite. Runs against synthetic fixtures and mock LLM only;
# it never contacts a model provider.
eval:
	$(PY) -m evals.run_evals
