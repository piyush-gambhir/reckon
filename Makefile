.PHONY: build check test install

build:
	cargo build --locked --release

check:
	cargo fmt --all -- --check
	cargo clippy --locked --all-targets -- -D warnings

test:
	cargo test --locked
	cargo build --locked
	python3 -B -m unittest discover -s tests -p 'test_*.py' -v
	bash tests/test-cli-install.sh

install: build
	cargo install --locked --path .
