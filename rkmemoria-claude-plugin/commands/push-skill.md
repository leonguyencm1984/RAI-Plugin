# /rkm:push-skill

Push a locally edited skill bundle back to the platform.

## Usage

```
/rkm:push-skill <slug> [--on-conflict replace|rename|skip]
```

## Steps

1. Parse arguments: `slug` (required), `--on-conflict` (default: `replace`).
2. Verify `.rkm/skills/<slug>/` exists and contains `skill.yaml`.
3. Call `rkm_push_skill` with `slug` and `on_conflict`.
4. Display the result:
   ```
   Pushed skill '<slug>' → version <version_no> created on platform.
   ```
5. Update `.rkm/skills/<slug>/.rkm-etag` with the new `updated_at` from the response.

## Notes

- The platform creates a new `SkillVersion` on every push.
- Other team members will see the new version in the platform UI immediately.
- Use `--on-conflict rename` if you want to create a new skill instead of updating the existing one.
