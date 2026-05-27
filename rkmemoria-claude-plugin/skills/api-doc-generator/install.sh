#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# install.sh — Install the adelie-api-doc skill for Claude Code
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SKILL_NAME="api-doc-generator"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_SKILLS_DIR="$HOME/.claude/skills"
TARGET_DIR="$GLOBAL_SKILLS_DIR/$SKILL_NAME"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  adelie-api-doc — Skill Installer${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Step 1: Validate source files ────────────────────────────
echo -e "${YELLOW}[1/4]${NC} Validating source files..."

if [[ ! -f "$SCRIPT_DIR/SKILL.md" ]]; then
    echo -e "${RED}Error: SKILL.md not found in $SCRIPT_DIR${NC}"
    exit 1
fi

WORD_COUNT=$(wc -w < "$SCRIPT_DIR/SKILL.md")
echo "       SKILL.md found ($WORD_COUNT words)"

if [[ -f "$SCRIPT_DIR/README.md" ]]; then
    echo "       README.md found"
fi

echo -e "       ${GREEN}Source files valid${NC}"
echo ""

# ── Step 2: Check Claude Code installation ───────────────────
echo -e "${YELLOW}[2/4]${NC} Checking Claude Code..."

if [[ ! -d "$HOME/.claude" ]]; then
    echo -e "${RED}Error: ~/.claude directory not found.${NC}"
    echo "       Please install Claude Code first."
    exit 1
fi

echo -e "       ${GREEN}Claude Code detected${NC}"
echo ""

# ── Step 3: Install skill ───────────────────────────────────
echo -e "${YELLOW}[3/4]${NC} Installing skill..."

# Create global skills directory if needed
mkdir -p "$GLOBAL_SKILLS_DIR"

# Handle existing installation
if [[ -e "$TARGET_DIR" ]]; then
    if [[ -L "$TARGET_DIR" ]]; then
        EXISTING_LINK=$(readlink "$TARGET_DIR")
        echo -e "       ${YELLOW}Existing symlink found -> $EXISTING_LINK${NC}"
    else
        echo -e "       ${YELLOW}Existing directory found at $TARGET_DIR${NC}"
    fi

    # Parse --force flag
    if [[ "${1:-}" == "--force" ]]; then
        echo "       --force flag detected, overwriting..."
        rm -rf "$TARGET_DIR"
    else
        read -rp "       Overwrite existing installation? [y/N] " confirm
        if [[ "$confirm" != [yY] ]]; then
            echo -e "       ${YELLOW}Installation cancelled${NC}"
            exit 0
        fi
        rm -rf "$TARGET_DIR"
    fi
fi

# Create symlink (auto-updates when repo is pulled)
ln -sf "$SCRIPT_DIR" "$TARGET_DIR"
echo "       Symlinked: $TARGET_DIR -> $SCRIPT_DIR"
echo -e "       ${GREEN}Skill installed${NC}"
echo ""

# ── Step 4: Verify installation ─────────────────────────────
echo -e "${YELLOW}[4/4]${NC} Verifying installation..."

ERRORS=0

if [[ -L "$TARGET_DIR" ]]; then
    echo -e "       Symlink:  ${GREEN}OK${NC}"
else
    echo -e "       Symlink:  ${RED}FAIL${NC}"
    ERRORS=$((ERRORS + 1))
fi

if [[ -f "$TARGET_DIR/SKILL.md" ]]; then
    echo -e "       SKILL.md: ${GREEN}OK${NC}"
else
    echo -e "       SKILL.md: ${RED}FAIL${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check YAML frontmatter
if head -1 "$TARGET_DIR/SKILL.md" | grep -q "^---"; then
    echo -e "       YAML:     ${GREEN}OK${NC}"
else
    echo -e "       YAML:     ${RED}FAIL${NC}"
    ERRORS=$((ERRORS + 1))
fi

echo ""

if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}  Installation complete!${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Usage:"
    echo "    /api-doc-generator generate P601"
    echo "    /api-doc-generator verify --dry-run"
    echo "    /api-doc-generator verify --file P601"
    echo ""
    echo "  The skill is symlinked, so changes to the"
    echo "  source files update the global install automatically."
    echo ""
else
    echo -e "${RED}Installation completed with $ERRORS error(s).${NC}"
    exit 1
fi
