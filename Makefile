.PHONY: test smoke
test:
	python3 -m unittest discover -v

smoke:
	python3 tests/integration_smoke.py
