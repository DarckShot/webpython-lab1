PYTHON ?= .venv/bin/python
MKDOCS = $(PYTHON) -m mkdocs
CONFIG = hod-raboty/mkdocs.yml
SITE_DIR ?= $(CURDIR)/site
DEV_ADDR ?= 127.0.0.1:8000

.PHONY: prepare build serve check
prepare:
	$(PYTHON) P3/experiment.py

build: prepare
	$(MKDOCS) build --strict -f "$(CONFIG)" -d "$(SITE_DIR)"

serve: prepare
	$(MKDOCS) serve --strict -f "$(CONFIG)" -a "$(DEV_ADDR)"

check: build
	$(PYTHON) hod-raboty/scripts/check_site.py --site "$(SITE_DIR)"
