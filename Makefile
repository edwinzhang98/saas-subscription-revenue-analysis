# One-command pipeline. Data goes to config.yaml -> data_dir (override with SAAS_DATA_DIR).
PY := uv run python

.PHONY: all setup data warehouse marts notebooks report docs dashboard test screenshots clean

all:            ## generate data, build warehouse, run analyses, notebooks, report and docs
	$(PY) run.py all

setup:          ## create the virtual environment from uv.lock
	uv sync

data:           ## synthetic data + generator sanity checks
	$(PY) run.py generate sanity

warehouse:      ## DuckDB warehouse from sql/
	$(PY) run.py warehouse

marts:          ## parquet cache + analysis tables + key numbers
	$(PY) run.py marts crosscheck validate

notebooks:
	$(PY) run.py notebooks

report:
	$(PY) run.py figures report

docs:
	$(PY) run.py docs

dashboard:      ## launch the Streamlit app
	uv run streamlit run dashboard/app.py

test:           ## unit tests (SQL files run standalone, metric identities, determinism)
	uv run pytest -q

screenshots:    ## README screenshots of every dashboard page (needs the app's dependencies + Chrome)
	$(PY) scripts/screenshots.py

clean:          ## remove generated data (keeps committed reports)
	rm -rf "$$(uv run python -c 'from saas_rev.config import get_settings; print(get_settings().data_dir)')"
