ai_slop_bot.zip: ai_slop_bot.py budget.py bufo.py bufo_emojis.json conversations.py parsing.py prompts.py providers.py slack.py image_upload.py media_refs.py model_config.py usage.py payments.py paypal_api.py payment_handler.py checkout.html backends
	rm -rf build
	mkdir -p build
	cp ai_slop_bot.py budget.py bufo.py bufo_emojis.json conversations.py parsing.py prompts.py providers.py slack.py image_upload.py media_refs.py model_config.py usage.py payments.py paypal_api.py payment_handler.py checkout.html build
	cp -r backends build
	cp -r $(shell pipenv --venv)/lib/python3.12/site-packages/* build
	rm -rf build/boto*
	cd build && zip -r ../ai_slop_bot.zip .
	rm -rf build

check:
	pylint --max-line-length=120 *.py backends/*.py
	pytest
