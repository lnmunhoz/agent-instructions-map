# Agent Instructions Map

A local, read-only map of what Claude Code loads as instructions in each folder you've used it in: CLAUDE.md and AGENTS.md files, rules, @imports, auto memory, hooks, skills, commands, subagents and plugins, in load order with token estimates.

## If a user points you at this repo

Run it for them. No install step, no dependencies beyond Python 3.8+ (standard library only).

1. `./serve.sh` scans `~/.claude` (or `$CLAUDE_CONFIG_DIR`) and opens the map at http://127.0.0.1:8765/. Set `PORT` to change the port. On Windows or without `sh`, run `python3 build.py`, copy `index.html` into `out/`, and serve `out/` with `python3 -m http.server`.
2. Open the page yourself if you can and tell the user what stands out: the heaviest projects by tokens, warnings (files over 200 lines, imports past the 4-hop limit, AGENTS.md files that never load) and skills with no category.
3. If they want their skills grouped, copy `skill-tags.example.json` to `skill-tags.json` and fill it in from the skill names in `out/data.json`.

## How it works

- `build.py` finds projects from session logs in `~/.claude/projects/*/*.jsonl` (the `cwd` field), works out the load order for each per the [Claude Code memory docs](https://code.claude.com/docs/en/memory), and writes `out/data.json`.
- `index.html` is a static page that reads `out/data.json`. No build step, no framework.
- Secrets matching common token patterns are replaced with `[redacted]` before anything is written.

## Rules for changing it

- Read only. The tool never writes outside `out/`.
- Keep it standard-library Python and one static HTML file.
- `out/` and `skill-tags.json` hold the user's private data and are git-ignored. Never commit them or paste their contents anywhere.
- When Claude Code changes how it loads instructions, update the load-order notes at the top of `build.py` with the docs date and Claude Code version you checked.
