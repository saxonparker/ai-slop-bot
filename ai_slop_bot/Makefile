ai_slop_bot.zip: ai_slop_bot.py budget.py parsing.py prompts.py providers.py slack.py image_upload.py usage.py backends Pipfile.lock
	rm -rf build
	mkdir -p build
	cp ai_slop_bot.py budget.py parsing.py prompts.py providers.py slack.py image_upload.py usage.py build
	cp -r backends build
	cp -r $(shell pipenv --venv)/lib/python3.12/site-packages/* build
	rm -rf build/boto*
	cd build && zip -r ../ai_slop_bot.zip .
	rm -rf build

check:
	pylint --max-line-length=120 *.py backends/*.py
	pytest
