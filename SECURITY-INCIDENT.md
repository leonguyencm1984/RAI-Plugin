# SECURITY INCIDENT RESPONSE

**Severity:** CRITICAL — P1  
**Date Detected:** 2026-06-05  
**Status:** OPEN — Requires Immediate Human Action  

---

## WHAT WAS LEAKED

The following secrets were committed to git in plaintext and must be treated as fully compromised:

| Secret | File | Partial Value |
|--------|------|---------------|
| GitHub PAT | `credential.md` | `github_pat_11BPXE66Q04...f7f` |
| Platform MCP token | `credential.md` | `1Jh4fiN...ViMo` |
| AUTH_PASSWORD | `rkmemoria-claude-plugin/skills/api-doc-generator/scripts/.env` | `Aaaa2222` |
| AES_KEY | `rkmemoria-claude-plugin/skills/api-doc-generator/scripts/.env` | `01234hyxvue56789` |

**The GitHub PAT is particularly dangerous:** the `.env` file has `EXECUTE_WRITES=true`, meaning
scripts authenticated with these credentials had write access and may have modified data.

---

## STOP — READ THIS BEFORE ROTATING TOKENS

> **CRITICAL DEPLOYMENT PREREQUISITE**
>
> `server.py:679` contains a key-name bug in the streaming auth path:
>
> ```python
> # BROKEN (current) — looks up wrong key, sends empty Bearer token on all SSE streams
> token = cfg.get("mcp_token", "")
>
> # FIXED — must match the key written by the login flow (server.py:664)
> token = cfg.get("token", "")
> ```
>
> **If you rotate the MCP token before deploying this fix, every plugin user's streaming
> calls (`rkm_run_skill_streaming`) will receive HTTP 401 immediately.**
>
> **Required order:**
> 1. Deploy the `cfg["token"]` fix to server.py  
> 2. Coordinate with plugin users to pull/restart their MCP server  
> 3. THEN rotate the MCP token (Section 3 below)

---

## SECTION 1 — IMMEDIATE ACTIONS (Do Right Now)

- [ ] **Do NOT push any new commits** that include `credential.md` or `.env` files — stop spreading exposure
- [ ] **Assume both tokens have been exfiltrated** — any git history that touched these files is potentially public
- [ ] **Do NOT delete `credential.md` from git yet** — the BFG/history-rewrite step is in Section 5; deleting the file alone does not remove it from history
- [ ] Open GitHub audit log immediately (before rotating the PAT) to capture evidence: https://github.com/settings/security-log
- [ ] Notify anyone with repo access that a credential rotation is in progress

---

## SECTION 2 — REVOKE THE GITHUB PAT

### 2a. Revoke via GitHub Web UI

1. Go to: https://github.com/settings/tokens  
   (Fine-grained PATs: https://github.com/settings/tokens?type=beta)
2. Find the token starting with `github_pat_11BPXE66Q04BGK...`
3. Click **Delete** / **Revoke** — this takes effect immediately
4. Confirm deletion in the dialog

### 2b. Rotate — Generate a Replacement

1. On the same page click **Generate new token (fine-grained)**
2. Set an expiration date (recommend: 90 days max)
3. Scope the token to only the repositories that actually need it
4. Grant only the minimum permissions required (remove write scopes unless explicitly needed)
5. Save the new token to a secrets manager (1Password, GitHub Secrets, etc.) — **never commit to git**

### 2c. Update All Consumers

- Replace the PAT in any CI/CD secrets (GitHub Actions → Settings → Secrets and variables → Actions)
- Update `.github/workflows/` environment references if hardcoded
- Check if the PAT was used in any local `~/.gitconfig` credential helpers:
  ```bash
  git config --global --list | grep -i token
  cat ~/.netrc | grep github
  ```

---

## SECTION 3 — REVOKE THE PLATFORM MCP TOKEN

### 3a. Via Platform Admin API

```bash
# List current tokens to find the one to revoke
curl -s http://localhost:8081/api/v1/mcp-auth/tokens \
  -H "Authorization: Bearer <your-admin-token>"

# Revoke the compromised token (replace TOKEN_ID with the ID from the list)
curl -s -X DELETE http://localhost:8081/api/v1/mcp-auth/tokens/<TOKEN_ID> \
  -H "Authorization: Bearer <your-admin-token>"
```

### 3b. Via Platform Admin UI (if available)

1. Navigate to: http://localhost:8081/ (admin panel)
2. Go to MCP Auth → Token Management
3. Find the token matching `1Jh4fiN...ViMo`
4. Click Revoke

### 3c. Issue a New MCP Token

```bash
curl -s -X POST http://localhost:8081/api/v1/mcp-auth/tokens \
  -H "Authorization: Bearer <your-admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"description": "rkmemoria-claude-plugin replacement token", "project": "Sanyu-Adelie-Be"}'
```

Save the new token to `~/.rkm/config.json` on each machine that runs the plugin:

```json
{
  "platform_url": "http://localhost:8081",
  "token": "<NEW_TOKEN_HERE>"
}
```

### 3d. BEFORE ROTATING — Deploy the Streaming Fix

Fix `server.py` line 679:

```python
# Change this:
token = cfg.get("mcp_token", "")
# To this:
token = cfg.get("token", "")
```

Restart/reload the MCP server on every connected Claude instance before proceeding to revoke.

---

## SECTION 4 — FORENSIC AUDIT CHECKLIST

### 4a. GitHub Audit Log

URL: https://github.com/settings/security-log  
For org repos: https://github.com/organizations/YOUR_ORG/settings/audit-log

Look for:
- [ ] Any events associated with the PAT `github_pat_11BPXE66Q04BGK...` after its creation date
- [ ] `repo.push`, `repo.create`, `repo.delete`, `repo.transfer` events — the PAT had write-capable env (`EXECUTE_WRITES=true`)
- [ ] `gists.create` events (PATs can create/edit gists)
- [ ] `org.add_member`, `org.remove_member`, `team.*` events if the PAT had org scope
- [ ] Any access from unexpected IP addresses or user agents
- [ ] Export the full audit log for the period since the PAT was created:
  ```bash
  # Via GitHub API (replace ORG and TOKEN)
  curl -H "Authorization: Bearer <ADMIN_PAT>" \
    "https://api.github.com/orgs/YOUR_ORG/audit-log?per_page=100&phrase=created:>2026-01-01" \
    | jq '.'
  ```

### 4b. What the PAT Could Have Done (EXECUTE_WRITES=true)

Given `EXECUTE_WRITES=true` in the api-doc-generator `.env`, the scripts that ran with these
credentials could have performed:

- **Git pushes** to any repo the PAT had access to (force-pushes, branch creation/deletion)
- **Repository content modifications** (commits via API: `PUT /repos/:owner/:repo/contents/:path`)
- **GitHub Actions secrets updates** if the PAT had `secrets` scope
- **Issue/PR creation, editing, closing** if the PAT had `issues`/`pull_requests` scope
- **Webhook creation** (potential for data exfiltration via outbound webhooks)
- **Platform data writes** via the MCP token (`POST /api/v1/skill-runs`, project mutations)

Check the api-doc-generator scripts for what write operations they actually invoke:
```bash
grep -r "EXECUTE_WRITES\|PUT\|POST\|DELETE\|push\|commit" \
  /Users/mac/Documents/RAI-Platform/RAI-Plugin/rkmemoria-claude-plugin/skills/api-doc-generator/scripts/
```

### 4c. Platform DB / AppConfig Audit

Check if the leaked MCP token was used to modify project data:

```bash
# Query platform API for recent runs under project Sanyu-Adelie-Be
curl -s "http://localhost:8081/api/v1/skill-runs?project=Sanyu-Adelie-Be&limit=100" \
  -H "Authorization: Bearer <admin-token>" | jq '.[] | {id, skill, status, created_at, caller_token}'

# Check AppConfig mutations
curl -s "http://localhost:8081/api/v1/projects/Sanyu-Adelie-Be/config/history" \
  -H "Authorization: Bearer <admin-token>" | jq '.'
```

Look for:
- [ ] Skill runs you did not initiate
- [ ] Config key changes (especially any keys containing secrets or API endpoints)
- [ ] Any new project members or permission grants
- [ ] Runs with `EXECUTE_WRITES=true` that wrote back to the platform

### 4d. AES Key Exposure

The key `01234hyxvue56789` was committed. Determine what data it encrypts:

- [ ] Search for all files that import or reference this key:
  ```bash
  grep -r "AES_KEY\|01234hyxvue56789\|aes\|decrypt\|encrypt" \
    /Users/mac/Documents/RAI-Platform/RAI-Plugin/rkmemoria-claude-plugin/skills/api-doc-generator/scripts/
  ```
- [ ] Any data encrypted with this key must be considered plaintext-compromised
- [ ] Re-encrypt affected data with a new key after rotation

---

## SECTION 5 — REMOVE SECRETS FROM GIT HISTORY

> **Do this AFTER all tokens are rotated and verified working.**

### 5a. Remove credential.md from history

```bash
# Using BFG Repo Cleaner (recommended — faster than git filter-branch)
# Download: https://rtyley.github.io/bfg-repo-cleaner/
java -jar bfg.jar --delete-files credential.md /path/to/RAI-Plugin.git

# Or with git filter-repo (pip install git-filter-repo)
cd /Users/mac/Documents/RAI-Platform/RAI-Plugin
git filter-repo --invert-paths --path credential.md

# Clean and force-push (coordinate with all repo users first)
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push --force-with-lease origin main
```

### 5b. Remove .env from history

```bash
git filter-repo --invert-paths \
  --path rkmemoria-claude-plugin/skills/api-doc-generator/scripts/.env
```

### 5c. Add to .gitignore permanently

Ensure these are in `.gitignore`:
```
credential.md
.env
*.env
**/secrets/**
**/.rkm/config.json
```

### 5d. Notify all collaborators

After force-push, all collaborators must re-clone or run:
```bash
git fetch --all && git reset --hard origin/main
```

---

## SECTION 6 — POST-ROTATION VERIFICATION

Run these checks after all rotations are complete:

```bash
# 1. Verify old PAT is dead
curl -H "Authorization: Bearer github_pat_11BPXE66Q04BGKsj6NelXh_<...>" \
  https://api.github.com/user
# Expected: 401 Unauthorized

# 2. Verify new PAT works
curl -H "Authorization: Bearer <NEW_GITHUB_PAT>" https://api.github.com/user
# Expected: 200 with your user info

# 3. Verify old MCP token is dead
curl http://localhost:8081/api/v1/mcp-auth/verify \
  -H "Authorization: Bearer 1Jh4fiNmLd72PTrzkBrZETcgnDU2LvhU_DTOtKrViMo"
# Expected: 401 Unauthorized

# 4. Verify new MCP token works
curl http://localhost:8081/api/v1/mcp-auth/verify \
  -H "Authorization: Bearer <NEW_MCP_TOKEN>"
# Expected: 200

# 5. Verify streaming fix is deployed — check server.py line ~679
grep -n "cfg.get" /Users/mac/Documents/RAI-Platform/RAI-Plugin/rkmemoria-claude-plugin/mcp/server.py | grep token
# Expected: cfg.get("token", "") — NOT cfg.get("mcp_token", "")

# 6. Smoke test streaming end-to-end
# Via Claude + MCP: ask Claude to run a skill that uses rkm_run_skill_streaming
# Confirm no 401 errors in server stderr
```

---

## SECTION 7 — COORDINATION RUNBOOK (Summary Order)

| Step | Action | Owner | Blocker? |
|------|--------|-------|----------|
| 1 | Open GitHub audit log and export evidence | Dev | No |
| 2 | Fix `cfg["mcp_token"]` → `cfg["token"]` in server.py | Dev | YES — blocks token rotation |
| 3 | Deploy server.py fix to all plugin users | Dev + Users | YES — blocks token rotation |
| 4 | Revoke GitHub PAT at https://github.com/settings/tokens | Dev | No — do immediately |
| 5 | Issue new GitHub PAT with minimal scope | Dev | After step 4 |
| 6 | Update GitHub PAT in all CI/CD secrets | Dev | After step 5 |
| 7 | Revoke MCP token via admin API | Admin | After step 3 |
| 8 | Issue new MCP token and distribute | Admin | After step 7 |
| 9 | Rotate AUTH_PASSWORD and AES_KEY | Dev | Coordinate with dependent services |
| 10 | Purge secrets from git history (BFG/filter-repo) | Dev | After all tokens rotated |
| 11 | Force-push and notify collaborators | Dev | After step 10 |
| 12 | Run post-rotation verification checklist | Dev + Admin | After steps 4–9 |

---

## CONTACTS / ESCALATION

- Platform admin panel: http://localhost:8081/
- GitHub security advisories: https://github.com/advisories
- GitHub support (compromised token): https://support.github.com/contact
- If this is a public repo or the leak window is > 24h, consider filing a GitHub Security Advisory

---

*This document was generated as part of the T1 security hardening sprint. Do not commit this file to git until all secrets in Section "WHAT WAS LEAKED" have been rotated and purged from history.*
