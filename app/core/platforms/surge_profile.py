"""Replace routing in a native Surge profile without rewriting connectivity."""
from __future__ import annotations

import re

from app.core.normalizer import normalize_nodes_with_source_names
from app.core.parsers.surge import parse_surge_nodes
from app.ir import BUILTIN_POLICY_TARGETS


_SECTION = re.compile(r"^[ \t]*\[([^]\r\n]+)\][ \t]*(?:(?:#|;|//)[^\r\n]*)?\r?\n?$")
_MANAGED = re.compile(r"^\s*#!MANAGED-CONFIG\b", re.IGNORECASE)


class NativeSurgeProfileError(ValueError):
    """Native references cannot be preserved without changing their meaning."""


def _sections(profile: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    name = ""
    lines: list[str] = []
    for line in profile.splitlines(keepends=True):
        if _MANAGED.match(line.lstrip("\ufeff")):
            # The input's update URL would replace our rules with the raw source.
            continue
        match = _SECTION.match(line)
        if match:
            if lines:
                sections.append((name, "".join(lines)))
            name, lines = match[1].strip().lower(), []
        lines.append(line)
    if lines:
        sections.append((name, "".join(lines)))
    return sections


def _definition_name(line: str) -> str | None:
    if line.lstrip().startswith(("#", ";")):
        return None
    name, separator, _ = line.partition("=")
    return name.strip() if separator else None


def replace_surge_routing(
    source: str, compiled: str, *, source_names: dict[str, str] | None = None,
) -> str:
    """Keep native sections and groups; disambiguate generated group names.

    Preserving unrelated groups keeps native proxy chains and other sections'
    policy references valid. Only the routing sections and source update
    directive are owned by Subflow. In particular, absent General/Host sections
    stay absent so the client's defaults are not silently replaced.
    """
    replacements = {name: body for name, body in _sections(compiled)
                    if name in {"proxy group", "rule"}}
    generated_names = {_definition_name(line)
                       for line in replacements["proxy group"].splitlines()} - {None}
    normalized = normalize_nodes_with_source_names(parse_surge_nodes(source)) if source_names is None else []
    node_names = {node.name: original_name for node, original_name in normalized} if source_names is None else source_names
    if ((source_names is None and len(node_names) != len(normalized))
            or node_names.keys() & (generated_names | BUILTIN_POLICY_TARGETS)):
        raise NativeSurgeProfileError(
            "机场节点或生成策略组存在名称冲突，无法无损区分连接与分流引用；请调整节点或策略组名称"
        )
    original = _sections(source)
    native_names = {_definition_name(line) for name, body in original
                    if name in {"proxy", "proxy group"}
                    for line in body.splitlines()[1:]} - {None}
    native_names.update(node_names.values())
    used_names = native_names | generated_names
    group_names: dict[str, str] = {}
    for name in sorted(generated_names & native_names):
        candidate = f"Subflow {name}"
        suffix = 2
        while candidate in used_names:
            candidate = f"Subflow {name} {suffix}"
            suffix += 1
        group_names[name] = candidate
        used_names.add(candidate)
    def target(value: str) -> str:
        return group_names.get(value, node_names.get(value, value))

    group_lines = []
    for line in replacements["proxy group"].splitlines()[1:]:
        name = _definition_name(line)
        if name is not None:
            parts = [part.strip() for part in line.partition("=")[2].split(",")]
            line = f"{group_names.get(name, name)} = " + ", ".join(
                [parts[0], *(target(member) for member in parts[1:])])
        group_lines.append(line)
    replacements["proxy group"] = "[Proxy Group]\n" + "\n".join(group_lines) + "\n"
    rule_lines = []
    for line in replacements["rule"].splitlines()[1:]:
        parts = line.split(",")
        if len(parts) > 1:
            position = -2 if parts[-1].strip().lower() == "no-resolve" else -1
            parts[position] = target(parts[position].strip())
            line = ",".join(parts)
        rule_lines.append(line)
    replacements["rule"] = "[Rule]\n" + "\n".join(rule_lines) + "\n"

    auxiliary_groups: list[str] = []
    for name, body in original:
        if name == "proxy group":
            auxiliary_groups.extend(body.splitlines(keepends=True)[1:])
    group_header, _, group_body = replacements["proxy group"].partition("\n")
    preserved_groups = "".join(auxiliary_groups)
    if preserved_groups and not preserved_groups.endswith("\n"):
        preserved_groups += "\n"
    replacements["proxy group"] = group_header + "\n" + preserved_groups + group_body

    result: list[str] = []
    replaced: set[str] = set()
    for name, body in original:
        if name in replacements:
            if name not in replaced:
                result.append(replacements[name].rstrip() + "\n\n")
                replaced.add(name)
        else:
            result.append(body)
    for name, body in replacements.items():
        if name not in replaced:
            result.append("\n" + body.rstrip() + "\n")
    return "".join(result)
