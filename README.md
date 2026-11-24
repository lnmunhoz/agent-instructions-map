# Agent Instructions Map

See exactly what Claude Code loads as instructions in every folder you've used it in.

For each project it shows, in load order:

- managed, user and project `CLAUDE.md` / `AGENTS.md` files, with their `@imports` expanded
- `.claude/rules` (always-on and path-scoped)
- auto memory (`MEMORY.md`)
- SessionStart hooks, output style, and files that load later (subfolder instructions)
- skills, slash commands, subagents and plugins, with the tokens their descriptions cost

It also flags problems: files over 200 lines, imports past the 4-hop limit, imports outside the project, and `AGENTS.md` files Claude Code never reads.

## Run it

```sh
git clone <this repo> && cd agent-instructions-map
./serve.sh
```

Needs Python 3.8+ and nothing else. It reads `~/.claude` (or `$CLAUDE_CONFIG_DIR`), writes `out/data.json`, and serves the map on http://127.0.0.1:8765 (set `PORT` to change it). Everything stays on your machine.

Or hand it to your agent: "Run the map in this repo and tell me what's bloating my context." `AGENTS.md` tells it what to do.

## Grouping skills

Copy `skill-tags.example.json` to `skill-tags.json` to give categories to skills you installed but didn't write. Skills you own can set `metadata: {category, origin}` in their `SKILL.md` frontmatter instead.

## Privacy

`out/data.json` contains the text of your instruction files. Common secret patterns are redacted, but treat it as private. `out/` and `skill-tags.json` are git-ignored.

## Limits

- Token counts are estimates (characters ÷ 3.7). Run `/context` in a live session for real numbers.
- Text injected by hooks isn't known until the hook runs, so hooks are listed, not measured.
- Projects are found from session logs, so folders you've never opened Claude Code in won't show up.

## License

MIT
