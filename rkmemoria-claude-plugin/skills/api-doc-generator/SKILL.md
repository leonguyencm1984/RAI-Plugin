---
name: api-doc-generator
description: Generates API documentation from Java source code and verifies docs against running backend for the Adelie Sanyu project
version: 5.0.0
risk: safe
source: local
---

# Adelie API Documentation Generator & Verifier

Use this skill when the user asks to generate API documentation for an Adelie screen/feature ID, or to verify existing API documentation against the running backend.

Do NOT use this skill for frontend code, non-API documentation, or general Java development.

## Arguments

### Generate + Verify (Step 1 → Step 2)

- `/adelie-api-doc {SCREEN_ID}` — Single screen (e.g., `/adelie-api-doc P601`)
- `/adelie-api-doc {ID1} {ID2} {ID3}` — Multiple screens
- `/adelie-api-doc all` — **All** activities that have a controller in the backend
- `/adelie-api-doc missing` — Only activities that **don't have docs yet**

### Verify Only (Step 2 only)

- `/adelie-api-doc verify` — Verify all existing docs against backend
- `/adelie-api-doc verify --file {ID}` — Verify single existing doc
- `/adelie-api-doc verify --dry-run` — Parse check only, no backend calls
- `/adelie-api-doc verify --execute-writes` — Include write endpoints (test env only!)
- `/adelie-api-doc verify --resume` — Resume interrupted run
- `/adelie-api-doc verify --fresh` — Clear progress, start fresh

## Workflow

When a screen ID (or `all` / `missing`) is provided, execute **Step 1 → Step 2** sequentially:

1. **Step 1: Generate** — Read Excel API list first (primary source), then read source code for details, produce API doc, write file, **output result immediately**
2. **Step 2: Verify** — Run verification script against generated doc(s), **output result immediately**

When `verify` is the argument (no screen ID), skip to Step 2 only.

### Excel-First Principle

The Excel file is the **primary resource** that defines which APIs belong to each screen. It contains **58 screens** and **359 API endpoints** — this is the authoritative API inventory.

1. **Excel first**: Read the Excel to get the complete list of APIs (method + path + function name) for the target screen
2. **Source code second**: Read Java source files to fill in business logic details, DTO fields, error codes, and response structures
3. **Reconcile**: If source code reveals additional endpoints not in the Excel (e.g., shared/common endpoints), include them but mark them as "discovered from source code"

If a screen ID is **not found in the Excel**, fall back to source-code-only generation and warn the user.

---

## Excel API List (Primary Resource)

The Excel file is the **first and primary resource** for API documentation. It contains the curated, researched list of all APIs per screen from the detail design documents.

### File Location

| Resource   | Path                                                              |
| ---------- | ----------------------------------------------------------------- |
| Excel file | `Adelie-Skills/api-doc-generator/API RESEARCH Block 4 5.xlsx`     |
| Sheet name | `API R4 For Implement`                                            |

All paths relative to `/Users/nguyendt/SanyuAdelie/`.

**Coverage**: 58 screens, 359 API endpoints across Blocks 4 and 5.

### Sheet Structure (Key Columns)

| Column | Header           | Purpose                                              | Example                        |
| ------ | ---------------- | ---------------------------------------------------- | ------------------------------ |
| 1      | No               | Row number                                           | `1`                            |
| 2      | 主管部門 (Block)  | Block designation                                    | `ブロック4`                     |
| 3      | 主管部門 (Dept)   | Department/Team                                      | `01_CSサポート部`               |
| 4      | 機能名・概要      | Screen ID + Japanese name                            | `P008 受注一括変更`             |
| 5      | PDF              | PDF reference marker                                 | —                              |
| 6      | STT              | Sequence number                                      | `1`                            |
| 7      | Tên trong DD     | Function name from detail design doc                 | `表示用データ取得処理(SV)`      |
| 8      | Method           | HTTP method                                          | `GET`, `POST`, `DELETE`        |
| 9      | Path             | API endpoint path                                    | `/p008/getJuchuDenpyoInfo`     |
| 10     | Tên tiếng Nhật   | Japanese backend function name                       | —                              |
| 11     | Ý nghĩa          | Vietnamese description of what the API does          | —                              |
| 12     | Phạm vi gọi      | Scope of call                                        | `[FE-BE]`                      |
| 13     | Phân loại         | Classification                                       | `DD + BE`                      |
| 14     | Ghi chú          | Notes/Comments                                       | —                              |
| 15     | API種別           | API type                                             | `通常API`, `特殊API`            |

### Extracting Screen ID from Column 4

Column 4 contains the screen ID followed by a space and the Japanese name. Extract the ID prefix:

```python
# Example: "P008 受注一括変更" → "P008"
# Example: "P010_1 受注入力（受注）" → "P010_1"
# Note: Some IDs use hyphens in Excel (P010-1) — normalize to underscores (P010_1)
screen_id = cell_value.split(" ")[0].strip().upper().replace("-", "_")
```

### Reading the Excel

```python
import openpyxl

EXCEL_PATH = "/Users/nguyendt/SanyuAdelie/Adelie-Skills/api-doc-generator/API RESEARCH Block 4 5.xlsx"
SHEET_NAME = "API R4 For Implement"

wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
ws = wb[SHEET_NAME]

# Build a dict: screen_id → list of {method, path, function_name, api_type, scope}
api_map = {}
for row in ws.iter_rows(min_row=2, values_only=True):  # skip header
    raw_id = row[3]  # column 4 (0-indexed: 3)
    if not raw_id:
        continue
    screen_id = str(raw_id).split(" ")[0].strip().upper().replace("-", "_")
    method = str(row[7] or "").strip().upper()   # column 8
    path = str(row[8] or "").strip()              # column 9
    func_name = str(row[6] or "").strip()         # column 7
    api_type = str(row[14] or "").strip()         # column 15
    scope = str(row[11] or "").strip()            # column 12
    if not path:
        continue
    api_map.setdefault(screen_id, []).append({
        "method": method,
        "path": path,
        "function": func_name,
        "api_type": api_type,
        "scope": scope
    })
wb.close()
```

### Using Excel Data in Generation

When generating a doc for screen `{ID}`:

1. Look up `api_map[ID]` to get the **definitive list of APIs** for that screen
2. For each API entry, use `method` + `path` to locate the corresponding controller method in source code
3. Use `function` (column 7) as the Japanese function name for the doc heading
4. Use `api_type` to note if an API is 通常API (normal) or 特殊API (special)
5. Use `scope` to understand if the API is FE-BE, BE-only, etc.

If the Excel lists an API but the source code cannot be found for it, document it with the Excel data and mark it:
```
⚠️ API listed in Excel but source code not found — verify implementation status
```

---

## Environment Configuration

The verification script and workflow use environment variables defined in a `.env` file. Both Step 1 (generation) and Step 2 (verification) should read these values to determine output paths and backend connection details.

### `.env` File Location

| Resource | Path                                              |
| -------- | ------------------------------------------------- |
| `.env`   | `Adelie-Skills/api-doc-generator/scripts/.env`    |

All paths relative to `/Users/nguyendt/SanyuAdelie/`.

### Variables Reference

| Variable           | Purpose                                          | Default                    |
| ------------------ | ------------------------------------------------ | -------------------------- |
| `BASE_URL`         | Backend server URL for live verification         | `http://localhost:8080`    |
| `AUTH_TOKEN`       | Pre-set auth token (empty = use login flow)      | *(empty)*                  |
| `AUTH_USERNAME`    | Login username for authentication                | `3`                        |
| `AUTH_PASSWORD`    | Login password for authentication                | `Aaaa2222`                 |
| `LOGIN_ENDPOINT`  | Login API path                                   | `/user/login`              |
| `AES_KEY`         | AES encryption key for password encryption       | `01234hyxvue56789`         |
| `TIMEOUT`         | Request timeout in seconds per endpoint          | `180`                      |
| `VERIFY_SSL`      | Whether to verify SSL certificates               | `false`                    |
| `DOCS_DIR`        | Directory for generated API docs (relative to `scripts/`) | `../documents`    |
| `REPORT_OUTPUT_DIR` | Directory for verification reports (relative to `scripts/`) | `reports`       |
| `DRY_RUN`         | Parse-only mode — no HTTP calls                  | `false`                    |
| `EXECUTE_WRITES`  | Whether to test write endpoints (POST/PUT/DELETE) | `true`                    |
| `VERBOSE`         | Enable verbose output during verification        | `true`                     |

### How Paths Are Resolved

- **`DOCS_DIR`** (`../documents` by default): Resolves to `/Users/nguyendt/SanyuAdelie/documents/` from the scripts directory. This is where Step 1 writes generated docs and where Step 2 reads them for verification.
- **`REPORT_OUTPUT_DIR`** (`reports` by default): Resolves to `/Users/nguyendt/SanyuAdelie/scripts/reports/`. This is where Step 2 writes per-doc and summary verification reports.
- **`BASE_URL`** (`http://localhost:8080` by default): Used by Step 2 for live verification. Step 2.1 health check and all endpoint calls use this URL.

### Reading `.env` in Workflow

Both steps should be aware of these paths:

```python
# Read .env to resolve paths
from dotenv import load_dotenv
import os

ENV_PATH = "/Users/nguyendt/SanyuAdelie/Adelie-Skills/api-doc-generator/scripts/.env"
load_dotenv(ENV_PATH)

DOCS_DIR = os.getenv("DOCS_DIR", "../documents")        # Step 1 output / Step 2 input
REPORT_DIR = os.getenv("REPORT_OUTPUT_DIR", "reports")   # Step 2 output
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080") # Step 2 backend URL
```

---

## Discovering Activities

The activities base directory is:

```
ADELIE-be/Adelie/biz-modules/biz-standard/src/main/java/jp/co/adelie/biz/standard/activities/
```

### For `all` — Generate docs for every activity with a controller

1. List all subdirectories in the activities folder
2. For each subdirectory, check if `{ID}/controller/{ID}Controller.java` exists (case-insensitive)
3. Skip `common/` — it contains shared utilities, not screen-specific APIs
4. Generate docs for each activity that has a controller

```bash
# Discovery command
ACTIVITIES_DIR="/Users/nguyendt/SanyuAdelie/ADELIE-be/Adelie/biz-modules/biz-standard/src/main/java/jp/co/adelie/biz/standard/activities"
for dir in "$ACTIVITIES_DIR"/*/; do
  id=$(basename "$dir")
  [ "$id" = "common" ] && continue
  # Check for controller (case-insensitive folder name)
  if ls "$dir"/controller/*Controller.java 1>/dev/null 2>&1; then
    echo "$id"
  fi
done
```

### For `missing` — Generate docs only for activities without existing docs

1. Run the discovery from `all` above
2. For each discovered activity ID, normalize to uppercase (e.g., `p101` → `P101`)
3. Check if `documents/Adelie_Sanyu_{ID}_API_Documentation_EN.md` already exists
4. Only generate for IDs where the doc file does NOT exist

```bash
# Find activities without docs
ACTIVITIES_DIR="/Users/nguyendt/SanyuAdelie/ADELIE-be/Adelie/biz-modules/biz-standard/src/main/java/jp/co/adelie/biz/standard/activities"
DOCS_DIR="/Users/nguyendt/SanyuAdelie/documents"
for dir in "$ACTIVITIES_DIR"/*/; do
  id=$(basename "$dir")
  [ "$id" = "common" ] && continue
  ID_UPPER=$(echo "$id" | tr '[:lower:]' '[:upper:]')
  if ls "$dir"/controller/*Controller.java 1>/dev/null 2>&1; then
    if [ ! -f "$DOCS_DIR/Adelie_Sanyu_${ID_UPPER}_API_Documentation_EN.md" ]; then
      echo "$ID_UPPER"
    fi
  fi
done
```

### Case-sensitivity note

Some activity folders use lowercase (e.g., `p101`, `p138`, `m999_1`). When generating docs:
- Always normalize the ID to **uppercase** for the output filename: `Adelie_Sanyu_P101_API_Documentation_EN.md`
- Use the **actual folder name** (lowercase) when reading source files
- The controller class name may also be lowercase in the folder but the class itself uses proper casing — read the actual file

### Batch execution strategy

When generating docs for many activities (`all` or `missing`):

1. First run discovery and **output the full list** to the user with count:
   ```
   Found {N} activities to document: P001, P001_1, P002, ...
   ```
2. Ask the user to confirm before proceeding (generating 80+ docs takes significant time)
3. Process each activity sequentially: read Excel API list → read source code → generate doc → output summary
4. After ALL docs are generated, run Step 2 verification on all docs at once
5. Output the combined verification summary

---

# Step 1: Generate API Documentation

## Paths

| Resource           | Path                                                                                                   |
| ------------------ | ------------------------------------------------------------------------------------------------------ |
| Backend activities | `ADELIE-be/Adelie/biz-modules/biz-standard/src/main/java/jp/co/adelie/biz/standard/activities/{ID}/`   |
| Common activities  | `ADELIE-be/Adelie/biz-modules/biz-standard/src/main/java/jp/co/adelie/biz/standard/activities/common/` |
| Output directory   | `documents/` *(configured via `DOCS_DIR` in `.env`)*                                                   |
| Output filename    | `Adelie_Sanyu_{ID}_API_Documentation_EN.md`                                                            |

All paths relative to `/Users/nguyendt/SanyuAdelie/`. The output directory is resolved from the `DOCS_DIR` variable in the [Environment Configuration](#environment-configuration) `.env` file.

## Sub-Screen ID Handling

Some screens have sub-screens with underscore-separated IDs (e.g., `P010_1`, `P010_2`, `P010_3`):

- The activity folder uses the full ID: `activities/P010_1/`
- The controller class is `P010_1Controller.java`
- The output file becomes `Adelie_Sanyu_P010_1_API_Documentation_EN.md`
- If the user provides only the parent ID (e.g., `P010`), check for sub-screen folders and ask if they want to generate docs for sub-screens as well

## 1.1 Read Excel API List (Primary Source)

For the given `{ID}`, first read the Excel to get the definitive API list:

1. Load the Excel file and extract all rows where the screen ID matches `{ID}`
2. Build the list of APIs: method, path, function name, API type
3. This list determines **how many endpoints** the doc will contain and **in what order**

If the screen is found in the Excel, output:
```
📋 Excel API list for {ID}: {N} endpoints found
```

If the screen is NOT found in the Excel, warn and fall back to source-code-only:
```
⚠️ Screen {ID} not found in Excel — falling back to source-code-only generation
```

## 1.2 Read Source Files (Detail Source)

For the given `{ID}`, read these files to fill in business logic details:

1. **Controller**: `{ID}/controller/{ID}Controller.java`
   - Extract: `@Tag(name=...)` for Japanese screen name, `@RequestMapping("/{path}")` for base path
   - For each method: `@Operation(summary="...")`, `@PostMapping("/{endpoint}")` or `@GetMapping(...)`, request DTO class from `@RequestBody`
   - **Cross-check**: Verify each Excel API path matches a controller method

2. **Service interface**: `{ID}/seivice/I{ID}Service.java`
   - Note: folder is `seivice/` (intentional typo in project — do NOT rename it)

3. **Service implementation**: `{ID}/seivice/Impl/{ID}ServiceImpl.java`
   - Extract: business logic flow, error codes (`AppException` with `msgKey`/`msgArgs`), transaction annotations (`@DSTransactional`, `@Transactional`), validation rules
   - Pay attention to try/catch blocks — each `AppException` throw reveals a documented error scenario

4. **Domain DTOs**: `{ID}/domain/*.java`
   - Extract: field names, Java types, annotations (`@NotNull`, `@NotBlank`, etc.)
   - Lombok `@Data` classes — all fields become request/response properties

5. **Mapper**: `{ID}/mapper/{ID}Mapper.java` and `{ID}/mapper/xml/{ID}Mapper.xml`
   - Extract: query structure for understanding data flow and which tables are involved

6. **Common controllers** (if referenced): Check if service calls common modules like `CodeMappingController`, `MConstController`, `M999_2Controller`. These shared endpoints should be documented as separate functions at the end of the doc

## 1.3 Reconcile Excel vs Source Code

After reading both sources, reconcile:

| Situation | Action |
| --------- | ------ |
| API in Excel AND in source code | Document fully (normal case) |
| API in Excel but NOT in source code | Document with Excel data, add warning: `⚠️ Source code not found` |
| API in source code but NOT in Excel | Include as additional endpoint, mark: `(discovered from source code)` |

Output a reconciliation summary:
```
🔄 Reconciliation for {ID}:
   Excel APIs: {N}  |  Source APIs: {M}
   Matched: {X}  |  Excel-only: {Y}  |  Source-only: {Z}
```

### Error Recovery

If an activity folder is not found at the expected path:

1. Try both uppercase and lowercase: `activities/P601/` and `activities/p601/`
2. Search with glob pattern: `activities/*601*/`
3. If still not found, inform the user and list available activity folders for them to choose from

## Japanese-to-English Translation

- Extract the Japanese name from `@Tag(name = "P601_請求締時更新")` on the controller class
- Provide an accurate English translation in parentheses: e.g., "Invoice Closing Period Update"
- For `@Operation(summary = "[明細データ取得処理(SV)]")`, translate each function name and keep the Japanese original alongside
- Common translation patterns in this project:
  - 取得処理 = Retrieval Process
  - 更新処理 = Update Process
  - 登録処理 = Registration Process
  - 削除処理 = Deletion Process
  - 検索処理 = Search Process
  - 一覧取得 = List Retrieval
  - 共通データ取得 = Common Data Retrieval
  - 解除処理 = Cancellation/Reversal Process
  - 印刷処理 = Print Process

## 1.4 Generate Markdown

Follow this **exact** template structure. The output must match the format of existing docs in `documents/`.

**CRITICAL**: Use `#### N.M METHOD /path` (H4) for endpoint headings — this is the format the verification script's primary parser expects.

### Document Header Template

```markdown
# {ID} - {JapaneseName} ({EnglishName}) - API Documentation

**Feature**: {ID} — {JapaneseName} ({EnglishName})
**Classification**: server (backend)
**Structure**: Follows server-side detail design document structure ({ID} Detail Design)
**Created**: {YYYY/MM/DD}
**Base URL**: `http://localhost:8080`

> **Note**: This documentation is organized by **server-side processing functions (SV)** as defined in the backend detail design documents. Each function corresponds to a server-side processing method annotated with `(SV)` in the backend code.

> **Response Format Note**:
>
> - Read APIs (typically GET/lookup): `code` + `data`
> - Write APIs (register/update/delete): `code` + `msg` (often `"success"`)
> - Error responses: `code` (e.g. 500/401) + `msg`

---

## Table of Contents

1. [{ID} Overview](#{id}-overview)
2. [Function 1: {EnglishName}](#function-1-{anchor})
3. [Function 2: {EnglishName}](#function-2-{anchor})
   ...

---

## {ID} Overview

{Description of the screen/feature derived from service logic}

**Key Features**:

- {Feature 1}
- {Feature 2}
  ...

**Total APIs**: {count}
**Total Functions**: {count}

---
```

### Per-Function Template

````markdown
## Function {N}: {EnglishFunctionName} ({JapaneseName}(SV))

**Japanese Name**: {JapaneseName}(SV)
**Backend Method**: `{methodName}()` in `{ID}ServiceImpl.java`
**Controller Annotation**: `@Operation(summary = "[{JapaneseName}(SV)]")`
**Detail Design Reference**: {ID} 詳細設計書

### Business Logic

{Describe the business logic from ServiceImpl: what it does, when used, key rules, validation, error conditions}

**Key Business Rules**:

- {Rule 1}
- {Rule 2}

### APIs

#### {N.1} {HTTP_METHOD} /{basePath}/{endpoint}

**Description**: {What the endpoint does}

**Request Body** (application/json — {DtoClassName}):

| Field   | Type   | Required | Description   |
| ------- | ------ | -------- | ------------- |
| {field} | {Type} | {Yes/No} | {Description} |

**Request Example**:

\```http
{METHOD} /{basePath}/{endpoint}
Authorization: Bearer <token>
Content-Type: application/json

{JSON example with realistic sample data}
\```

**Response Example (Success)**:

\```json
{
  "code": 200,
  "data": {...}
}
\```

**Response Example (Error)**:

\```json
{
  "code": 500,
  "msg": "{error message}",
  "msgKey": "{E-code}",
  "msgArgs": ["{arg}"]
}
\```

**{ResponseDtoName} Response Fields** (when response data is a complex object):

| Field | Type | Description |
| ----- | ---- | ----------- |
| {field} | {Type} | {Description} |

**Edge Cases**:

1. **{Case title}**

   - {Business Logic explanation}
   - **Response**: `{ "code": ..., ... }`

2. **{Another case}**

   - {Explanation}

🔍 SCHEMA VERIFIED — `{DtoClassName}.java`

**Developer Estimate**:

- **Read Detail Design**: {X} minutes
- **Check API in Backend Code**: {Y} minutes
- **Verify with Test Cases**: {Z} minutes
- **Total**: ~{T} minutes

---
````

### Cross-Screen Endpoint Template

When a function references endpoints from another screen (e.g., P702 using P701 endpoints):

```markdown
## Function {N}: {Name} ({JapaneseName}(SV))

**Backend Method**: `{method}()` in `{OtherID}ServiceImpl.java` (cross-screen)
**Controller Annotation**: `@Operation(summary = "...")` ({OtherID}Controller)
**Detail Design Reference**: {OtherID} 詳細設計書 (shared endpoint used by {ID})

### Business Logic

Same as {OtherID} Function {M}. See {OtherID} documentation for full details.

### APIs

#### {N.1} {METHOD} /{otherBasePath}/{endpoint}

See {OtherID} Function {M} (`{METHOD} /{otherBasePath}/{endpoint}`) for full documentation.
```

### Response Envelope Rules

The project uses `ResponseResult` with `@JsonInclude(NON_NULL)`:

| Scenario        | Fields                                                                 |
| --------------- | ---------------------------------------------------------------------- |
| Success (read)  | `{ "code": 200, "data": ... }`                                         |
| Success (write) | `{ "code": 200, "msg": "success" }`                                    |
| AppException    | `{ "code": 500, "msg": "...", "msgKey": "E0100", "msgArgs": ["..."] }` |
| Generic error   | `{ "code": 500, "msg": "...", "msgKey": "E0001", "msgArgs": [""] }`    |

### Type Mapping

| Java Type         | Doc Type   |
| ----------------- | ---------- |
| String            | String     |
| int / Integer     | int        |
| short / Short     | short      |
| long / Long       | long       |
| boolean / Boolean | boolean    |
| BigDecimal        | BigDecimal |
| Date / LocalDate  | Date       |
| List\<X\>         | List\<X\>  |

## 1.5 Write Output and Report

Write the generated markdown to: `documents/Adelie_Sanyu_{ID}_API_Documentation_EN.md`

If the file already exists, ask the user before overwriting.

**Immediately output a summary to the user**:

```
✅ Step 1 Complete: Generated {ID} API Documentation
   Screen: {JapaneseName} ({EnglishName})
   Functions: {N}
   Endpoints: {M}
   Output: documents/Adelie_Sanyu_{ID}_API_Documentation_EN.md
```

Then proceed directly to Step 2.

---

# Step 2: Verify Documentation

This step runs **automatically** after Step 1 completes. It can also be invoked standalone with `/adelie-api-doc verify`.

## Paths

| Resource       | Path                                   | `.env` Variable        |
| -------------- | -------------------------------------- | ---------------------- |
| Script         | `scripts/verify_api_docs.py`           | —                      |
| Docs input     | `scripts/../documents/`                | `DOCS_DIR`             |
| Reports output | `scripts/reports/`                     | `REPORT_OUTPUT_DIR`    |
| Summary report | `scripts/reports/SUMMARY.md`           | `REPORT_OUTPUT_DIR`    |
| Per-doc report | `scripts/reports/{ID}_verification.md` | `REPORT_OUTPUT_DIR`    |

The verification script reads `DOCS_DIR` and `REPORT_OUTPUT_DIR` from the [Environment Configuration](#environment-configuration) `.env` file to resolve input/output paths.

## 2.1 Check Prerequisites

```bash
# Check Python and requests module
cd /Users/nguyendt/SanyuAdelie/scripts
python3 -c "import requests; print('OK')"
```

If `requests` is missing, only dry-run (parse-only) mode works. Warn user.

Check if the backend is running (using `BASE_URL` from `.env`, default `http://localhost:8080`):

```bash
# Read BASE_URL from .env, fallback to default
BASE_URL=$(grep '^BASE_URL=' /Users/nguyendt/SanyuAdelie/Adelie-Skills/api-doc-generator/scripts/.env | cut -d= -f2 | tr -d '[:space:]')
BASE_URL=${BASE_URL:-http://localhost:8080}
curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/actuator/health" 2>/dev/null || echo "Backend not running"
```

> **Note**: The `.env` file also controls `DRY_RUN` (skip HTTP calls) and `EXECUTE_WRITES` (include write endpoints). CLI flags `--dry-run` and `--execute-writes` override these `.env` defaults.

## 2.2 Run Verification

### After generating a single doc (Step 1 → Step 2):

```bash
cd /Users/nguyendt/SanyuAdelie/scripts

# Always run dry-run parse check first (no backend needed)
python3 verify_api_docs.py --dry-run --file Adelie_Sanyu_{ID}_API_Documentation_EN.md

# If backend is running, also run live verification
python3 verify_api_docs.py --file Adelie_Sanyu_{ID}_API_Documentation_EN.md
```

### After generating multiple docs (Step 1 → Step 2):

```bash
cd /Users/nguyendt/SanyuAdelie/scripts

# Dry-run parse check all generated docs
python3 verify_api_docs.py --dry-run

# If backend is running, also run live verification
python3 verify_api_docs.py --fresh
```

### Verify-only mode (no Step 1):

```bash
cd /Users/nguyendt/SanyuAdelie/scripts

# All docs — dry run
python3 verify_api_docs.py --dry-run

# All docs — live
python3 verify_api_docs.py

# Single file — live
python3 verify_api_docs.py --file Adelie_Sanyu_{ID}_API_Documentation_EN.md

# With write endpoints (test env only!)
python3 verify_api_docs.py --execute-writes

# Resume interrupted run
python3 verify_api_docs.py --resume

# Fresh start
python3 verify_api_docs.py --fresh
```

## 2.3 Read and Report Results

After verification completes, read the report and **immediately output results to the user**.

1. Read the report:
   - Single doc: `scripts/reports/{ID}_verification.md`
   - All docs: `scripts/reports/SUMMARY.md`

2. Check that **total endpoints parsed** matches the number of endpoints documented in Step 1

3. **Parse errors** should be 0 — if any exist, the generated doc has formatting issues; fix and re-run

4. **Immediately output a verification summary**:

```
✅ Step 2 Complete: Verification Results for {ID}
   Endpoints parsed: {N}
   Parse errors: 0
   PASS: {X}  |  PASS_WARN: {Y}  |  FAIL: {Z}  |  SKIP: {W}
   Report: scripts/reports/{ID}_verification.md
```

5. Interpret results for the user:
   - **PASS** = endpoint is correctly documented and reachable
   - **PASS_WARN** = expected when using test payloads (API reachable, business-level error from empty/test body). This is normal and does NOT indicate a doc problem
   - **FAIL** = HTTP 5xx server error — likely a doc error (wrong path or method) or a backend bug. List each failing endpoint
   - **TIMEOUT** / **UNREACHABLE** = backend may be down or endpoint not registered. Suggest checking backend status
   - **SKIP** = write operation skipped (use `--execute-writes` to enable) or dry-run mode
   - **AUTH_FAIL** = authentication failure — check credentials in `scripts/.env`

6. If backend is not running, report only the dry-run parse check results and note that live verification was skipped

## Status Code Reference

| Status      | Meaning                                                                                 |
| ----------- | --------------------------------------------------------------------------------------- |
| PASS        | HTTP < 500 AND response `code` < 400                                                    |
| PASS_WARN   | HTTP < 500 BUT response `code` >= 400 (API reachable, business error from test payload) |
| FAIL        | HTTP 5xx server error                                                                   |
| TIMEOUT     | Request exceeded 15s timeout                                                            |
| ERROR       | Connection reset, parse error, etc.                                                     |
| UNREACHABLE | Cannot connect to backend                                                               |
| SKIP        | Write operation skipped or dry-run                                                      |
| AUTH_FAIL   | Authentication failure                                                                  |

---

## Examples

### Generate + verify for a single screen

```
/adelie-api-doc P601
```

→ Step 1: Reads Excel API list for P601, reads source code, reconciles, generates `documents/Adelie_Sanyu_P601_API_Documentation_EN.md`
→ Step 2: Verifies the doc and outputs verification results

### Generate + verify for multiple screens

```
/adelie-api-doc P601 P602 P603
```

→ Step 1: For each screen — reads Excel → reads source → reconciles → generates doc
→ Step 2: Verifies all generated docs and outputs combined results

### Generate + verify ALL activities in the backend

```
/adelie-api-doc all
```

→ Discovers all activities with controllers (~144 folders)
→ Lists them and asks for confirmation
→ Step 1: For each — reads Excel (if available) → reads source → generates doc
→ Step 2: Verifies all generated docs and outputs combined results

### Generate + verify only MISSING docs

```
/adelie-api-doc missing
```

→ Discovers activities that don't have docs yet
→ Lists them and asks for confirmation
→ Step 1: For each — reads Excel (if available) → reads source → generates doc
→ Step 2: Verifies all generated docs and outputs combined results

### Verify all existing docs

```
/adelie-api-doc verify
```

→ Skips Step 1, runs Step 2 on all docs in `documents/`

### Verify single existing doc

```
/adelie-api-doc verify --file P601
```

### Verify with dry-run only (no backend needed)

```
/adelie-api-doc verify --dry-run
```
