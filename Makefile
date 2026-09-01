.PHONY: check rust-check python-check web-check

check: rust-check python-check web-check

rust-check:
	cd control-plane && cargo test --locked

python-check:
	cd bridge && python3 -m unittest discover -s tests -v

web-check:
	node --check control-plane/static/app.js
	bash -n bridge/start-real.sh
	bash -n bridge/start-ominix-asr.sh
