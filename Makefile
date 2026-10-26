PY := .venv/bin/python
TEMPORAL := ./bin/temporal

.PHONY: setup temporal probe demo demo-fixed audit flow worker clean dev client-install

dev:              ## start both backend (8000) and client (3000) in sync
	./start.sh

client-install:   ## install frontend dependencies
	cd client && npm install

setup:            ## create venv and install deps
	python3 -m venv .venv && $(PY) -m pip install -q --upgrade pip && $(PY) -m pip install -q -r requirements.txt

temporal:         ## download the dev server into ./bin (once)
	mkdir -p bin && curl -sSL "https://temporal.download/cli/archive/latest?platform=darwin&arch=$(shell uname -m | sed 's/x86_64/amd64/')" -o /tmp/temporal.tar.gz && tar -xzf /tmp/temporal.tar.gz -C bin

probe:            ## verify the LLM endpoint before anything depends on it
	$(PY) audit.py probe

demo:             ## the vulnerable tree: 0/100
	MOCK_LLM=1 $(PY) audit.py run --path fixtures/bad --frameworks SOC2,HIPAA,GDPR

demo-fixed:       ## the remediated tree: 100/100
	MOCK_LLM=1 $(PY) audit.py run --path fixtures/fixed --frameworks SOC2,HIPAA,GDPR

audit:            ## audit a real repo: make audit REPO=OWASP/NodeGoat
	MOCK_LLM=1 $(PY) audit.py run --clone $(REPO) --frameworks SOC2,GDPR --max-files 12

worker:           ## start the Temporal worker
	$(PY) -m workflows.worker

flow:             ## run the durable workflow (needs temporal + worker running)
	$(PY) -m workflows.start --path fixtures/bad --frameworks SOC2,HIPAA,GDPR --slow 2

clean:
	rm -rf out repo __pycache__ */__pycache__
