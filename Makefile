.PHONY: dry-run draft auth deps
deps:
	pip install -r requirements.txt
dry-run: deps        ## build out/reel.mp4 locally, no publish
	MODE=dry python -m pipeline.main
draft: deps          ## publish one Reel as DRAFT to the Page
	MODE=draft python -m pipeline.main
auth:                ## one-time: user token -> non-expiring Page token
	python scripts/auth.py
