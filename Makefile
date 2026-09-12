ifeq ($(wildcard /data/data/com.termux/files/usr/bin/bash),)
SHELL := /bin/bash
else
SHELL := /data/data/com.termux/files/usr/bin/bash
endif
ROOT  := $(CURDIR)
VERSION ?= latest

.PHONY: build update fetch verify smoke install uninstall clean

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

install:
	mkdir -p "$(HOME)/bin"
	sed "s|@ROOT@|$(ROOT)|g" scripts/launcher.sh > "$(HOME)/bin/claude"
	chmod +x "$(HOME)/bin/claude"
	@echo "installed: $(HOME)/bin/claude"

uninstall:
	rm -f "$(HOME)/bin/claude"

clean:
	rm -f work/claude-graph.bin dist/claude
