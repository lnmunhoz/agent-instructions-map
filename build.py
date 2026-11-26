#!/usr/bin/env python3
"""Map what Claude Code loads as instructions, starting from ~/.claude.

For every folder Claude Code has run in (found from ~/.claude/projects/*/*.jsonl),
work out the instruction files a new session there loads, in load order, following
https://code.claude.com/docs/en/memory (checked 2026-11-24, Claude Code 2.1.331):

  1. Managed policy CLAUDE.md
  2. ~/.claude/CLAUDE.md (+ @imports), then ~/.claude/rules/**/*.md without `paths`
  3. Each folder from / down to the working folder: CLAUDE.md, .claude/CLAUDE.md,
     .claude/rules/*.md (no `paths`), CLAUDE.local.md last. AGENTS.md and
     .claude/AGENTS.md instead only when no CLAUDE.md, .claude/CLAUDE.md or
     CLAUDE.local.md exists in the working folder or above (default setting).
  4. Auto memory: ~/.claude/projects/<encoded git root>/memory/MEMORY.md (first 200 lines)
  Plus: SessionStart hooks (inject text we can't see ahead of time), output style,
  and files that load later (subfolder CLAUDE.md/AGENTS.md, path-scoped rules).

Writes out/data.json for index.html. Usage: python3 build.py
Set CLAUDE_CONFIG_DIR to scan a config folder other than ~/.claude.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude").expanduser()
OUT = Path(__file__).parent / "out"
if sys.platform == "darwin":
    MANAGED = Path("/Library/Application Support/ClaudeCode/CLAUDE.md")
elif sys.platform.startswith("win"):
    MANAGED = Path(r"C:\Program Files\ClaudeCode\CLAUDE.md")
else:
    MANAGED = Path("/etc/claude-code/CLAUDE.md")
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".turbo", "vendor",
             ".venv", "venv", "__pycache__", "target", ".cache", "coverage", "out"}
MAX_IMPORT_DEPTH = 4
# Optional labels for skills you didn't write. Copy skill-tags.example.json to
# skill-tags.json (git-ignored) and edit it to group your own skills.
try:
    TAGS = json.loads((Path(__file__).parent / "skill-tags.json").read_text())
except Exception:
    TAGS = {}
FILES = {}  # path -> {text, tokens, lines, mtime}

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[abp]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{30,}\.[A-Za-z0-9_\-]+)")


def est_tokens(text):
    return max(1, round(len(text) / 3.7)) if text else 0


def agent_view(text):
    """Text as the agent gets it: frontmatter and block HTML comments removed."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4:].lstrip("\n")
    out, in_fence = [], False
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence and ln.lstrip().startswith("<!--"):
            # skip until -->
            j = i
            while j < len(lines) and "-->" not in lines[j]:
                j += 1
            i = j + 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def frontmatter_paths(text):
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    fm = text[4:end]
    m = re.search(r"^paths:\s*(.*)$", fm, re.M)
    if not m:
        return None
    inline = m.group(1).strip()
    if inline:
        return [p.strip().strip("\"'") for p in inline.strip("[]").split(",") if p.strip()]
    items = re.findall(r"^\s*-\s*(.+)$", fm[m.end():], re.M)
    return [i.strip().strip("\"'") for i in items]


def read(path):
    p = str(path)
    if p in FILES:
        return FILES[p]
    try:
        raw = Path(p).read_text(errors="replace")[:300_000]
        st = Path(p).stat()
    except Exception:
        return None
    raw = SECRET_RE.sub("[redacted]", raw)
    view = agent_view(raw)
    FILES[p] = {"text": view, "tokens": est_tokens(view), "lines": view.count("\n") + 1,
                "mtime": int(st.st_mtime)}
    return FILES[p]


def find_imports(text):
    """@path imports outside code fences and code spans."""
    found, in_fence = [], False
    for ln in text.split("\n"):
        if ln.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        ln = re.sub(r"`[^`]*`", "", ln)
        for m in re.finditer(r"(?:^|(?<=\s)|(?<=\())@((?:\\ |[^\s`])+)", ln):
            ref = m.group(1).replace("\\ ", " ").rstrip(".,;:)!?\"'")
            if ref:
                found.append(ref)
    return found


def resolve(ref, base_dir):
    if ref.startswith("~/"):
        p = HOME / ref[2:]
    elif ref.startswith("/"):
        p = Path(ref)
    else:
        p = Path(base_dir) / ref
    try:
        p = Path(os.path.normpath(p))
        return p if p.is_file() else None
    except Exception:
        return None


def node(path, kind, scope, root=None, depth=0, seen=None, note=None):
    """A loaded file with its imports expanded (max four hops)."""
    seen = seen if seen is not None else set()
    real = os.path.realpath(path)
    f = read(path)
    n = {"path": str(path), "kind": kind, "scope": scope, "imports": [], "warnings": []}
    if note:
        n["note"] = note
    if real != str(path):
        n["symlink"] = real
    if f is None:
        n["warnings"].append("Couldn't read this file.")
        return n
    if f["lines"] > 200:
        n["warnings"].append(f"{f['lines']} lines; Claude Code suggests under 200.")
    seen.add(real)
    for ref in find_imports(f["text"]):
        target = resolve(ref, Path(path).parent)
        if not target:
            continue
        treal = os.path.realpath(target)
        if treal in seen:
            continue
        if depth + 1 > MAX_IMPORT_DEPTH:
            n["warnings"].append(f"@{ref} is past the 4-hop import limit and won't load.")
            continue
        child = node(target, "import", scope, root, depth + 1, seen)
        child["ref"] = ref
        if root and scope in ("project", "local") and not str(treal).startswith(str(root)):
            child["warnings"].append("Outside the project folder: loads only after you approve external imports for this project.")
        n["imports"].append(child)
    return n


def total_tokens(n):
    t = FILES.get(n["path"], {}).get("tokens", 0)
    return t + sum(total_tokens(c) for c in n["imports"])


def rules_in(d):
    """Return (always, scoped) rule files under d/.claude/rules."""
    rd = Path(d) / ".claude" / "rules"
    always, scoped = [], []
    if not rd.is_dir():
        return always, scoped
    for p in sorted(rd.rglob("*.md")):
        f = read(p)
        raw = p.read_text(errors="replace") if f else ""
        paths = frontmatter_paths(raw)
        (scoped if paths else always).append((p, paths))
    return always, scoped


def git_root(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            common = Path(r.stdout.strip())
            return str(common.parent if common.name == ".git" else common)
    except Exception:
        pass
    return None


def auto_memory(cwd):
    root = git_root(cwd) or cwd
    enc = re.sub(r"[^A-Za-z0-9]", "-", root)
    p = CLAUDE / "projects" / enc / "memory" / "MEMORY.md"
    if p.is_file():
        f = read(p)
        if f and f["lines"] > 200:
            FILES[str(p)]["note"] = "Only the first 200 lines load."
        return p
    return None


def later_files(cwd):
    """CLAUDE.md / AGENTS.md in subfolders, loaded once Claude touches files there."""
    found = []
    start = Path(cwd)
    for dirpath, dirnames, filenames in os.walk(start):
        rel_depth = len(Path(dirpath).relative_to(start).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")] if rel_depth < 4 else []
        if rel_depth == 0:
            continue
        for name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"):
            if name in filenames:
                found.append(str(Path(dirpath) / name))
                read(Path(dirpath) / name)
        if len(found) > 40:
            break
    return found


def project_layers(cwd):
    cwd_p = Path(cwd)
    ancestors = [Path(*cwd_p.parts[:i]) for i in range(1, len(cwd_p.parts) + 1)]
    has_claude = any(
        (a / "CLAUDE.md").is_file() or (a / "CLAUDE.local.md").is_file()
        or ((a / ".claude" / "CLAUDE.md").is_file() and a != HOME)
        for a in ancestors)
    layers, scoped, notes = [], [], []
    for a in ancestors:
        is_home = a == HOME
        files = []
        if has_claude:
            files += [(a / "CLAUDE.md", "project")]
            if not is_home:
                files += [(a / ".claude" / "CLAUDE.md", "project")]
        else:
            files += [(a / "AGENTS.md", "project"), (a / ".claude" / "AGENTS.md", "project")]
        always, sc = ([], []) if is_home else rules_in(a)
        files += [(p, "rule") for p, _ in always]
        scoped += [{"path": str(p), "paths": ps} for p, ps in sc]
        files += [(a / "CLAUDE.local.md", "local")]
        for p, scope in files:
            if p.is_file():
                kind = "rule" if scope == "rule" else p.name
                layers.append(node(p, kind, "local" if scope == "local" else "project", root=cwd))
        if has_claude and (a / "AGENTS.md").is_file():
            # Already read when a CLAUDE.md imports it or is a symlink to it.
            imported = any(os.path.realpath(c["path"]) == os.path.realpath(a / "AGENTS.md")
                           for l in layers for c in walk(l))
            if not imported:
                notes.append({"path": str(a / "AGENTS.md"),
                              "text": "Not read: a CLAUDE.md exists here or above, so Claude Code skips AGENTS.md. Import it with @AGENTS.md, or set Project instructions to claude-md-and-agents-md."})
    return layers, scoped, notes, ("CLAUDE.md" if has_claude else "AGENTS.md")


def walk(n):
    yield n
    for c in n["imports"]:
        yield from walk(c)


def discover_projects():
    seen = {}
    root = CLAUDE / "projects"
    if not root.is_dir():
        return []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        sessions = list(d.glob("*.jsonl"))
        if not sessions:
            continue
        newest = max(sessions, key=lambda p: p.stat().st_mtime)
        cwd = None
        for s in sorted(sessions, key=lambda p: -p.stat().st_mtime)[:3]:
            try:
                with open(s, errors="replace") as fh:
                    for i, line in enumerate(fh):
                        m = re.search(r'"cwd":"([^"]+)"', line)
                        if m:
                            cwd = m.group(1)
                            break
                        if i > 200:
                            break
            except Exception:
                pass
            if cwd:
                break
        if not cwd or not Path(cwd).is_dir():
            continue
        cwd = os.path.realpath(cwd)  # symlinked paths to one folder count once
        if cwd.startswith(("/private/", "/tmp", "/var/")) or "/scratch" in cwd or "/scratchpad" in cwd:
            continue
        t = int(newest.stat().st_mtime)
        prev = seen.get(cwd)
        if prev:
            prev["sessions"] += len(sessions)
            prev["lastUsed"] = max(prev["lastUsed"], t)
        else:
            seen[cwd] = {"cwd": cwd, "sessions": len(sessions), "lastUsed": t}
    return sorted(seen.values(), key=lambda p: -p["lastUsed"])


def global_layers():
    layers = []
    if MANAGED.is_file():
        layers.append(node(MANAGED, "CLAUDE.md", "managed"))
    if (CLAUDE / "CLAUDE.md").is_file():
        layers.append(node(CLAUDE / "CLAUDE.md", "CLAUDE.md", "user"))
    always, scoped = rules_in(HOME)
    for p, _ in always:
        layers.append(node(p, "rule", "user"))
    return layers, [{"path": str(p), "paths": ps} for p, ps in scoped]


def settings_info():
    s = {}
    try:
        s = json.loads((CLAUDE / "settings.json").read_text())
    except Exception:
        pass
    mode = (((s.get("pluginConfigs") or {}).get("cc-plugin-agents-md@builtin") or
             (s.get("pluginConfigs") or {}).get("agents-md@builtin") or {}).get("options") or {}).get(
        "instructionFiles", "claude-md-or-agents-md")
    hooks = []
    for group in (s.get("hooks") or {}).get("SessionStart", []):
        for h in group.get("hooks", []):
            cmd = h.get("command", "")
            hooks.append({"matcher": group.get("matcher", ""), "command": cmd[:240]})
    style = s.get("outputStyle")
    style_file = None
    if style:
        for cand in (CLAUDE / "output-styles").glob("*.md") if (CLAUDE / "output-styles").is_dir() else []:
            if cand.stem.lower() == style.lower():
                style_file = str(cand)
    plugins = [k for k, v in (s.get("enabledPlugins") or {}).items() if v]
    return {"instructionFiles": mode, "sessionStartHooks": hooks, "outputStyle": style,
            "outputStyleFile": style_file, "claudeMdExcludes": s.get("claudeMdExcludes") or [],
            "pluginsEnabled": len(plugins), "autoMemory": s.get("autoMemoryEnabled", True)}


# ---------- Skills, commands and subagents ----------
def parse_fm(path):
    """Frontmatter fields (flat key: value) and the body of a markdown file."""
    try:
        raw = Path(path).read_text(errors="replace")[:200_000]
    except Exception:
        return {}, ""
    raw = SECRET_RE.sub("[redacted]", raw)
    fm, body = {}, raw
    if raw.startswith("---\n"):
        end = raw.find("\n---", 4)
        if end != -1:
            block, body = raw[4:end], raw[end + 4:].lstrip("\n")
            key = None
            for ln in block.split("\n"):
                m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", ln)
                if m:
                    key = m.group(1)
                    fm[key] = m.group(2).strip().strip("\"'")
                elif key == "metadata" and re.match(r"^\s+[\w-]+:\s*\S", ln):
                    mk, mv = ln.strip().split(":", 1)
                    fm.setdefault("_meta", {})[mk.strip()] = mv.strip().strip("\"'")
                    fm["metadata"] = "-"
                elif key and ln.startswith((" ", "\t")) and fm.get(key) in ("", ">", "|", ">-", "|-"):
                    fm[key] = ln.strip()
                elif key and ln.startswith((" ", "\t")):
                    fm[key] += " " + ln.strip()
    return fm, body


def item(path, kind, source, plugin=None):
    fm, body = parse_fm(path)
    p = Path(path)
    name = fm.get("name") or (p.parent.name if p.name == "SKILL.md" else p.stem)
    if plugin:
        name = f"{plugin}:{name}"
    desc = fm.get("description", "")
    auto = str(fm.get("disable-model-invocation", "")).lower() != "true"
    userinv = str(fm.get("user-invocable", "true")).lower() != "false"
    key = str(path)
    if key not in FILES:
        FILES[key] = {"text": body, "tokens": est_tokens(body), "lines": body.count("\n") + 1,
                      "mtime": int(p.stat().st_mtime)}
    meta = fm.get("_meta", {})
    cat, origin = meta.get("category"), meta.get("origin")
    short_name = name.split(":")[-1]
    for pat, t in TAGS.get("skills", {}).items():
        if pat == short_name or (pat.endswith("*") and short_name.startswith(pat[:-1])):
            cat = cat or t.get("category"); origin = origin or t.get("origin")
    if plugin:
        cat = cat or TAGS.get("plugins", {}).get(plugin, plugin.capitalize())
        origin = origin or f"plugin: {plugin}"
    if source in ("project", "project-shared") and not cat:
        cat = "Project"
    if source == "shared" and not origin:
        origin = "installed: ~/.agents"
    return {"path": key, "kind": kind, "source": source, "name": name, "description": desc,
            "category": cat or "Uncategorized", "origin": origin or "unknown",
            "auto": auto, "userInvocable": userinv, "tools": fm.get("tools"), "model": fm.get("model"),
            "symlink": os.path.realpath(path) if os.path.islink(p.parent if p.name == "SKILL.md" else p) else None,
            "descTokens": est_tokens(name + ": " + desc) if auto and kind != "agent" else 0}


def scan_dir(base, source, plugin=None):
    """Skills (dir/SKILL.md), commands (*.md) and agents (*.md) under one config root."""
    base = Path(base)
    out = []
    for sub, kind in (("skills", "skill"), ("commands", "command"), ("agents", "agent")):
        d = base / sub
        if not d.is_dir():
            continue
        if kind == "skill":
            for sk in sorted(d.iterdir()):
                if (sk / "SKILL.md").is_file():
                    out.append(item(sk / "SKILL.md", kind, source, plugin))
        else:
            for f in sorted(d.rglob("*.md")):
                out.append(item(f, kind, source, plugin))
    return out


def global_capabilities():
    caps = scan_dir(CLAUDE, "user")
    caps += scan_dir(HOME / ".agents", "shared")
    s = {}
    try:
        s = json.loads((CLAUDE / "settings.json").read_text())
    except Exception:
        pass
    enabled = {k for k, v in (s.get("enabledPlugins") or {}).items() if v}
    plugins = []
    try:
        inst = json.loads((CLAUDE / "plugins" / "installed_plugins.json").read_text()).get("plugins", {})
    except Exception:
        inst = {}
    for key, entries in inst.items():
        if not entries:
            continue
        path = entries[0].get("installPath")
        on = key in enabled
        found = scan_dir(path, "plugin", key.split("@")[0]) if path and Path(path).is_dir() else []
        for f in found:
            f["pluginEnabled"] = on
        plugins.append({"key": key, "enabled": on, "version": Path(path).name if path else None,
                        "count": len(found)})
        caps += found
    registry = []
    reg = HOME / ".agents" / "registry"
    if reg.is_dir():
        for d in sorted(reg.iterdir()):
            try:
                a = json.loads((d / "agent.json").read_text())
            except Exception:
                continue
            ins = d / "instructions.md"
            if ins.is_file():
                read(ins)
            registry.append({"name": a.get("name", d.name), "harness": a.get("harness"), "effort": a.get("effort"),
                             "path": str(d), "instructions": str(ins) if ins.is_file() else None})
    other = [str(p) for p in sorted((HOME / ".agents").glob("*.md"))] if (HOME / ".agents").is_dir() else []
    for o in other:
        read(o)
    return caps, plugins, registry, other


def project_capabilities(cwd):
    """Project skills, commands and agents in the working folder and folders above it, below ~."""
    out, cwd_p = [], Path(cwd)
    for i in range(len(cwd_p.parts), 0, -1):
        a = Path(*cwd_p.parts[:i])
        if a == HOME or HOME not in a.parents:
            break
        found = scan_dir(a / ".claude", "project") + scan_dir(a / ".agents", "project-shared")
        for f in found:
            f["root"] = str(a)
        out += found
    return out


def main():
    t0 = time.time()
    g_layers, g_scoped = global_layers()
    g_caps, plugins, registry, agents_other = global_capabilities()
    settings = settings_info()
    projects = []
    # Files already loaded through an @import (e.g. ~/.agents/AGENTS.md) aren't "other" notes.
    loaded = {os.path.realpath(n["path"]) for l in g_layers for n in walk(l)}
    agents_other = [o for o in agents_other if os.path.realpath(o) not in loaded]
    for p in discover_projects():
        layers, scoped, notes, mode = project_layers(p["cwd"])
        mem = auto_memory(p["cwd"])
        wt = re.match(r"(.+?)/(?:\.claude/worktrees|\.t3/worktrees|conductor/workspaces)/[^/]+", p["cwd"])
        # A CLAUDE.md that tells the agent in words to read AGENTS.md (no @import).
        for l in layers:
            txt = FILES.get(l["path"], {}).get("text", "")
            if l["kind"] == "CLAUDE.md" and "AGENTS.md" in txt and not any(
                    c.get("ref", "").endswith("AGENTS.md") for c in l["imports"]):
                ag = Path(l["path"]).parent / "AGENTS.md"
                if ag.is_file() and os.path.realpath(l["path"]) != os.path.realpath(ag):
                    notes[:] = [n for n in notes if n["path"] != str(ag)]
                    notes.append({"path": str(ag), "text": "Not loaded: the CLAUDE.md next to it only mentions it in words, so the agent sees it only if it decides to open the file. Replace the sentence with @AGENTS.md."})
        p.update({
            "worktreeOf": wt.group(1) if wt else None,
            "name": Path(p["cwd"]).name or p["cwd"],
            "mode": mode,
            "layers": layers,
            "memory": node(mem, "MEMORY.md", "memory") if mem else None,
            "scopedRules": g_scoped + scoped,
            "later": later_files(p["cwd"]),
            "notes": notes,
            "caps": project_capabilities(p["cwd"]),
        })
        p["tokens"] = (sum(total_tokens(l) for l in g_layers) + sum(total_tokens(l) for l in layers)
                       + (total_tokens(p["memory"]) if p["memory"] else 0))
        projects.append(p)
    OUT.mkdir(exist_ok=True)
    data = {"generated": int(time.time()), "home": str(HOME), "claudeVersion": claude_version(),
            "settings": settings, "global": g_layers,
            "caps": g_caps, "plugins": plugins, "registry": registry, "agentsOther": agents_other, "projects": projects, "files": FILES}
    (OUT / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    print(f"{len(projects)} projects, {len(FILES)} files, "
          f"{(OUT / 'data.json').stat().st_size // 1024} KB in {time.time() - t0:.1f}s")


def claude_version():
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10).stdout.split()[0]
    except Exception:
        return None


if __name__ == "__main__":
    main()
