from __future__ import annotations

from io import StringIO

from ruamel.yaml import YAML


def render_yaml(config: dict) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    stream = StringIO()
    yaml.dump(config, stream)
    return stream.getvalue()
