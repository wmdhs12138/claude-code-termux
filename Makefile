ifeq ($(wildcard /data/data/com.termux/files/usr/bin/bash),)
SHELL := /bin/bash
else
SHELL := /data/data/com.termux/files/usr/bin/bash
endif
ROOT  := $(CURDIR)
VERSION ?= latest

.PHONY: build update fetch verify smoke install uninstall clean refresh-base distclean fingerprint

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
	rm -f work/claude-graph.bin dist/claude

# The same number CI puts in `toolchain-v<N>-<fingerprint>`: tracked files only,
# so a stray __pycache__ cannot make it disagree with the published tag.
fingerprint:
	@git ls-files -z scripts tools | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-7

# The base is a rolling canary tag, but work/ caches it, so a plain build never
# notices that the tag moved. This drops the cache and rebuilds against the
# current one -- the way to find out whether a new canary still grafts.
refresh-base:
	rm -rf work/bun-android work/bun-android.zip
	bash scripts/build.sh $(VERSION)

# work/ holds ~1 GB of caches (official binaries, base Bun, graphs) and dist/
# the 225 MB artifact. `make build` recreates both; the downloaded binaries and
# the base are re-fetched.
distclean:
	rm -rf work dist
