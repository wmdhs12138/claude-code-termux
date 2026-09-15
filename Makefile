ifeq ($(wildcard /data/data/com.termux/files/usr/bin/bash),)
SHELL := /bin/bash
else
SHELL := /data/data/com.termux/files/usr/bin/bash
endif
ROOT  := $(CURDIR)
VERSION ?= latest

.PHONY: build update fetch verify smoke test install uninstall clean refresh-base distclean fingerprint

build:
	bash scripts/build.sh $(VERSION)

update:
	bash scripts/update.sh

fetch:
	bash scripts/fetch-claude.sh $(VERSION)

verify:
	./dist/claude --version

smoke:
	python3 tools/tui_smoke.py ./dist/claude 7

test:
	python3 -m unittest discover -s tests -v

# The launcher template substitutes @ROOT@ through sed, so escape the path
# first: an '&' or '|' in it would otherwise silently produce a launcher that
# points somewhere else.
install:
	mkdir -p "$(HOME)/bin"
	ROOT_ESC=$$(printf '%s' '$(ROOT)' | sed 's/[&|\\]/\\&/g'); \
	  sed "s|@ROOT@|$$ROOT_ESC|g" scripts/launcher.sh > "$(HOME)/bin/claude"
	chmod +x "$(HOME)/bin/claude"
	@echo "installed: $(HOME)/bin/claude"

uninstall:
	rm -f "$(HOME)/bin/claude"

clean:
	flock -n .build.lock -c 'rm -f work/claude-graph.bin dist/claude'

# The same number CI puts in `toolchain-v<N>-<fingerprint>`: tracked files only,
# so a stray __pycache__ cannot make it disagree with the published tag. The set
# below must match the workflow's TC_PATHS; if the two ever drift, this target
# stops reproducing the published tag, which is how the drift gets noticed.
fingerprint:
	@git ls-files -z scripts tools .github | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-7

# The default base comes from an immutable mirrored release, so plain builds
# stay reproducible. To evaluate another upstream Bun, pass BUN_URL explicitly;
# refresh downloads and promotes it only after every build check passes.
refresh-base:
	REFRESH_BASE=1 bash scripts/build.sh $(VERSION)

# work/ holds ~1 GB of caches (official binaries, base Bun, graphs) and dist/
# the 225 MB artifact. `make build` recreates both; the downloaded binaries and
# the base are re-fetched.
distclean:
	flock -n .build.lock -c 'rm -rf work dist'
