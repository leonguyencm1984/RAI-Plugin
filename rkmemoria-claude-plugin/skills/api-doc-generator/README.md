# adelie-api-doc

A Claude Code skill that generates API documentation from Java source code and verifies documentation against the running ADELIE backend.

## Features

**Mode 1 — Generate** reads Java controller, service, DTO, and mapper files for a given screen ID (e.g., `P601`) and produces a markdown API documentation file matching the project's established format.

**Mode 2 — Verify** runs the project's `verify_api_docs.py` script against the live backend and summarizes pass/fail results for every documented endpoint.

## Installation

```bash
# From the project root
.claude/skills/adelie-api-doc/install.sh

# Or with --force to overwrite an existing installation
.claude/skills/adelie-api-doc/install.sh --force
```

The installer creates a symlink from `~/.claude/skills/adelie-api-doc` to this directory, so updates to the source files take effect immediately without reinstalling.

## Usage

### Generate documentation

```
/adelie-api-doc generate P601
/adelie-api-doc generate P010_1
/adelie-api-doc generate P601 P602 P603
```

This reads the backend source at `ADELIE-be/Adelie/biz-modules/biz-standard/src/main/java/jp/co/adelie/biz/standard/activities/{ID}/` and writes the output to `documents/Adelie_Sanyu_{ID}_API_Documentation_EN.md`.

### Verify documentation

```
# Parse-only (no backend required)
/adelie-api-doc verify --dry-run

# Verify a single screen
/adelie-api-doc verify --file P601

# Full live verification (read-only endpoints)
/adelie-api-doc verify

# Include write endpoints (test environment only)
/adelie-api-doc verify --execute-writes

# Resume an interrupted run
/adelie-api-doc verify --resume
```

Reports are written to `scripts/reports/{ID}_verification.md` with an aggregate summary at `scripts/reports/SUMMARY.md`.

### Generate then verify

```
/adelie-api-doc generate P701
/adelie-api-doc verify --file P701
```

## Prerequisites

| Requirement | Generate | Verify (dry-run) | Verify (live) |
|-------------|----------|-------------------|---------------|
| Claude Code | Yes | Yes | Yes |
| Python 3 | No | Yes | Yes |
| `requests` module | No | No | Yes |
| Running backend (port 8080) | No | No | Yes |

## Verification Status Codes

| Status | Meaning |
|--------|---------|
| PASS | HTTP < 500 and response `code` < 400 |
| PASS_WARN | API reachable, business-level error from test payload |
| FAIL | HTTP 5xx server error |
| TIMEOUT | Request exceeded 15 s |
| ERROR | Connection reset or parse error |
| UNREACHABLE | Cannot connect to backend |

## File Structure

```
.claude/skills/adelie-api-doc/
  SKILL.md      — Skill definition (loaded by Claude Code)
  README.md     — This file
  install.sh    — Installer script
```

## Version

1.0.0
