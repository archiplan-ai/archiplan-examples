#!/usr/bin/env python3
"""
Render the archi plan into a tree of markdown task files under ./tasks/.

Each task = 1 md file. Sub-scoped tasks live in tasks/<scope>/. Per-scope
integration tasks become README.md for that subdirectory.

Each rendered file is structured:
- YAML-style property table
- Brief description
- Bulleted node definition (split on "; " for readability)
- Mermaid diagram of the node and its adjacent edges
- Requirements (with origin, target, verifications)
- Outputs (path -> purpose table)
- Stack details (bullets)
- Acceptance criteria (bullets)
- Related tasks (cross-links)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = Path("/Users/ilia/dev/test-proj/blockchain-event-gateway")
OUT = REPO / "tasks"

INTEGRATION_TASKS = {
    "compliance_audit_integration": "compliance_audit",
    "tenant_store_integration": "tenant_store",
    "chain_router_integration": "chain_router",
    "region_coordinator_integration": "region_coordinator",
    "gateway_integration": "gateway",
}

# Reverse: scope -> integration task id
SCOPE_TO_INT = {v: k for k, v in INTEGRATION_TASKS.items()}


def sh(cmd: list[str], cwd: Path = REPO) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if res.returncode != 0 and res.stderr:
        # archi crashes sometimes; tolerate but show
        sys.stderr.write(f"warn: {' '.join(cmd)}: {res.stderr[:200]}\n")
    return res.stdout


@dataclass
class Edge:
    src: str
    dst: str
    etype: str


@dataclass
class TaskMeta:
    id: str
    scope: str
    deps: int


def list_tasks() -> list[TaskMeta]:
    out = sh(["archi", "plan", "task", "list"])
    tasks = []
    for line in out.splitlines():
        # format: id  scope=X  node=Y  deps=N  description...
        m = re.match(r"^(\S+)\s+scope=(\S+)\s+node=\S+\s+deps=(\d+)", line)
        if m:
            tasks.append(TaskMeta(id=m.group(1), scope=m.group(2), deps=int(m.group(3))))
    return tasks


def list_edges_in_scope(scope: str) -> list[Edge]:
    """List edges in a given scope. scope='/' = root; otherwise enter sub-scope."""
    if scope == "/":
        out = sh(["archi", "edge", "list"])
    else:
        sh(["archi", "scope", "enter", scope])
        out = sh(["archi", "edge", "list"])
        sh(["archi", "scope", "leave"])
    edges = []
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+->\s+(\S+)\s+\(type:\s+(\S+)\)\s*$", line)
        if m:
            edges.append(Edge(m.group(1), m.group(2), m.group(3)))
    return edges


@dataclass
class ParsedTask:
    id: str
    node_id: str
    scope: str
    node_type: str
    definition: str
    description: str
    spec_refs: list[tuple[str, str]] = field(default_factory=list)  # (kind, line)
    requirements: list[dict] = field(default_factory=list)
    outputs: list[tuple[str, str]] = field(default_factory=list)
    stack_details: list[str] = field(default_factory=list)
    acceptance: list[tuple[str, list[str]]] = field(default_factory=list)


def parse_task(tid: str) -> ParsedTask:
    raw = sh(["archi", "plan", "task", "show", tid])
    lines = raw.splitlines()

    # Header line: # tid — `node`
    m = re.search(r"^#\s+(\S+)\s+—\s+`(\S+)`", lines[0]) if lines else None
    if not m:
        raise ValueError(f"unexpected task show header for {tid}")
    pid = m.group(1)
    node_id = m.group(2)

    pt = ParsedTask(id=pid, node_id=node_id, scope="", node_type="", definition="", description="")

    section = None
    cur_req: Optional[dict] = None
    cur_out: Optional[list[str]] = None  # [path, purpose]
    cur_accept: Optional[tuple[str, list[str]]] = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("## "):
            # Flush any pending in-section state before switching
            if cur_req is not None:
                pt.requirements.append(cur_req)
                cur_req = None
            if cur_accept is not None:
                pt.acceptance.append(cur_accept)
                cur_accept = None
            heading = ln[3:].strip()
            section = heading.lower()
            cur_out = None
            i += 1
            continue
        # top-level metadata before first ##
        if section is None:
            sm = re.match(r"^- \*\*scope\*\*: `([^`]+)`", ln)
            if sm:
                pt.scope = sm.group(1)
            sn = re.match(r"^- \*\*node\*\*: `([^`]+)`", ln)
            if sn:
                pass  # already captured
            st = re.match(r"^\s+- type: `([^`]+)`", ln)
            if st:
                pt.node_type = st.group(1)
            sd = re.match(r"^\s+- definition: (.*)$", ln)
            if sd:
                pt.definition = sd.group(1).strip()
            sds = re.match(r"^- \*\*description\*\*: (.*)$", ln)
            if sds:
                pt.description = sds.group(1).strip()
            i += 1
            continue
        # Section bodies
        if section == "spec elements (code-link targets)":
            sm = re.match(r"^- (Node|Edge):\s+(.*)$", ln)
            if sm:
                pt.spec_refs.append((sm.group(1), sm.group(2).strip()))
        elif section == "requirements":
            rm = re.match(r"^- `([^`]+)`\s+`([^`]+)`\s+—\s+\*\*(.*?)\*\*\s+—\s+origin:\s+([^.]+)\.\s+Targets:\s+(.*)\.\s*$", ln)
            if rm:
                if cur_req is not None:
                    pt.requirements.append(cur_req)
                cur_req = {
                    "id": rm.group(1),
                    "code": rm.group(2),
                    "summary": rm.group(3),
                    "origin": rm.group(4).strip(),
                    "targets": rm.group(5).strip(),
                    "matched_via": "",
                    "verifications": [],
                }
            elif cur_req is not None:
                mv = re.match(r"^\s+- matched via:\s+(.*)$", ln)
                if mv:
                    cur_req["matched_via"] = mv.group(1).strip().strip("`")
                else:
                    # verification lines (continuation or " - ...")
                    s = ln.strip()
                    if s and not s.startswith("- "):
                        # continuation of previous verification or matched via
                        if cur_req["verifications"]:
                            cur_req["verifications"][-1] += " " + s
                    elif s.startswith("- "):
                        text = s[2:]
                        # filter known fields
                        if not text.startswith("matched via:"):
                            cur_req["verifications"].append(text)
        elif section == "outputs":
            om = re.match(r"^- `([^`]+)`\s+—\s+(.*)$", ln)
            if om:
                pt.outputs.append((om.group(1), om.group(2).strip()))
        elif section == "stack details":
            sm = re.match(r"^- (.*)$", ln)
            if sm:
                pt.stack_details.append(sm.group(1).strip())
        elif section == "acceptance criteria":
            rh = re.match(r"^- `([^`]+)`:\s*$", ln)
            if rh:
                if cur_accept is not None:
                    pt.acceptance.append(cur_accept)
                cur_accept = (rh.group(1), [])
            elif cur_accept is not None:
                bm = re.match(r"^\s+-\s+(.*)$", ln)
                if bm:
                    cur_accept[1].append(bm.group(1).strip())
                elif ln.strip() and not ln.startswith("All listed"):
                    if cur_accept[1]:
                        cur_accept[1][-1] += " " + ln.strip()
        i += 1

    if cur_req is not None:
        pt.requirements.append(cur_req)
    if cur_accept is not None:
        pt.acceptance.append(cur_accept)
    return pt


def split_definition(text: str) -> list[str]:
    """Split a long node definition into bullet-sized chunks.

    Splits on '; ' and on sentence boundaries that look like enumerations.
    """
    text = text.strip()
    if not text:
        return []
    # First split on '; '
    parts: list[str] = []
    for chunk in text.split("; "):
        chunk = chunk.strip()
        if not chunk:
            continue
        # If still very long, split on '. '
        if len(chunk) > 220:
            for sub in re.split(r"(?<=[.])\s+(?=[A-Z(])", chunk):
                sub = sub.strip()
                if sub:
                    parts.append(sub)
        else:
            parts.append(chunk)
    return parts


def parse_edge_ref(line: str) -> Optional[Edge]:
    # "from=X to=Y type=Z (scope: ...)"
    m = re.search(r"from=(\S+)\s+to=(\S+)\s+type=(\S+)\b", line)
    if m:
        return Edge(m.group(1), m.group(2), m.group(3))
    return None


def mermaid_for(pt: ParsedTask, all_edges_root: list[Edge], scope_edges: dict[str, list[Edge]]) -> str:
    """Build a Mermaid graph showing this node and its immediate neighbours.

    Pulls from spec_refs first, plus root and scope edges as a check.
    """
    node = pt.node_id
    edges: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()

    # From spec_refs
    for kind, line in pt.spec_refs:
        if kind == "Edge":
            e = parse_edge_ref(line)
            if e:
                key = (e.src, e.dst, e.etype)
                if key not in seen:
                    seen.add(key)
                    edges.append(e)

    # Augment from root/scope edges
    pool = list(all_edges_root)
    for sc, es in scope_edges.items():
        pool.extend(es)
    for e in pool:
        if e.src == node or e.dst == node:
            key = (e.src, e.dst, e.etype)
            if key not in seen:
                seen.add(key)
                edges.append(e)

    nodes = {node}
    for e in edges:
        nodes.add(e.src)
        nodes.add(e.dst)

    if len(edges) == 0:
        # Standalone node: just render a single box
        return f"```mermaid\ngraph LR\n    {node}([\"{node}\"])\n```"

    lines = ["```mermaid", "graph LR"]
    # Highlight central node with a different shape
    for n in sorted(nodes):
        if n == node:
            lines.append(f'    {n}(["**{n}**"]):::central')
        else:
            lines.append(f'    {n}["{n}"]')
    for e in edges:
        lines.append(f"    {e.src} -->|{e.etype}| {e.dst}")
    lines.append("    classDef central fill:#fde68a,stroke:#b45309,stroke-width:2px;")
    lines.append("```")
    return "\n".join(lines)


def link_for(other_id: str, current_scope: str, all_meta: dict[str, TaskMeta]) -> str:
    """Build a relative md link from current task to `other_id`.

    Integration tasks at scope '/' for sub-scope X live at tasks/X/README.md.
    Plain tasks at scope '/' live at tasks/<id>.md.
    Sub-scope tasks live at tasks/<scope>/<id>.md.
    """
    if other_id in INTEGRATION_TASKS:
        sub = INTEGRATION_TASKS[other_id]
        target_path = f"{sub}/README.md"
    elif other_id in all_meta:
        m = all_meta[other_id]
        if m.scope == "/":
            target_path = f"{other_id}.md"
        else:
            target_path = f"{m.scope}/{other_id}.md"
    else:
        return f"`{other_id}`"

    if current_scope == "/":
        return f"[{other_id}]({target_path})"
    # current is in sub-dir
    if target_path.startswith(f"{current_scope}/"):
        return f"[{other_id}]({target_path[len(current_scope)+1:]})"
    return f"[{other_id}](../{target_path})"


def render_task(
    pt: ParsedTask,
    meta: TaskMeta,
    wave: Optional[int],
    all_edges_root: list[Edge],
    scope_edges: dict[str, list[Edge]],
    all_meta: dict[str, TaskMeta],
) -> str:
    o: list[str] = []
    title = pt.id
    o.append(f"# {title}")
    o.append("")
    if pt.description:
        # First sentence as tagline
        first = re.split(r"(?<=[.:])\s+", pt.description, maxsplit=1)[0]
        o.append(f"> {first}")
        o.append("")

    # Properties
    o.append("## Properties")
    o.append("")
    o.append("| Field | Value |")
    o.append("| --- | --- |")
    o.append(f"| Task | `{pt.id}` |")
    o.append(f"| Scope | `{meta.scope}` |")
    o.append(f"| Node | `{pt.node_id}` |")
    o.append(f"| Node type | `{pt.node_type}` |")
    o.append(f"| Dependencies | `{meta.deps}` |")
    if wave is not None:
        o.append(f"| Wave | `{wave}` |")
    o.append("")

    # Architecture diagram
    o.append("## Architecture")
    o.append("")
    o.append(mermaid_for(pt, all_edges_root, scope_edges))
    o.append("")

    # Description (broken into bullets)
    if pt.description:
        o.append("## Implementation summary")
        o.append("")
        for chunk in split_definition(pt.description):
            o.append(f"- {chunk}")
        o.append("")

    # Definition (broken into bullets)
    if pt.definition:
        o.append(f"## Node definition (`{pt.node_id}` — {pt.node_type})")
        o.append("")
        chunks = split_definition(pt.definition)
        # If there are tons of chunks, group into top-level / sub-bullets if they look enumerated
        for chunk in chunks:
            o.append(f"- {chunk}")
        o.append("")

    # Requirements
    if pt.requirements:
        o.append("## Requirements")
        o.append("")
        for r in pt.requirements:
            o.append(f"### `{r['id']}` — {r['code']}")
            o.append("")
            o.append(f"**Summary:** {r['summary']}")
            o.append("")
            o.append(f"- Origin: `{r['origin']}`")
            o.append(f"- Targets: {r['targets']}")
            if r.get("matched_via"):
                o.append(f"- Matched via: `{r['matched_via']}`")
            if r.get("verifications"):
                o.append("- Verifications:")
                for v in r["verifications"]:
                    o.append(f"  - {v}")
            o.append("")

    # Outputs
    if pt.outputs:
        o.append("## Outputs")
        o.append("")
        o.append("| Path | Purpose |")
        o.append("| --- | --- |")
        for p, purpose in pt.outputs:
            # Escape pipes
            purpose = purpose.replace("|", "\\|")
            o.append(f"| `{p}` | {purpose} |")
        o.append("")

    # Stack details
    if pt.stack_details:
        o.append("## Stack details")
        o.append("")
        for s in pt.stack_details:
            o.append(f"- {s}")
        o.append("")

    # Acceptance criteria
    if pt.acceptance:
        o.append("## Acceptance criteria")
        o.append("")
        for code, items in pt.acceptance:
            o.append(f"### {code}")
            o.append("")
            for it in items:
                o.append(f"- {it}")
            o.append("")

    # Related tasks: tasks in same scope or referenced via edges
    related: set[str] = set()
    # neighbours from spec_refs
    for kind, line in pt.spec_refs:
        if kind == "Edge":
            e = parse_edge_ref(line)
            if e:
                if e.src != pt.node_id:
                    related.add(e.src)
                if e.dst != pt.node_id:
                    related.add(e.dst)
    # neighbours from root edges (where this node is referenced as src or dst)
    for e in all_edges_root:
        if e.src == pt.node_id and e.dst != pt.node_id:
            related.add(e.dst)
        if e.dst == pt.node_id and e.src != pt.node_id:
            related.add(e.src)
    # neighbours from this task's own scope edges (intra-service)
    if meta.scope != "/" and meta.scope in scope_edges:
        for e in scope_edges[meta.scope]:
            if e.src == pt.node_id and e.dst != pt.node_id:
                related.add(e.dst)
            if e.dst == pt.node_id and e.src != pt.node_id:
                related.add(e.src)
    # Filter only those that are themselves task ids (top-level mostly)
    rel_links = []
    for n in sorted(related):
        if n in all_meta or n in INTEGRATION_TASKS:
            rel_links.append(link_for(n, meta.scope, all_meta))
        elif n in SCOPE_TO_INT:
            rel_links.append(link_for(SCOPE_TO_INT[n], meta.scope, all_meta))
    if rel_links:
        o.append("## Related tasks (graph neighbours)")
        o.append("")
        for rl in rel_links:
            o.append(f"- {rl}")
        o.append("")

    # Footer
    o.append("---")
    o.append("")
    o.append(f"_Source of truth: `archi plan task show {pt.id}`. Regenerate with `python3 tasks/_generate.py`._")
    o.append("")
    return "\n".join(o)


def render_root_readme(
    metas: list[TaskMeta],
    waves: dict[int, list[str]],
    all_edges_root: list[Edge],
) -> str:
    o: list[str] = []
    o.append("# Implementation tasks — `gateway-v1`")
    o.append("")
    o.append("> Tree of implementation tasks projected from the hardened spec (`v10`). Each task = 1 markdown file.")
    o.append("")
    o.append("## How to read this tree")
    o.append("")
    o.append("- Top-level tasks (`scope=/`) sit at the root of `tasks/`.")
    o.append("- Each Service with internal sub-scope (gateway, chain_router, region_coordinator, tenant_store, compliance_audit) has its own subdirectory; the subdirectory `README.md` is the integration task for that service.")
    o.append("- Tasks are organized into waves by dependency (Wave 1 = no dependencies; later waves depend on earlier ones).")
    o.append("- Source of truth is the archi spec; regenerate with `python3 tasks/_generate.py`.")
    o.append("")

    # Top-level architecture diagram
    o.append("## Top-level architecture")
    o.append("")
    o.append("```mermaid")
    o.append("graph LR")
    nodes = set()
    for e in all_edges_root:
        nodes.add(e.src)
        nodes.add(e.dst)
    for n in sorted(nodes):
        o.append(f'    {n}["{n}"]')
    for e in all_edges_root:
        o.append(f"    {e.src} -->|{e.etype}| {e.dst}")
    o.append("```")
    o.append("")

    # Wave structure
    o.append("## Waves")
    o.append("")
    for wnum in sorted(waves):
        o.append(f"### Wave {wnum} ({len(waves[wnum])} tasks)")
        o.append("")
        for tid in waves[wnum]:
            m = next((mm for mm in metas if mm.id == tid), None)
            if not m:
                continue
            link = link_for_root(tid, m.scope)
            scope_lbl = "" if m.scope == "/" else f" _(scope: `{m.scope}`)_"
            o.append(f"- {link}{scope_lbl}")
        o.append("")

    # Subdirectory map
    o.append("## Sub-services")
    o.append("")
    for sub in ["compliance_audit", "tenant_store", "chain_router", "region_coordinator", "gateway"]:
        o.append(f"- [{sub}/]({sub}/README.md) — integration task + child task tree")
    o.append("")

    # Top-level standalone tasks
    o.append("## Top-level standalone tasks")
    o.append("")
    for m in sorted([m for m in metas if m.scope == "/" and m.id not in INTEGRATION_TASKS], key=lambda x: x.id):
        o.append(f"- [{m.id}]({m.id}.md)")
    o.append("")

    o.append("---")
    o.append("")
    o.append("_Generated from `archi plan show` and `archi plan task show <id>` outputs._")
    o.append("")
    return "\n".join(o)


def link_for_root(other_id: str, scope: str) -> str:
    if other_id in INTEGRATION_TASKS:
        sub = INTEGRATION_TASKS[other_id]
        return f"[{other_id}]({sub}/README.md)"
    if scope == "/":
        return f"[{other_id}]({other_id}.md)"
    return f"[{other_id}]({scope}/{other_id}.md)"


def render_subdir_readme(
    scope: str,
    integration_tid: str,
    metas: list[TaskMeta],
    waves: dict[int, list[str]],
    all_edges_root: list[Edge],
    scope_edges: dict[str, list[Edge]],
    all_meta: dict[str, TaskMeta],
) -> str:
    """Render the integration task as the README, then list all children."""
    pt = parse_task(integration_tid)
    int_meta = next(m for m in metas if m.id == integration_tid)
    wave = next((w for w, ids in waves.items() if integration_tid in ids), None)
    body = render_task(pt, int_meta, wave, all_edges_root, scope_edges, all_meta)

    children = sorted([m for m in metas if m.scope == scope], key=lambda x: x.id)
    extras = ["", "## Child tasks", ""]
    extras.append("| Task | Wave | Deps | Brief |")
    extras.append("| --- | --- | --- | --- |")
    for m in children:
        wave_ch = next((w for w, ids in waves.items() if m.id in ids), "?")
        # one-line brief from list
        line = sh(["archi", "plan", "task", "list"]).splitlines()
        brief = ""
        for l in line:
            if l.startswith(m.id + " "):
                # description begins after deps=N
                bm = re.search(r"deps=\d+\s+(.*)$", l)
                if bm:
                    brief = bm.group(1).strip()
                break
        # Truncate brief
        if len(brief) > 140:
            brief = brief[:137] + "..."
        brief = brief.replace("|", "\\|")
        extras.append(f"| [{m.id}]({m.id}.md) | {wave_ch} | {m.deps} | {brief} |")
    extras.append("")
    # Internal architecture diagram for this scope
    if scope_edges.get(scope):
        extras.append("## Internal architecture")
        extras.append("")
        extras.append("```mermaid")
        extras.append("graph LR")
        scope_nodes = set()
        for e in scope_edges[scope]:
            scope_nodes.add(e.src)
            scope_nodes.add(e.dst)
        for n in sorted(scope_nodes):
            extras.append(f'    {n}["{n}"]')
        for e in scope_edges[scope]:
            extras.append(f"    {e.src} -->|{e.etype}| {e.dst}")
        extras.append("```")
        extras.append("")
    return body + "\n" + "\n".join(extras)


def parse_waves(plan_show_text: str) -> dict[int, list[str]]:
    waves: dict[int, list[str]] = defaultdict(list)
    cur = None
    for line in plan_show_text.splitlines():
        m = re.match(r"^\*\*Wave (\d+)\*\*\s*$", line)
        if m:
            cur = int(m.group(1))
            continue
        if cur is not None:
            mt = re.match(r"^- \*\*([^*]+)\*\*", line)
            if mt:
                waves[cur].append(mt.group(1))
    return dict(waves)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for sub in ("compliance_audit", "tenant_store", "chain_router", "region_coordinator", "gateway"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    metas = list_tasks()
    all_meta = {m.id: m for m in metas}
    plan_show = sh(["archi", "plan", "show"])
    waves = parse_waves(plan_show)

    # Edge sets
    print("Collecting edges...", file=sys.stderr)
    all_edges_root = list_edges_in_scope("/")
    scope_edges = {}
    for sc in ("compliance_audit", "tenant_store", "chain_router", "region_coordinator", "gateway"):
        scope_edges[sc] = list_edges_in_scope(sc)

    # Generate per-task files
    print(f"Rendering {len(metas)} tasks...", file=sys.stderr)
    for m in metas:
        try:
            pt = parse_task(m.id)
        except Exception as e:
            print(f"!! parse failed for {m.id}: {e}", file=sys.stderr)
            continue
        wave = next((w for w, ids in waves.items() if m.id in ids), None)

        if m.id in INTEGRATION_TASKS:
            sub = INTEGRATION_TASKS[m.id]
            path = OUT / sub / "README.md"
            content = render_subdir_readme(sub, m.id, metas, waves, all_edges_root, scope_edges, all_meta)
        elif m.scope == "/":
            path = OUT / f"{m.id}.md"
            content = render_task(pt, m, wave, all_edges_root, scope_edges, all_meta)
        else:
            path = OUT / m.scope / f"{m.id}.md"
            content = render_task(pt, m, wave, all_edges_root, scope_edges, all_meta)

        path.write_text(content)
        print(f"  wrote {path.relative_to(REPO)}", file=sys.stderr)

    # Root README
    root_md = render_root_readme(metas, waves, all_edges_root)
    (OUT / "README.md").write_text(root_md)
    print(f"  wrote {(OUT / 'README.md').relative_to(REPO)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
