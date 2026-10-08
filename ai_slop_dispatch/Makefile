ai_slop_dispatch.zip: ai_slop_dispatch.py generation_modal.py ../ai_slop_bot/hall_of_fame.py Pipfile.lock
	rm -rf build
	mkdir -p build
	cp ai_slop_dispatch.py generation_modal.py build
	cp ../ai_slop_bot/hall_of_fame.py build
	cp -r $(shell pipenv --venv)/lib/python3.12/site-packages/* build
	rm -rf build/boto*
	cd build && zip -r ../ai_slop_dispatch.zip .
	rm -rf build
