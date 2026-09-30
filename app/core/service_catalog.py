"""Shared service rules for workbench preferences and Leo exports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[2] / 'community_templates/leo/services.json'


def service_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))['services']


def catalog_revision() -> str:
    return hashlib.sha256(CATALOG_PATH.read_bytes() + CATALOG_PATH.with_name("leo.yaml").read_bytes()).hexdigest()


def service_rules(service: dict, target: str | None = None) -> list[str]:
    return [f"{rule['match']},{target or service['group']}" for rule in service['rules']]


def template_service_block() -> str:
    lines = ['# Managed service rules: services.json']
    for service in service_catalog():
        for rule in service['rules']:
            if rule.get('template_inline'):
                lines.append(f"- {rule['match']},{service['default_target']}")
    lines.append('# End managed service rules')
    return '\n'.join(lines) + '\n'
