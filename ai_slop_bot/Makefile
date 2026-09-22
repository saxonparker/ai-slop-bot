ai_slop_bot.zip: ai_slop_bot.py hall_of_fame.py video_thumbnails.py budget.py bufo.py bufo_emojis.json conversations.py parsing.py prompts.py providers.py slack.py image_upload.py media_refs.py model_config.py usage.py payments.py paypal_api.py payment_handler.py checkout.html backends Pipfile ffmpeg_layer.zip
	rm -rf build
	mkdir -p build
	cp ai_slop_bot.py budget.py bufo.py bufo_emojis.json conversations.py parsing.py prompts.py providers.py slack.py image_upload.py media_refs.py model_config.py usage.py payments.py paypal_api.py payment_handler.py checkout.html build
	cp hall_of_fame.py build
	cp video_thumbnails.py build
	cp -r backends build
	cp -r $(shell pipenv --venv)/lib/python3.12/site-packages/* build
	rm -rf build/boto*
	rm -rf build/imageio_ffmpeg*
	rm -f ai_slop_bot.zip
	cd build && zip -qr ../ai_slop_bot.zip .
	rm -rf build

ffmpeg_layer.zip: Pipfile
	rm -rf ffmpeg-build
	mkdir -p ffmpeg-build/python
	cp -r $(shell pipenv --venv)/lib/python3.12/site-packages/imageio_ffmpeg* ffmpeg-build/python/
	rm -f ffmpeg_layer.zip
	cd ffmpeg-build && zip -qr ../ffmpeg_layer.zip .
	rm -rf ffmpeg-build

check:
	pylint --max-line-length=120 *.py backends/*.py
	pytest
