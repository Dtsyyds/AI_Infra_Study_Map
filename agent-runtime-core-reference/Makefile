SHELL := /bin/bash
PY := env -u PYTHONPATH .venv/bin/python
RUN := 20261022-reference

.PHONY: db-start db-stop migrate start stop worker test check demo status preflight

preflight:
	$(PY) -m examples.dev_preflight

db-start:
	.postgres/bin/pg_ctl -D $(CURDIR)/.pgdata -l $(CURDIR)/.pglog -o '-h 127.0.0.1 -p 55439 -k $(CURDIR)/.pgdata' -w start

db-stop:
	.postgres/bin/pg_ctl -D $(CURDIR)/.pgdata -m fast -w stop

migrate:
	$(PY) -m examples.record --run $(RUN) --name migrate -- .venv/bin/alembic upgrade head

start:
	$(PY) -m examples.services start

stop:
	$(PY) -m examples.services stop

status:
	$(PY) -m examples.services status

worker:
	$(PY) -m runtime.orchestrator.worker

test:
	$(PY) -m examples.record --run $(RUN) --name pytest -- .venv/bin/python -m pytest -q -rs --junitxml=reports/$(RUN)/test-results.xml

check:
	$(PY) -m examples.record --run $(RUN) --name ruff -- .venv/bin/ruff check core runtime api config tests examples deploy
	$(PY) -m examples.record --run $(RUN) --name mypy -- .venv/bin/mypy
	$(MAKE) test RUN=$(RUN)

demo:
	$(PY) -m examples.record --run $(RUN) --name demo -- .venv/bin/python -m examples.demo --run $(RUN)

images:
	$(PY) -m examples.record --run $(RUN) --name docker-image-pull -- .venv/bin/python -m examples.images
