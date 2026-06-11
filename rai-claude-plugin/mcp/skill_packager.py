"""
skill_packager.py — build a .rai skill archive from a SKILL.md directory.

Shared pack/sign helpers are imported by server.py; the CLI is the engine
behind the /rai:build-skill command.

CLI usage:
    python3 skill_packager.py <path/to/SKILL.md> [--output PATH]
                              [--runtime prompt|script|hybrid] [--force-yaml]
"""
from __future__ import annotations

import fnmatch
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Pack / sign helpers (shared with server.py)
# ---------------------------------------------------------------------------

def _load_ignore_patterns(skill_dir: Path) -> list[str]:
    """Read .rkmpackignore from the skill directory and return glob patterns."""
    ignore_file = skill_dir / ".rkmpackignore"
    if not ignore_file.exists():
        return []
    lines = ignore_file.read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _should_ignore(rel_path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(Path(rel_path).name, p)
        for p in patterns
    )


def _pack_skill(skill_dir: Path) -> bytes:
    """Build a zip from the skill directory, honoring .rkmpackignore."""
    patterns = _load_ignore_patterns(skill_dir)
    always_skip = {".rkm-etag", ".rkmpackignore"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(skill_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(skill_dir))
            if f.name in always_skip:
                continue
            if _should_ignore(rel, patterns):
                continue
            zf.write(f, rel)
    return buf.getvalue()


def _sign_bundle(zip_bytes: bytes, slug: str) -> bytes:
    """Sign zip_bytes with ed25519 key from ~/.rkm/keys/{slug}.ed25519 and embed signature."""
    key_path = Path.home() / ".rkm" / "keys" / f"{slug}.ed25519"
    if not key_path.exists():
        return zip_bytes  # No key — return unsigned

    try:
        from nacl.signing import SigningKey
        private_key = SigningKey(bytes.fromhex(key_path.read_text().strip()))
        sig = private_key.sign(zip_bytes).signature

        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as src, \
             zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            dst.writestr("signature.ed25519", sig)
        return buf.getvalue()
    except Exception as exc:
        print(f"[rkm] Warning: signing failed ({exc}), building unsigned", file=sys.stderr)
        return zip_bytes


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    """Parse the leading ---...--- YAML frontmatter from a SKILL.md file.

    Handles simple scalar ``key: value`` lines only — no nested YAML.
    Returns an empty dict when no frontmatter block is present.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            break
        m = re.match(r'^(\w[\w-]*):\s*(.*)', line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            fm[key] = val
        i += 1
    return fm


# ---------------------------------------------------------------------------
# skill.yaml generator
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert arbitrary text to a valid slug matching ^[a-z0-9][a-z0-9-]*$."""
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or "skill"


def _yaml_str(val: str) -> str:
    """Quote a YAML string value when it contains special characters."""
    if any(c in val for c in (':', '#', '"', "'", '\n', ',')):
        escaped = val.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return val


def generate_skill_yaml(
    skill_dir: Path,
    fm: dict,
    runtime: Optional[str] = None,
    kb_contract: Optional[dict] = None,
) -> str:
    """Build a minimal valid skill.yaml manifest from SKILL.md frontmatter.

    Args:
        skill_dir: The skill directory (used as slug fallback).
        fm:        Parsed frontmatter dict from SKILL.md.
        runtime:   Explicit runtime override (prompt|script|hybrid).
    """
    raw_name = fm.get("name", skill_dir.name)
    slug = _slugify(raw_name)
    if not re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
        slug = _slugify(skill_dir.name)

    name = fm.get("name", slug)
    description = fm.get("description", "")
    version = fm.get("version", "1.0.0")

    if runtime:
        rt = runtime
    elif (skill_dir / "scripts").is_dir():
        rt = "prompt"
        print(
            "[rkm] Warning: a scripts/ directory exists but --runtime was not specified. "
            "Defaulting to runtime: prompt. Re-run with --runtime script if this is a "
            "script/hybrid skill.",
            file=sys.stderr,
        )
    else:
        rt = "prompt"

    lines = [
        "schema_version: 1",
        f"slug: {slug}",
        f"name: {_yaml_str(name)}",
    ]
    if description:
        lines.append(f"description: {_yaml_str(description)}")
    lines += [
        f"version: {version}",
        "tags: []",
        "visibility: project",
        f"runtime: {rt}",
        "server_executable: false",
        "variables: []",
    ]
    if kb_contract is not None:
        ki = kb_contract.get("kb_input", {})
        ko = kb_contract.get("kb_output", {})
        lines += [
            "kb_input_contract:",
            f"  enabled: {str(ki.get('enabled', False)).lower()}",
            f"  retrieval: {ki.get('retrieval', 'none')}",
            "kb_output_contract:",
            f"  enabled: {str(ko.get('enabled', False)).lower()}",
            f"  target: {ko.get('target', 'wiki')}",
            f"  namespace: \"{ko.get('namespace', '')}\"",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _read_slug_from_yaml(yaml_path: Path) -> str:
    """Extract the ``slug`` value from a skill.yaml file."""
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r'^slug:\s*(\S+)', line)
        if m:
            return m.group(1)
    return _slugify(yaml_path.parent.name)


def _is_signed(zip_bytes: bytes) -> bool:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return "signature.ed25519" in zf.namelist()


def _validate_archive(zip_bytes: bytes) -> None:
    """Validate required files are present and warn on potential secrets."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()

        missing = [f for f in ("skill.yaml", "SKILL.md") if f not in names]
        if missing:
            print(
                f"Error: archive is missing required files: {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Warn on suspicious filenames
        secret_name_patterns = {"*.env", ".env*", "credentials*", "*.pem", "*.key"}
        for name in names:
            if any(fnmatch.fnmatch(Path(name).name, p) for p in secret_name_patterns):
                print(
                    f"[rkm] WARNING: potentially sensitive file in archive: {name}",
                    file=sys.stderr,
                )

        # Warn on secret-looking content in text files
        suspect_re = re.compile(
            r'(password|secret|api_key|access_token|private_key)\s*[=:]\s*\S',
            re.IGNORECASE,
        )
        text_exts = {".md", ".yaml", ".yml", ".py", ".txt", ".sh", ".env",
                     ".cfg", ".ini", ".toml"}
        for name in names:
            if Path(name).suffix.lower() in text_exts:
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    if suspect_re.search(content):
                        print(
                            f"[rkm] WARNING: possible credentials in {name} — "
                            "review before uploading.",
                            file=sys.stderr,
                        )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Public build API
# ---------------------------------------------------------------------------

def build(
    skill_md_path: "str | Path",
    output: "Optional[str | Path]" = None,
    runtime: Optional[str] = None,
    force_yaml: bool = False,
) -> Path:
    """Build a .rai archive from a skill directory.

    Args:
        skill_md_path: Path to the SKILL.md file.
        output:        Destination path for the .rai file.
                       Defaults to ``<slug>.rai`` alongside the skill directory.
        runtime:       Override runtime field in a generated skill.yaml.
        force_yaml:    Re-generate skill.yaml even when one already exists.

    Returns:
        Path to the written .rai file.
    """
    skill_md_path = Path(skill_md_path).resolve()

    if not skill_md_path.exists():
        print(f"Error: SKILL.md not found at {skill_md_path}", file=sys.stderr)
        sys.exit(1)
    if skill_md_path.name != "SKILL.md":
        print(
            f"Error: expected a file named SKILL.md, got: {skill_md_path.name}",
            file=sys.stderr,
        )
        sys.exit(1)

    skill_dir = skill_md_path.parent
    yaml_path = skill_dir / "skill.yaml"

    # Step 1: resolve or generate skill.yaml
    if yaml_path.exists() and not force_yaml:
        slug = _read_slug_from_yaml(yaml_path)
        print(f"[rkm] Using existing skill.yaml (slug: {slug})")
    else:
        fm = parse_frontmatter(skill_md_path.read_text(encoding="utf-8"))
        yaml_content = generate_skill_yaml(skill_dir, fm, runtime=runtime)
        action = "Regenerated" if yaml_path.exists() else "Generated"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        slug = _read_slug_from_yaml(yaml_path)
        print(f"[rkm] {action} skill.yaml (slug: {slug})")

    # Step 2: pack + sign
    zip_bytes = _sign_bundle(_pack_skill(skill_dir), slug)
    signed = _is_signed(zip_bytes)

    # Step 3: validate contents and warn on secrets
    _validate_archive(zip_bytes)

    # Step 4: write output
    out_path = Path(output).resolve() if output else skill_dir.parent / f"{slug}.rai"
    out_path.write_bytes(zip_bytes)

    # Step 5: report
    print(f"\n[rkm] Built:  {out_path}")
    print(f"      Signed: {'yes' if signed else f'no  (place key at ~/.rkm/keys/{slug}.ed25519 to enable)'}")
    print("\nArchive contents:")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            print(f"  {info.filename:<45}  {info.file_size:>8} bytes")

    print("\nNext steps:")
    print("  Upload via UI:   Skills → Import → drop the .rai file")
    print(
        f"  Dry-run upload:  curl -X POST $BASE/api/v1/skills/import/dry-run "
        f"-H 'Authorization: Bearer $TOKEN' -F 'file=@{out_path.name}'"
    )
    print("  Push via plugin: /rai:push-skill <slug>  (after /rai:pull-skills)")

    return out_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="skill_packager",
        description=(
            "Build a .rai skill archive from a SKILL.md directory.\n\n"
            "Zips the entire skill directory (SKILL.md, skill.yaml, scripts/, etc.),\n"
            "honoring .rkmpackignore. Generates skill.yaml from SKILL.md frontmatter\n"
            "when none is present."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "skill_md",
        metavar="SKILL.md",
        help="Path to the SKILL.md file (e.g. skills/my-skill/SKILL.md)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="PATH",
        help="Destination path for the .rai file (default: <slug>.rai next to the skill directory)",
    )
    parser.add_argument(
        "--runtime",
        choices=["prompt", "script", "hybrid"],
        help="Force the runtime field when generating skill.yaml",
    )
    parser.add_argument(
        "--force-yaml",
        action="store_true",
        help="Re-generate skill.yaml even when one already exists",
    )
    args = parser.parse_args()
    build(
        args.skill_md,
        output=args.output,
        runtime=args.runtime,
        force_yaml=args.force_yaml,
    )


if __name__ == "__main__":
    main()
