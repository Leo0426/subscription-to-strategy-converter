#!/usr/bin/env python3
"""Regenerate the catalog-owned block in standalone Leo; --check never writes."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.service_catalog import CATALOG_PATH, template_service_block

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
path = CATALOG_PATH.with_name('leo.yaml')
original = path.read_text()
start_marker = '# Managed service rules: services.json\n'
end_marker = '# End managed service rules\n'
if start_marker not in original:
    raise SystemExit('managed service block missing from leo.yaml')
start = original.index(start_marker)
end = original.index(end_marker, start) + len(end_marker)
expected = original[:start] + template_service_block() + original[end:]
if args.check:
    if expected != original:
        raise SystemExit('service rules changed: run uv run python scripts/sync-service-rules.py and re-audit Leo')
    print('Service catalog and Leo match.')
else:
    path.write_text(expected)
    print('Updated Leo managed service rules; refresh its audit snapshot.')
