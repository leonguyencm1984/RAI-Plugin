#!/usr/bin/env python3
"""
ADELIE API Documentation Verification Script

Parses API documentation markdown files, extracts endpoints,
optionally calls them against a running backend, and generates
verification reports.

Usage:
    # Doc-parse only (no backend needed)
    python verify_api_docs.py --dry-run

    # Verify a single doc
    python verify_api_docs.py --file Adelie_Sanyu_P701_API_Documentation_EN.md

    # Full verification against running BE (read-only endpoints)
    python verify_api_docs.py

    # Full verification including write endpoints (test env only!)
    python verify_api_docs.py --execute-writes

    # Resume from where previous run stopped
    python verify_api_docs.py --resume

    # Clear previous progress and start fresh
    python verify_api_docs.py --fresh
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import base64
import warnings

try:
    import requests
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from Crypto.Cipher import AES as AES_Cipher
except ImportError:
    AES_Cipher = None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Endpoint:
    """Represents a single API endpoint extracted from documentation."""
    function_number: str
    http_method: str
    url_path: str
    description: str = ""
    content_type: str = "application/json"
    request_body_example: Optional[str] = None
    response_example: Optional[str] = None
    is_write_operation: bool = False
    is_multipart: bool = False


@dataclass
class VerificationResult:
    """Result of verifying a single endpoint."""
    endpoint: Endpoint
    status: str = "SKIP"  # PASS, FAIL, SKIP, ERROR, UNREACHABLE
    http_status: Optional[int] = None
    response_time_ms: Optional[float] = None
    response_body: Optional[str] = None
    response_structure_match: bool = False
    notes: str = ""
    request_url: str = ""
    request_method: str = ""
    request_headers: Optional[dict] = None
    request_body_sent: Optional[str] = None
    response_headers: Optional[dict] = None
    response_content_type: str = ""
    response_code_field: Optional[int] = None
    response_msg_field: str = ""
    response_data_keys: Optional[list] = None


@dataclass
class DocReport:
    """Verification report for a single documentation file."""
    doc_file: str
    screen_id: str
    screen_name: str = ""
    total_endpoints: int = 0
    endpoints: list = field(default_factory=list)
    results: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config:
    def __init__(self):
        if load_dotenv is not None:
            env_path = Path(__file__).parent / ".env"
            if env_path.exists():
                load_dotenv(env_path)

        self.base_url = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
        self.auth_token = os.getenv("AUTH_TOKEN", "")
        self.auth_username = os.getenv("AUTH_USERNAME", "3")
        _auth_password = os.getenv("AUTH_PASSWORD")
        if not _auth_password:
            raise ValueError(
                "AUTH_PASSWORD env var is required — set it in scripts/.env before running"
            )
        self.auth_password = _auth_password
        self.login_endpoint = os.getenv("LOGIN_ENDPOINT", "/user/login")
        _aes_key = os.getenv("AES_KEY")
        if not _aes_key:
            raise ValueError(
                "AES_KEY env var is required — set it in scripts/.env before running"
            )
        self.aes_key = _aes_key
        self.timeout = int(os.getenv("TIMEOUT", "15"))
        self.verify_ssl = os.getenv("VERIFY_SSL", "false").lower() == "true"
        self.docs_dir = os.getenv("DOCS_DIR", "../documents")
        self.report_output_dir = os.getenv("REPORT_OUTPUT_DIR", "./reports")
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        self.execute_writes = os.getenv("EXECUTE_WRITES", "false").lower() == "true"
        self.verbose = os.getenv("VERBOSE", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Documentation Parser
# ---------------------------------------------------------------------------

# Regex: heading like "#### 1.1 POST /p701/init"
RE_ENDPOINT_HEADING_H4 = re.compile(
    r"^####\s+(\d+\.\d+)\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
    re.MULTILINE,
)

# Regex: heading like "## 1. POST /p501/getList — Description"
RE_ENDPOINT_HEADING_H2 = re.compile(
    r"^##\s+(\d+)\.\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
    re.MULTILINE,
)

# Regex: heading like "### API" followed by ```http block with method + path
RE_API_SECTION = re.compile(
    r"^###\s+APIs?\s*$",
    re.MULTILINE,
)

# Regex: "**Request Body** (application/json -- P701QueryDto):"
RE_REQUEST_BODY = re.compile(
    r"\*\*Request\s+Body\*\*\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Regex: extract JSON from ```json ... ``` blocks
RE_JSON_BLOCK = re.compile(
    r"```json\s*\n([\s\S]*?)\n```",
)

# Regex: extract HTTP from ```http ... ``` blocks
RE_HTTP_BLOCK = re.compile(
    r"```http\s*\n([\s\S]*?)\n```",
)

# Regex: function heading like "## Function 1: ..." or "## 1. ..."
RE_FUNCTION_HEADING = re.compile(
    r"^##\s+(?:Function\s+)?(\d+)[:.]\s*(.+)",
    re.MULTILINE,
)

# Regex: screen ID from filename like Adelie_Sanyu_P701_API_Documentation_EN.md
RE_SCREEN_ID = re.compile(r"Adelie_Sanyu_(P\d+)_API_Documentation")

# Regex: multipart indicator
RE_MULTIPART = re.compile(
    r"multipart/form-data|@RequestPart|MultipartFile",
    re.IGNORECASE,
)


def parse_doc_file(filepath: str) -> DocReport:
    """Parse a single API documentation markdown file."""
    path = Path(filepath)
    screen_match = RE_SCREEN_ID.search(path.name)
    screen_id = screen_match.group(1) if screen_match else path.stem

    report = DocReport(
        doc_file=path.name,
        screen_id=screen_id,
    )

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.parse_errors.append(f"Cannot read file: {exc}")
        return report

    # Extract screen name from first heading
    first_heading = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    if first_heading:
        report.screen_name = first_heading.group(1).strip()

    # Find all endpoint headings — try h4 format first, then h2 format
    endpoints_found = list(RE_ENDPOINT_HEADING_H4.finditer(content))

    if not endpoints_found:
        # Try h2 format: "## 1. POST /p501/getList — Description"
        endpoints_found = list(RE_ENDPOINT_HEADING_H2.finditer(content))

    if not endpoints_found:
        # Fallback 1: try to find endpoints in ```http blocks
        for http_match in RE_HTTP_BLOCK.finditer(content):
            block = http_match.group(1)
            method_line = block.strip().split("\n")[0]
            parts = method_line.split(maxsplit=1)
            if len(parts) == 2 and parts[0] in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                ep = Endpoint(
                    function_number="?",
                    http_method=parts[0],
                    url_path=parts[1].split("?")[0].split(" ")[0],
                    is_write_operation=parts[0] in ("POST", "PUT", "PATCH", "DELETE"),
                )
                report.endpoints.append(ep)

        # Fallback 2: find bare "METHOD /path" lines in request examples
        if not report.endpoints:
            seen_paths: set[str] = set()
            bare_re = re.compile(
                r"^(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}]+)",
                re.MULTILINE,
            )
            for m in bare_re.finditer(content):
                url = m.group(2).split("?")[0]
                key = f"{m.group(1)} {url}"
                if key not in seen_paths:
                    seen_paths.add(key)
                    report.endpoints.append(Endpoint(
                        function_number="?",
                        http_method=m.group(1),
                        url_path=url,
                        is_write_operation=m.group(1) in ("POST", "PUT", "PATCH", "DELETE"),
                    ))

        if not report.endpoints:
            report.parse_errors.append("No endpoint headings found in any format")

        report.total_endpoints = len(report.endpoints)
        return report

    for i, match in enumerate(endpoints_found):
        func_num = match.group(1)
        method = match.group(2)
        url_path = match.group(3)

        # Extract section content between this heading and the next
        start = match.end()
        end = endpoints_found[i + 1].start() if i + 1 < len(endpoints_found) else len(content)
        section = content[start:end]

        # Determine content type
        content_type = "application/json"
        is_multipart = bool(RE_MULTIPART.search(section))
        if is_multipart:
            content_type = "multipart/form-data"
        else:
            body_match = RE_REQUEST_BODY.search(section)
            if body_match:
                ct_text = body_match.group(1).split("—")[0].strip().split("--")[0].strip()
                if ct_text:
                    content_type = ct_text

        # Extract request example
        request_example = None
        http_blocks = RE_HTTP_BLOCK.findall(section)
        if http_blocks:
            request_example = http_blocks[0]

        # Extract first response example
        response_example = None
        json_blocks = RE_JSON_BLOCK.findall(section)
        if json_blocks:
            response_example = json_blocks[0]

        # Determine description from nearby text
        desc = ""
        desc_match = re.search(r"\*\*Description\*\*:\s*(.+)", section)
        if desc_match:
            desc = desc_match.group(1).strip()

        ep = Endpoint(
            function_number=func_num,
            http_method=method,
            url_path=url_path,
            description=desc,
            content_type=content_type,
            request_body_example=request_example,
            response_example=response_example,
            is_write_operation=method in ("POST", "PUT", "PATCH", "DELETE"),
            is_multipart=is_multipart,
        )
        report.endpoints.append(ep)

    report.total_endpoints = len(report.endpoints)
    return report


# ---------------------------------------------------------------------------
# API Caller
# ---------------------------------------------------------------------------

class APICaller:
    """Handles HTTP requests to the backend."""

    TOKEN_MAX_AGE_SECONDS = 25 * 60  # refresh token after 25 minutes

    def __init__(self, config: Config):
        self.config = config
        self.session = None
        self.token = config.auth_token
        self._token_acquired_at: Optional[float] = None
        self._consecutive_timeouts: int = 0
        self._backend_down: bool = False

    def _new_session(self):
        """Create a fresh requests session with proper adapter config."""
        if requests is None:
            raise RuntimeError(
                "requests library not installed. Run: pip install requests"
            )
        session = requests.Session()
        session.verify = self.config.verify_ssl
        # Limit pool size and disable keep-alive to prevent connection pool blockage
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _reset_session(self):
        """Close and recreate session to clear stuck connections."""
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
        self.session = self._new_session()

    def _check_backend_down(self):
        """After 3 consecutive timeouts, do a health check with recovery wait."""
        if self._consecutive_timeouts >= 3 and not self._backend_down:
            print("  [WARN] 3 consecutive timeouts, waiting for backend recovery...")
            # Wait for the backend to finish processing heavy queries
            time.sleep(5)
            self._reset_session()
            if not self._quick_health_check():
                # Retry once more after a longer wait
                print("  [WARN] First health check failed, retrying after 10s...")
                time.sleep(10)
                self._reset_session()
                if not self._quick_health_check():
                    self._backend_down = True
                    print("  [WARN] Backend unresponsive — skipping remaining endpoints")
                else:
                    print("  [OK]   Backend recovered after retry")
                    self._consecutive_timeouts = 0
            else:
                print("  [OK]   Backend still responsive (endpoints are just slow)")
                self._consecutive_timeouts = 0

    def reset_backend_status(self):
        """Reset backend status for a new document (backend may recover)."""
        self._backend_down = False
        self._consecutive_timeouts = 0

    def _ensure_session(self):
        if self.session is None:
            self.session = self._new_session()

        # Login if no token, or proactively refresh if token is old
        if not self.token:
            self._auto_login()
        elif (
            self._token_acquired_at is not None
            and (time.time() - self._token_acquired_at) > self.TOKEN_MAX_AGE_SECONDS
        ):
            print("  [AUTH] Token expired, refreshing...")
            self.token = ""
            self._auto_login()

    @staticmethod
    def _aes_encrypt(plaintext: str, key: str) -> str:
        """AES/ECB/PKCS5 encrypt and base64-encode."""
        if AES_Cipher is None:
            raise RuntimeError(
                "pycryptodome not installed. Run: pip install pycryptodome"
            )
        key_bytes = key.encode("utf-8")
        pad_len = 16 - len(plaintext) % 16
        padded = plaintext + chr(pad_len) * pad_len
        cipher = AES_Cipher.new(key_bytes, AES_Cipher.MODE_ECB)
        encrypted = cipher.encrypt(padded.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    def _quick_health_check(self) -> bool:
        """Check if backend is responsive (longer timeout to allow recovery)."""
        try:
            self._ensure_session()
            self.session.get(
                f"{self.config.base_url}/",
                timeout=(5, 10),
                allow_redirects=False,
            )
            return True  # Any response means backend is alive
        except Exception:
            return False

    def _auto_login(self):
        """Attempt auto-login to obtain JWT token via Set-Cookie."""
        url = f"{self.config.base_url}{self.config.login_endpoint}"
        encrypted_pw = self._aes_encrypt(
            self.config.auth_password, self.config.aes_key
        )
        payload = {
            "username": self.config.auth_username,
            "password": encrypted_pw,
        }
        # Use shorter timeout for login (10s) to avoid long waits
        login_timeout = min(self.config.timeout, 10)
        try:
            resp = self.session.post(
                url, json=payload, timeout=(5, login_timeout),
                headers={"Connection": "close"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200:
                    # Token is returned in Set-Cookie header
                    cookie_token = resp.cookies.get("token", "")
                    if cookie_token:
                        self.token = cookie_token
                        self._token_acquired_at = time.time()
                        print(f"  [AUTH] Login successful, token obtained from cookie")
                    else:
                        # Fallback: parse Set-Cookie header directly
                        set_cookie = resp.headers.get("Set-Cookie", "")
                        for part in set_cookie.split(";"):
                            part = part.strip()
                            if part.startswith("token="):
                                self.token = part[len("token="):]
                                self._token_acquired_at = time.time()
                                print(f"  [AUTH] Login successful, token obtained from Set-Cookie header")
                                break
                        if not self.token:
                            print(f"  [AUTH] Login OK but no token in cookies or headers")
                else:
                    print(f"  [AUTH] Login failed: {data.get('msg', 'unknown error')}")
            else:
                print(f"  [AUTH] Login failed: HTTP {resp.status_code}")
        except Exception as exc:
            print(f"  [AUTH] Login error: {exc}")

    def _is_auth_failure(self, resp) -> bool:
        """Check if response indicates an authentication failure."""
        if resp.status_code == 401:
            return True
        try:
            body = resp.json()
            if body.get("code") == 401:
                return True
            msg = str(body.get("msg", ""))
            if "認証失敗" in msg or "No login" in msg:
                return True
        except (ValueError, AttributeError):
            pass
        return False

    def call_endpoint(self, endpoint: Endpoint) -> VerificationResult:
        """Call a single endpoint and return verification result."""
        result = VerificationResult(endpoint=endpoint)

        if self.config.dry_run:
            result.status = "SKIP"
            result.notes = "Dry-run mode: HTTP call skipped"
            return result

        if endpoint.is_write_operation and not self.config.execute_writes:
            result.status = "SKIP"
            result.notes = "Write operation skipped (use --execute-writes to enable)"
            return result

        # Skip if backend is confirmed down after consecutive timeouts
        if self._backend_down:
            # Re-check periodically in case backend recovered
            if self._quick_health_check():
                print("  [OK]   Backend recovered, resuming verification")
                self._backend_down = False
                self._consecutive_timeouts = 0
                self._reset_session()
            else:
                result.status = "UNREACHABLE"
                result.notes = "Backend unresponsive (skipped after consecutive timeouts)"
                return result

        self._ensure_session()

        url = f"{self.config.base_url}{endpoint.url_path}"
        headers = {"Connection": "close"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["Cookie"] = f"token={self.token}"

        result.request_url = url
        result.request_method = endpoint.http_method

        # Build request based on method and content type
        kwargs = {
            "headers": headers,
            "timeout": (10, self.config.timeout),  # (connect, read) timeouts
        }

        # Parse request body from example if available
        body = None
        if endpoint.request_body_example and not endpoint.is_multipart:
            body = _extract_json_from_http_block(endpoint.request_body_example)

        if endpoint.http_method == "GET":
            request_func = self.session.get
        elif endpoint.http_method == "POST":
            request_func = self.session.post
            if body:
                kwargs["json"] = body
                headers["Content-Type"] = "application/json"
            elif not endpoint.is_multipart:
                kwargs["json"] = {}
                headers["Content-Type"] = "application/json"
        elif endpoint.http_method == "PUT":
            request_func = self.session.put
            if body:
                kwargs["json"] = body
        elif endpoint.http_method == "DELETE":
            request_func = self.session.delete
        else:
            result.status = "ERROR"
            result.notes = f"Unsupported HTTP method: {endpoint.http_method}"
            return result

        # Capture request body sent
        if body is not None:
            try:
                result.request_body_sent = json.dumps(body, ensure_ascii=False)[:1000]
            except (TypeError, ValueError):
                result.request_body_sent = str(body)[:1000]
        elif endpoint.http_method in ("POST", "PUT", "PATCH"):
            result.request_body_sent = "{}"

        result.request_headers = {
            k: v for k, v in headers.items() if k != "Cookie"
        }

        resp = self._do_request(request_func, url, kwargs, result)
        if resp is None:
            return result

        # Retry once on auth failure (token expired mid-run)
        if self._is_auth_failure(resp):
            print(f"  [AUTH] Token expired, re-authenticating...")
            self.token = ""
            self._auto_login()
            if self.token:
                kwargs["headers"]["Authorization"] = f"Bearer {self.token}"
                kwargs["headers"]["Cookie"] = f"token={self.token}"
                resp = self._do_request(request_func, url, kwargs, result)
                if resp is None:
                    return result

        # Final auth check after retry
        if self._is_auth_failure(resp):
            result.status = "AUTH_FAIL"
            result.notes = "Authentication failed after token refresh"
            return result

        self._process_response(resp, result)
        return result

    def _do_request(self, request_func, url, kwargs, result):
        """Execute HTTP request and populate basic result fields. Returns response or None on error."""
        try:
            start = time.time()
            resp = request_func(url, **kwargs)
            elapsed_ms = (time.time() - start) * 1000

            result.http_status = resp.status_code
            result.response_time_ms = round(elapsed_ms, 1)
            result.response_content_type = resp.headers.get("Content-Type", "")
            result.response_headers = dict(resp.headers)

            try:
                result.response_body = resp.text[:4000]
            except Exception:
                result.response_body = "<binary or unreadable>"

            # Successful response resets timeout counter
            self._consecutive_timeouts = 0
            return resp

        except requests.exceptions.ConnectionError:
            result.status = "UNREACHABLE"
            result.notes = f"Cannot connect to {self.config.base_url}"
            self._consecutive_timeouts += 1
            self._check_backend_down()
            self._reset_session()
            # Recovery delay to let backend settle
            time.sleep(3)
            return None
        except requests.exceptions.Timeout:
            result.status = "TIMEOUT"
            result.notes = f"Request timed out after {self.config.timeout}s"
            self._consecutive_timeouts += 1
            self._check_backend_down()
            self._reset_session()
            # Recovery delay to let backend finish processing heavy queries
            time.sleep(3)
            return None
        except Exception as exc:
            result.status = "ERROR"
            result.notes = f"Request error: {exc}"
            self._consecutive_timeouts += 1
            self._check_backend_down()
            self._reset_session()
            return None

    @staticmethod
    def _process_response(resp, result):
        """Analyze response and set status/structure fields."""
        if resp.status_code < 500:
            result.status = "PASS"
            try:
                resp_json = resp.json()
                result.response_code_field = resp_json.get("code")
                result.response_msg_field = str(resp_json.get("msg", ""))[:200]
                data_val = resp_json.get("data")
                if isinstance(data_val, dict):
                    result.response_data_keys = list(data_val.keys())[:20]
                elif isinstance(data_val, list):
                    result.response_data_keys = [f"list[{len(data_val)} items]"]
                elif data_val is not None:
                    result.response_data_keys = [f"({type(data_val).__name__})"]

                has_code = "code" in resp_json
                has_data_or_msg = "data" in resp_json or "msg" in resp_json
                result.response_structure_match = has_code and has_data_or_msg
                if not result.response_structure_match:
                    result.notes = f"Non-standard envelope. Keys: {list(resp_json.keys())}"
                elif result.response_code_field and result.response_code_field >= 400:
                    # API exists and responds, but business-level error
                    # (e.g. malformed test body, missing required fields)
                    result.status = "PASS_WARN"
                    result.notes = f"API reachable, business error: code={result.response_code_field}"
            except (ValueError, AttributeError):
                result.notes = "Response is not JSON"
                result.response_structure_match = False
        else:
            result.status = "FAIL"
            result.notes = f"Server error: HTTP {resp.status_code}"
            try:
                resp_json = resp.json()
                result.response_code_field = resp_json.get("code")
                result.response_msg_field = str(resp_json.get("msg", ""))[:200]
            except (ValueError, AttributeError):
                pass


def _extract_json_from_http_block(http_block: str) -> Optional[dict]:
    """Extract JSON body from an HTTP request example block."""
    lines = http_block.strip().split("\n")
    body_start = None
    for i, line in enumerate(lines):
        if line.strip() == "" and i > 0:
            body_start = i + 1
            break

    if body_start is not None and body_start < len(lines):
        body_text = "\n".join(lines[body_start:]).strip()
        if body_text:
            try:
                return json.loads(body_text)
            except json.JSONDecodeError:
                return None
    return None


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

_HTTP_STATUS_MAP = {
    200: "OK", 201: "Created", 204: "No Content",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
    415: "Unsupported Media Type", 422: "Unprocessable Entity",
    500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
}


def _http_status_text(code: int) -> str:
    return _HTTP_STATUS_MAP.get(code, "Unknown")


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _pretty_json(text: str) -> str:
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, ensure_ascii=False)[:3000]
    except (json.JSONDecodeError, TypeError):
        return text[:3000]


def generate_report(doc_report: DocReport, config: Config) -> str:
    """Generate a markdown verification report for a single doc."""
    lines = [
        f"# Verification Report: {doc_report.screen_id}",
        f"**Source**: `{doc_report.doc_file}`",
        f"**Screen**: {doc_report.screen_name}",
        f"**Verified**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Mode**: {'Dry-run (parse only)' if config.dry_run else 'Live verification'}",
        f"**Base URL**: `{config.base_url}`",
        "",
        "---",
        "",
        f"## Summary",
        f"- **Total endpoints parsed**: {doc_report.total_endpoints}",
    ]

    if doc_report.parse_errors:
        lines.append(f"- **Parse errors**: {len(doc_report.parse_errors)}")
        for err in doc_report.parse_errors:
            lines.append(f"  - {err}")

    # Count statuses
    status_counts = {}
    for r in doc_report.results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    if status_counts:
        lines.append("")
        lines.append("| Status | Count |")
        lines.append("|--------|-------|")
        for status, count in sorted(status_counts.items()):
            lines.append(f"| {status} | {count} |")

    # Endpoint details table
    lines.extend([
        "",
        "## Endpoints",
        "",
        "| # | Method | Path | Status | HTTP | Time (ms) | Structure | Notes |",
        "|---|--------|------|--------|------|-----------|-----------|-------|",
    ])

    for r in doc_report.results:
        ep = r.endpoint
        http_code = str(r.http_status) if r.http_status else "-"
        resp_time = str(r.response_time_ms) if r.response_time_ms else "-"
        struct = "OK" if r.response_structure_match else "-"
        notes = r.notes[:80] if r.notes else ""
        lines.append(
            f"| {ep.function_number} | {ep.http_method} | `{ep.url_path}` "
            f"| {r.status} | {http_code} | {resp_time} | {struct} | {notes} |"
        )

    # Detailed per-endpoint sections (always generated in live mode, parse-only in dry-run)
    lines.extend([
        "",
        "## Endpoint Details",
        "",
    ])

    for r in doc_report.results:
        ep = r.endpoint
        status_emoji = {
            "PASS": "✅", "PASS_WARN": "⚡", "FAIL": "❌", "SKIP": "⏭️",
            "ERROR": "⚠️", "TIMEOUT": "⏱️", "UNREACHABLE": "🔌", "AUTH_FAIL": "🔒",
        }.get(r.status, "❓")

        lines.extend([
            f"### {status_emoji} {ep.function_number} {ep.http_method} `{ep.url_path}`",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Status** | {r.status} |",
            f"| **Description** | {ep.description or 'N/A'} |",
            f"| **Content-Type** | {ep.content_type} |",
            f"| **Write Operation** | {'Yes' if ep.is_write_operation else 'No'} |",
            f"| **Multipart** | {'Yes' if ep.is_multipart else 'No'} |",
        ])

        if r.request_url:
            lines.append(f"| **Request URL** | `{r.request_url}` |")
        if r.http_status is not None:
            lines.append(f"| **HTTP Status** | {r.http_status} ({_http_status_text(r.http_status)}) |")
        if r.response_time_ms is not None:
            lines.append(f"| **Response Time** | {r.response_time_ms} ms |")
        if r.response_content_type:
            lines.append(f"| **Response Content-Type** | `{r.response_content_type}` |")
        if r.response_code_field is not None:
            lines.append(f"| **Response `code`** | {r.response_code_field} |")
        if r.response_msg_field:
            lines.append(f"| **Response `msg`** | {_escape_md(r.response_msg_field[:120])} |")
        if r.response_data_keys:
            lines.append(f"| **Response `data` keys** | {', '.join(r.response_data_keys)} |")
        lines.append(f"| **Structure Match** | {'✅ Yes' if r.response_structure_match else '❌ No'} |")
        if r.notes:
            lines.append(f"| **Notes** | {_escape_md(r.notes[:200])} |")

        lines.append("")

        # Request body sent
        if r.request_body_sent:
            lines.extend([
                "**Request Body Sent**:",
                "",
                "```json",
                _pretty_json(r.request_body_sent),
                "```",
                "",
            ])

        # Response body (truncated)
        if r.response_body and r.status not in ("SKIP",):
            truncated = r.response_body[:2000]
            lines.extend([
                "**Response Body** (truncated):",
                "",
                "```json",
                _pretty_json(truncated),
                "```",
                "",
            ])

        # Response headers (selected)
        if r.response_headers and r.status not in ("SKIP",):
            selected = {k: v for k, v in r.response_headers.items()
                        if k.lower() in ("content-type", "set-cookie", "x-request-id",
                                          "server", "date", "content-length")}
            if selected:
                lines.append("**Response Headers** (selected):")
                lines.append("")
                for k, v in selected.items():
                    val = v[:120] + "..." if len(v) > 120 else v
                    lines.append(f"- `{k}`: `{val}`")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def generate_summary_report(all_reports: list, config: Config) -> str:
    """Generate a summary report across all documentation files."""

    # --- Compute totals first ---
    total_endpoints = 0
    total_pass = 0
    total_pass_warn = 0
    total_fail = 0
    total_skip = 0
    total_error = 0
    total_timeout = 0
    total_unreachable = 0
    total_parse_err = 0
    all_times: list[float] = []

    per_doc_rows: list[str] = []
    for report in sorted(all_reports, key=lambda r: r.screen_id):
        counts = {
            "PASS": 0, "PASS_WARN": 0, "FAIL": 0, "SKIP": 0,
            "ERROR": 0, "TIMEOUT": 0, "UNREACHABLE": 0, "AUTH_FAIL": 0,
        }
        for r in report.results:
            counts[r.status] = counts.get(r.status, 0) + 1
            if r.response_time_ms is not None:
                all_times.append(r.response_time_ms)

        total_endpoints += report.total_endpoints
        total_pass += counts["PASS"]
        total_pass_warn += counts["PASS_WARN"]
        total_fail += counts["FAIL"]
        total_skip += counts["SKIP"]
        total_error += counts["ERROR"]
        total_timeout += counts["TIMEOUT"]
        total_unreachable += counts["UNREACHABLE"]
        total_parse_err += len(report.parse_errors)

        parse_err = len(report.parse_errors)
        per_doc_rows.append(
            f"| {report.screen_id} | {report.doc_file} | {report.total_endpoints} "
            f"| {counts['PASS']} | {counts['PASS_WARN']} | {counts['FAIL']} | {counts['SKIP']} "
            f"| {counts['ERROR']} | {counts['TIMEOUT']} | {counts['UNREACHABLE']} | {counts['AUTH_FAIL']} | {parse_err} |"
        )

    total_reachable = total_pass + total_pass_warn
    pass_rate = round(total_reachable / total_endpoints * 100, 1) if total_endpoints else 0
    clean_pass_rate = round(total_pass / total_endpoints * 100, 1) if total_endpoints else 0
    avg_time = round(sum(all_times) / len(all_times), 1) if all_times else 0
    max_time = round(max(all_times), 1) if all_times else 0
    min_time = round(min(all_times), 1) if all_times else 0

    # --- Build report ---
    lines = [
        "# API Documentation Verification - Summary Report",
        "",
        "## Purpose",
        "",
        "This report verifies that all API endpoints documented in the ADELIE backend",
        "API documentation files are correctly implemented and reachable on the running",
        "backend server. The script parses each markdown documentation file, extracts",
        "endpoint definitions (method + path), and calls them against the live backend.",
        "",
        "**Verification criteria**:",
        "- **PASS**: API responds with HTTP < 500 and response body `code` < 400 (clean success)",
        "- **PASS_WARN**: API responds with HTTP < 500 but response body `code` >= 400 (API exists, business-level error from test payload)",
        "- **FAIL**: HTTP 5xx server error",
        "- **TIMEOUT**: Request exceeded configured timeout",
        "- **ERROR**: Other request errors (connection reset, parse error)",
        "- **UNREACHABLE**: Cannot connect to backend",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| **Date** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",
        f"| **Mode** | {'Dry-run (parse only)' if config.dry_run else 'Live verification'} |",
        f"| **Base URL** | `{config.base_url}` |",
        f"| **Total Documents** | {len(all_reports)} |",
        f"| **Total Endpoints** | {total_endpoints} |",
        f"| **Passed (clean)** | {total_pass} |",
        f"| **Passed (with warning)** | {total_pass_warn} |",
        f"| **Failed** | {total_fail} |",
        f"| **Timeout** | {total_timeout} |",
        f"| **Errors** | {total_error} |",
        f"| **Unreachable** | {total_unreachable} |",
        f"| **Skipped** | {total_skip} |",
        f"| **Parse Errors** | {total_parse_err} |",
        f"| **Reachable Rate** | {pass_rate}% ({total_reachable}/{total_endpoints}) |",
        f"| **Clean Pass Rate** | {clean_pass_rate}% ({total_pass}/{total_endpoints}) |",
    ]
    if all_times:
        lines.extend([
            f"| **Avg Response Time** | {avg_time} ms |",
            f"| **Min Response Time** | {min_time} ms |",
            f"| **Max Response Time** | {max_time} ms |",
        ])
    lines.extend([
        "",
        "---",
        "",
        "## Results by Document",
        "",
        "| Screen | Doc File | Endpoints | PASS | PASS_WARN | FAIL | SKIP | ERROR | TIMEOUT | UNREACHABLE | AUTH_FAIL | Parse Errors |",
        "|--------|----------|-----------|------|-----------|------|------|-------|---------|-------------|-----------|--------------|",
    ])

    lines.extend(per_doc_rows)

    lines.extend([
        f"| **TOTAL** | | **{total_endpoints}** "
        f"| **{total_pass}** | **{total_pass_warn}** | **{total_fail}** | **{total_skip}** "
        f"| **{total_error}** | **{total_timeout}** | **{total_unreachable}** "
        f"| **{total_endpoints - total_pass - total_pass_warn - total_fail - total_skip - total_error - total_timeout - total_unreachable}** | **{total_parse_err}** |",
        "",
    ])

    # Documents with issues
    problem_docs = [
        r for r in all_reports
        if r.parse_errors or any(res.status in ("FAIL", "ERROR", "TIMEOUT", "AUTH_FAIL") for res in r.results)
    ]
    if problem_docs:
        lines.extend([
            "---",
            "",
            "## Documents with Issues",
            "",
        ])
        for report in sorted(problem_docs, key=lambda r: r.screen_id):
            lines.append(f"### {report.screen_id}")
            if report.parse_errors:
                for err in report.parse_errors:
                    lines.append(f"- Parse error: {err}")
            for r in report.results:
                if r.status in ("FAIL", "ERROR", "TIMEOUT", "AUTH_FAIL"):
                    cross_screen = ""
                    if not r.endpoint.url_path.startswith(f"/{report.screen_id.lower()}"):
                        cross_screen = " (cross-screen endpoint)"
                    lines.append(
                        f"- {r.endpoint.http_method} {r.endpoint.url_path}: "
                        f"{r.status} - {r.notes}{cross_screen}"
                    )
            lines.append("")

    lines.extend([
        "---",
        "",
        f"*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PROGRESS_FILE = ".verify_progress.json"


def _load_progress(output_dir: Path) -> dict:
    """Load resume progress from state file."""
    progress_path = output_dir / PROGRESS_FILE
    if progress_path.exists():
        try:
            return json.loads(progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_progress(output_dir: Path, progress: dict) -> None:
    """Save resume progress to state file."""
    progress_path = output_dir / PROGRESS_FILE
    progress_path.write_text(
        json.dumps(progress, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _clear_progress(output_dir: Path) -> None:
    """Remove the progress state file."""
    progress_path = output_dir / PROGRESS_FILE
    if progress_path.exists():
        progress_path.unlink()


def find_doc_files(docs_dir: str) -> list:
    """Find all API documentation markdown files."""
    docs_path = Path(docs_dir)
    if not docs_path.is_dir():
        print(f"Error: Documentation directory not found: {docs_path}")
        sys.exit(1)

    files = sorted(docs_path.glob("Adelie_Sanyu_P*_API_Documentation_EN.md"))
    return [str(f) for f in files]


def main():
    parser = argparse.ArgumentParser(
        description="Verify ADELIE API documentation against running backend"
    )
    parser.add_argument(
        "--file", "-f",
        help="Verify a single doc file (filename or full path)",
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        default=None,
        help="Parse docs only, skip HTTP calls",
    )
    parser.add_argument(
        "--execute-writes",
        action="store_true",
        default=False,
        help="Execute write operations (POST/PUT/DELETE). USE WITH CAUTION!",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Verbose output",
    )
    parser.add_argument(
        "--docs-dir",
        help="Override documentation directory path",
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Override report output directory",
    )
    parser.add_argument(
        "--resume", "-r",
        action="store_true",
        default=False,
        help="Resume from where previous run stopped (skip completed docs)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        default=False,
        help="Clear previous progress and start fresh",
    )

    args = parser.parse_args()
    config = Config()

    # CLI args override env/config
    if args.dry_run is not None:
        config.dry_run = args.dry_run
    if args.execute_writes:
        config.execute_writes = True
    if args.verbose:
        config.verbose = True
    if args.docs_dir:
        config.docs_dir = args.docs_dir
    if args.output_dir:
        config.report_output_dir = args.output_dir

    # Resolve docs directory
    docs_dir = Path(config.docs_dir)
    if not docs_dir.is_absolute():
        docs_dir = Path(__file__).parent / docs_dir
    config.docs_dir = str(docs_dir)

    # Resolve output directory
    output_dir = Path(config.report_output_dir)
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Handle resume/fresh flags
    if args.fresh:
        _clear_progress(output_dir)

    progress = _load_progress(output_dir) if args.resume else {}
    completed_screens: set[str] = set(progress.get("completed", []))

    print("=" * 60)
    print("ADELIE API Documentation Verification")
    print("=" * 60)
    print(f"  Mode:       {'DRY-RUN (parse only)' if config.dry_run else 'LIVE'}")
    print(f"  Base URL:   {config.base_url}")
    print(f"  Docs dir:   {config.docs_dir}")
    print(f"  Output dir: {output_dir}")
    print(f"  Writes:     {'ENABLED' if config.execute_writes else 'disabled'}")
    if args.resume and completed_screens:
        print(f"  Resume:     skipping {len(completed_screens)} completed doc(s): {', '.join(sorted(completed_screens))}")
    print()

    # Find doc files
    if args.file:
        file_path = args.file
        if not Path(file_path).exists():
            file_path = str(docs_dir / args.file)
        if not Path(file_path).exists():
            print(f"Error: File not found: {args.file}")
            sys.exit(1)
        doc_files = [file_path]
    else:
        doc_files = find_doc_files(str(docs_dir))

    if not doc_files:
        print("No documentation files found.")
        sys.exit(1)

    print(f"Found {len(doc_files)} documentation file(s)")
    print()

    # Initialize API caller
    caller = APICaller(config)

    # Process each doc
    all_reports = []
    skipped_count = 0
    for filepath in doc_files:
        filename = Path(filepath).name

        # Extract screen_id early to check resume state
        screen_match = RE_SCREEN_ID.search(filename)
        screen_id = screen_match.group(1) if screen_match else Path(filepath).stem

        if args.resume and screen_id in completed_screens:
            # Load the existing report for the summary
            existing_report_file = output_dir / f"{screen_id}_verification.md"
            print(f"Skipping (already completed): {filename}", flush=True)
            skipped_count += 1
            # Still parse the doc so it appears in the summary
            doc_report = parse_doc_file(filepath)
            # Mark all endpoints as SKIP with resume note
            for ep in doc_report.endpoints:
                result = VerificationResult(
                    endpoint=ep,
                    status="SKIP",
                    notes="Resumed: previously completed",
                )
                doc_report.results.append(result)
            all_reports.append(doc_report)
            continue

        print(f"Processing: {filename}", flush=True)

        # Reset backend status for each document (backend may recover)
        caller.reset_backend_status()

        # Parse
        doc_report = parse_doc_file(filepath)
        print(f"  Parsed {doc_report.total_endpoints} endpoints", flush=True)

        if doc_report.parse_errors:
            for err in doc_report.parse_errors:
                print(f"  WARNING: {err}")

        # Verify each endpoint
        for i, ep in enumerate(doc_report.endpoints):
            result = caller.call_endpoint(ep)
            doc_report.results.append(result)
            # Small delay between requests to avoid overwhelming the backend
            if i < len(doc_report.endpoints) - 1 and result.status not in ("UNREACHABLE",):
                time.sleep(1.0)

            if config.verbose:
                status_icon = {
                    "PASS": "+", "PASS_WARN": "~", "FAIL": "X", "SKIP": "-",
                    "ERROR": "!", "TIMEOUT": "T", "UNREACHABLE": "?", "AUTH_FAIL": "A",
                }.get(result.status, "?")
                http_code = f" HTTP {result.http_status}" if result.http_status else ""
                resp_time = f" {result.response_time_ms}ms" if result.response_time_ms else ""
                extra = ""
                if result.response_code_field is not None and result.response_code_field != 200:
                    extra = f" code={result.response_code_field}"
                if result.notes and result.status in ("FAIL", "ERROR", "UNREACHABLE"):
                    extra += f" ({result.notes[:60]})"
                print(
                    f"  [{status_icon}] {ep.http_method:6s} {ep.url_path:40s} "
                    f"=> {result.status}{http_code}{resp_time}{extra}",
                    flush=True,
                )

        # Generate per-doc report
        report_content = generate_report(doc_report, config)
        report_file = output_dir / f"{doc_report.screen_id}_verification.md"
        report_file.write_text(report_content, encoding="utf-8")
        print(f"  Report: {report_file.name}")

        all_reports.append(doc_report)

        # Save progress after each completed doc
        completed_screens.add(screen_id)
        _save_progress(output_dir, {
            "completed": sorted(completed_screens),
            "last_updated": datetime.now().isoformat(),
        })

        print()

    if skipped_count:
        print(f"Resumed: {skipped_count} doc(s) skipped (previously completed)")
        print()

    # Generate summary report
    summary = generate_summary_report(all_reports, config)
    summary_file = output_dir / "SUMMARY.md"
    summary_file.write_text(summary, encoding="utf-8")
    print(f"Summary report: {summary_file}")

    # All docs completed — clear progress file so next run starts fresh
    if len(completed_screens) == len(doc_files):
        _clear_progress(output_dir)
        print("All documents verified — progress state cleared.")

    # Print quick summary
    total_eps = sum(r.total_endpoints for r in all_reports)
    status_totals = {
        "PASS": 0, "PASS_WARN": 0, "FAIL": 0, "SKIP": 0,
        "ERROR": 0, "TIMEOUT": 0, "UNREACHABLE": 0, "AUTH_FAIL": 0,
    }
    all_times = []
    for report in all_reports:
        for res in report.results:
            status_totals[res.status] = status_totals.get(res.status, 0) + 1
            if res.response_time_ms is not None:
                all_times.append(res.response_time_ms)
    total_parse_err = sum(len(r.parse_errors) for r in all_reports)

    avg_time = round(sum(all_times) / len(all_times), 1) if all_times else 0
    max_time = round(max(all_times), 1) if all_times else 0
    min_time = round(min(all_times), 1) if all_times else 0

    total_reachable = status_totals['PASS'] + status_totals['PASS_WARN']

    print()
    print("=" * 60)
    print(f"  Documents:   {len(all_reports)}")
    print(f"  Endpoints:   {total_eps}")
    print(f"  PASS:        {status_totals['PASS']}")
    print(f"  PASS_WARN:   {status_totals['PASS_WARN']}")
    print(f"  FAIL:        {status_totals['FAIL']}")
    print(f"  SKIP:        {status_totals['SKIP']}")
    print(f"  ERROR:       {status_totals['ERROR']}")
    print(f"  TIMEOUT:     {status_totals['TIMEOUT']}")
    print(f"  UNREACHABLE: {status_totals['UNREACHABLE']}")
    print(f"  AUTH_FAIL:   {status_totals['AUTH_FAIL']}")
    print(f"  Parse errs:  {total_parse_err}")
    print(f"  Reachable:   {total_reachable}/{total_eps}")
    if all_times:
        print(f"  Avg time:    {avg_time} ms")
        print(f"  Min time:    {min_time} ms")
        print(f"  Max time:    {max_time} ms")
    print("=" * 60)


if __name__ == "__main__":
    main()
