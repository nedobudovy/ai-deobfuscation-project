# ==========================================
# OLLVM build matrix for the deobfuscation study
# Compiles every program in source_files/ (and
# obfuscated_source_files/) into build_<passes>/
# and build_obfuscated_<passes>/ using clang +
# the llvm-pass-hikari plugin (policy.json driven).
#
# Usage:
#   make NO_OBFUSCATION=1   # plain pair: build_plain, build_obfuscated_plain
#   make FLA=1              # + control flow flattening
#   make SUB=1              # + instruction substitution
#   make BCF=1              # + bogus control flow
#   make SPLIT=1            # + basic block splitting
#   make ALL=1              # fla + sub + bcf at once
#   make FLA=1 BCF=1        # any combination
#
# Overridable on the command line / via env:
#   make CC=/path/to/clang PLUGIN=/path/to/Obfuscator.dylib FLA=1
# ==========================================

# 1. Compiler and plugin
# NOTE: hard assignment on purpose — make's built-in CC=cc would win over
# `?=` and the OLLVM plugin (built for LLVM's plugin API) would be rejected.
# A command-line override still works: make CC=/path/to/clang
CC     = /opt/homebrew/opt/llvm/bin/clang
LLVM_HOME ?= /opt/homebrew/opt/llvm
PLUGIN ?= $(abspath llvm-pass-hikari/obfuscator/build/Obfuscator.dylib)

SRC_DIR  = source_files
OBF_DIR  = obfuscated_source_files

SRCS     = $(wildcard $(SRC_DIR)/*.c)
OBF_SRCS = $(wildcard $(OBF_DIR)/*.c)

# 2. SDK and library paths (macOS)
MAC_SDK = $(shell xcrun --show-sdk-path 2>/dev/null)
CFLAGS = -O0 -Wall -Wno-error=incompatible-function-pointer-types \
         -isysroot $(MAC_SDK) -I/opt/homebrew/include
LDFLAGS = -L/opt/homebrew/lib -lSDL2

# 3. Obfuscation passes (set via make flags)
#    FLA=1          control flow flattening
#    SUB=1          instruction substitution
#    BCF=1          bogus control flow
#    SPLIT=1        basic block splitting
#    ALL=1          all passes
#    NO_OBFUSCATION=1  compile everything as-is, no plugin at all

BUILD_SUFFIX =
ifdef NO_OBFUSCATION
  BUILD_SUFFIX := _plain
else
  ifdef ALL
    BUILD_SUFFIX := _all
  else
    ifdef FLA
      BUILD_SUFFIX := $(BUILD_SUFFIX)_fla
    endif
    ifdef SUB
      BUILD_SUFFIX := $(BUILD_SUFFIX)_sub
    endif
    ifdef BCF
      BUILD_SUFFIX := $(BUILD_SUFFIX)_bcf
    endif
    ifdef SPLIT
      BUILD_SUFFIX := $(BUILD_SUFFIX)_split
    endif
  endif
endif

# 4. Output directories
ifeq ($(BUILD_SUFFIX),)
  BUILD_DIR     = build_standard
  OBF_BUILD_DIR = build_obfuscated_standard
else ifeq ($(BUILD_SUFFIX),_plain)
  BUILD_DIR     = build_plain
  OBF_BUILD_DIR = build_obfuscated_plain
else
  BUILD_DIR     = build$(BUILD_SUFFIX)
  OBF_BUILD_DIR = build_obfuscated$(BUILD_SUFFIX)
endif

# 5. Plugin flags — all suppressed when NO_OBFUSCATION=1
ifdef NO_OBFUSCATION
  PLUGIN_FLAG     =
  OBF_PLUGIN_FLAG =
else
  ifneq ($(BUILD_SUFFIX),)
    PLUGIN_FLAG = -fpass-plugin=$(PLUGIN)
  else
    PLUGIN_FLAG =
  endif
  OBF_PLUGIN_FLAG = -fpass-plugin=$(PLUGIN)
endif

# Targets
TARGETS     = $(patsubst $(SRC_DIR)/%.c,     $(BUILD_DIR)/%, $(SRCS))
OBF_TARGETS = $(patsubst $(OBF_DIR)/%.c, $(OBF_BUILD_DIR)/%, $(OBF_SRCS))

# ==========================================
# Build Rules
# ==========================================
.PHONY: all clean help policy plugin-check

all: plugin-check policy $(TARGETS) $(OBF_TARGETS)
	@echo "========================================"
	@echo "Build complete!"
	@echo "  Standard binaries : $(BUILD_DIR)/"
	@echo "  Obfuscated binaries: $(OBF_BUILD_DIR)/"
	@echo "========================================"

# Fail fast if an obfuscated build is requested without the plugin —
# proceeding would silently produce non-obfuscated binaries.
plugin-check:
	@if [ -n "$(PLUGIN_FLAG)" ] && [ ! -f "$(PLUGIN)" ]; then \
		echo "ERROR: plugin not found: $(PLUGIN)"; \
		echo "       Run ./setup.sh to fetch and build llvm-pass-hikari."; \
		exit 1; \
	fi

# Standard sources
$(BUILD_DIR)/%: $(SRC_DIR)/%.c | $(BUILD_DIR)
	@echo "Building $@ ..."
	-cd $(BUILD_DIR) && $(CC) $(CFLAGS) $(PLUGIN_FLAG) $(abspath $<) -o $(notdir $@) $(LDFLAGS)

# Obfuscated sources
$(OBF_BUILD_DIR)/%: $(OBF_DIR)/%.c | $(OBF_BUILD_DIR)
	@echo "Building (obfuscated) $@ ..."
	-cd $(OBF_BUILD_DIR) && $(CC) $(CFLAGS) $(OBF_PLUGIN_FLAG) $(abspath $<) -o $(notdir $@) $(LDFLAGS)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(OBF_BUILD_DIR):
	mkdir -p $(OBF_BUILD_DIR)

# Generate policy.json — skipped entirely when NO_OBFUSCATION=1
policy: | $(BUILD_DIR) $(OBF_BUILD_DIR)
ifdef NO_OBFUSCATION
	@echo "NO_OBFUSCATION=1 — skipping policy.json generation"
else
ifneq ($(BUILD_SUFFIX),)
	-@python3 gen_policy.py --src-dir $(abspath $(SRC_DIR)) --out $(BUILD_DIR)/policy.json \
		$(if $(ALL),--fla --sub --bcf) \
		$(if $(FLA),--fla) $(if $(SUB),--sub) $(if $(BCF),--bcf) $(if $(SPLIT),--split)
	@echo "Generated $(BUILD_DIR)/policy.json"
else
	@echo "No obfuscation passes — skipping $(BUILD_DIR)/policy.json"
endif
	@if [ -d "$(OBF_DIR)" ]; then \
		python3 gen_policy.py --src-dir $(abspath $(OBF_DIR)) --out $(OBF_BUILD_DIR)/policy.json \
			$(if $(ALL),--fla --sub --bcf) \
			$(if $(FLA),--fla) $(if $(SUB),--sub) $(if $(BCF),--bcf) $(if $(SPLIT),--split) \
			$(if $(BUILD_SUFFIX),,--fla --sub --bcf) && \
		echo "Generated $(OBF_BUILD_DIR)/policy.json"; \
	else \
		echo "Skipping $(OBF_BUILD_DIR)/policy.json — $(OBF_DIR)/ not found (run ./obfuscate_source_code.sh first)"; \
	fi
endif

clean:
	@echo "Cleaning up all build directories..."
	-rm -rf build_*

help:
	@echo "Usage:"
	@echo "  make                  # standard build; obfuscated_source_files always use OLLVM"
	@echo "  make FLA=1            # + control flow flattening for source_files"
	@echo "  make SUB=1            # + instruction substitution for source_files"
	@echo "  make BCF=1            # + bogus control flow for source_files"
	@echo "  make SPLIT=1          # + basic block splitting for source_files"
	@echo "  make ALL=1            # all passes for source_files"
	@echo "  make FLA=1 BCF=1      # combine passes freely for source_files"
	@echo "  make NO_OBFUSCATION=1 # compile everything as-is, no plugin at all"
	@echo "  make clean            # remove all build directories"
	@echo "  make help             # this help"
	@echo ""
	@echo "Output:"
	@echo "  build_standard/            <- source_files, no obfuscation"
	@echo "  build_obfuscated_standard/ <- obfuscated_source_files, always OLLVM"
	@echo "  build_<passes>/            <- source_files with selected passes"
	@echo "  build_obfuscated_<passes>/ <- obfuscated_source_files with selected passes"
	@echo "  build_plain/               <- source_files, NO_OBFUSCATION=1"
	@echo "  build_obfuscated_plain/    <- obfuscated_source_files, NO_OBFUSCATION=1"
