# Host compatibility and installation

The skill directory containing this document is the single canonical source for
the implementation. Do not maintain separate copies of `SKILL.md`, `scripts/`,
`references/`, or `assets/` for individual agent hosts.

## Supported host locations

| Host | Managed user location | Notes |
| --- | --- | --- |
| Codex | `$CODEX_HOME/skills/pola-wechat-public-account-reader` when `CODEX_HOME` is set; otherwise `~/.codex/skills/pola-wechat-public-account-reader` | The installer manages exactly one of these locations. `.agents/skills` remains an Agent Skills-compatible project/user alternative, but is not also installed by this script. |
| Claude Code | `~/.claude/skills/pola-wechat-public-account-reader` | Claude Code can also load the same directory from a plugin `skills/` subtree. |
| Qoder | `~/.qoder/skills/pola-wechat-public-account-reader` | Project-local `.qoder/skills` is available when repository-scoped installation is preferred. |
| QoderWork | `~/.qoderwork/skills/pola-wechat-public-account-reader` | The documented standalone location is user-scoped. Expert Kits can distribute the same skill under `skills/`. |

The installer creates absolute symbolic links to this canonical directory. Codex
and Claude Code document skill-directory symlink support. Qoder and QoderWork
should be smoke-tested after installation because their public documentation does
not guarantee symlink discovery on every runtime and platform.

## Read-only check

Running the script without `--apply` performs only `lstat`, `readlink`, and path
resolution operations. It does not create parent directories, links, or files.

```bash
python3 scripts/install_hosts.py
```

Limit a check to one or more hosts by repeating `--host`:

```bash
python3 scripts/install_hosts.py --host codex --host claude-code
```

For an isolated check or test, `--home` relocates every managed destination and
takes precedence over `CODEX_HOME`:

```bash
python3 scripts/install_hosts.py --home /tmp/pola-host-check
```

## Explicit installation

No installation occurs unless `--apply` is present:

```bash
python3 scripts/install_hosts.py --apply
```

The command preflights every selected target before writing. If any target is a
real directory, a regular file, a valid symlink to another location, an
unapproved broken symlink, or cannot be inspected safely, the whole invocation
is refused and no selected target is changed. Use `--host` to install an
independent subset when another host already has a deliberately managed target.

The state rules are:

| Existing state at the exact managed target | Default/check behavior | `--apply` behavior |
| --- | --- | --- |
| Missing | Report `install` required | Create parent directories and one symlink |
| Valid symlink resolving to the canonical skill | Report ready | No-op |
| Real directory or regular file | Refuse | Refuse without overwrite |
| Valid symlink resolving elsewhere | Refuse | Refuse without overwrite |
| Broken symlink | Refuse unless repair is requested | Repair only with both `--apply --repair-broken` |

To repair a broken symlink at the exact skill target:

```bash
python3 scripts/install_hosts.py \
  --host qoder-work \
  --apply \
  --repair-broken
```

The repair operation does not scan the host skill directory and never changes
broken links with other names. A valid link to a different source is never
treated as repairable.

## JSON result and exit codes

Every normal invocation prints one JSON object. It includes the canonical
`source`, effective `home`, `mode`, overall `status`, aggregate `changed` flag,
and one result per selected host. Each host result records `target`, observed
`state`, `planned_action`, actual `action`, and whether it changed.

| Exit code | Meaning |
| --- | --- |
| `0` | All selected targets are ready, or the requested apply completed |
| `1` | A read-only check found missing or explicitly repairable targets |
| `2` | An existing target or changed-after-preflight target was refused |
| `3` | Source validation, inspection, or filesystem operation failed |
| `64` | Invalid command-line arguments |

The implementation uses only the Python standard library and never copies the
skill source. For packaged distribution, generate host-specific archives from a
single staging tree with `skills/pola-wechat-public-account-reader/`, adding only
the thin `.codex-plugin`, `.claude-plugin`, or `.qoder-plugin` manifest required
by the destination. Generated archives are build outputs, not maintained source
copies.
