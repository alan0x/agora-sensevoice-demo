.PHONY: check rust-check python-check web-check

check: rust-check python-check web-check

rust-check:
	cd control-plane && cargo test --locked
	cd control-plane && cargo clippy --locked -- -D warnings

python-check:
	cd bridge && python3 -m unittest discover -s tests -v

web-check:
	node --check control-plane/static/app.js
	bash -n bridge/start-real.sh
	bash -n bridge/start-ominix-asr.sh
	bash -n deploy/macos/install-launch-agents.sh
	bash -n deploy/macos/status.sh
