"""Small public evidence vocabulary; never include raw controller/CLI payloads."""
from datetime import datetime, timezone
import re


def identity(client: str, *, version=None, mode='unknown', platform='unknown') -> dict:
    return {'client': client, 'platform': platform, 'version': version, 'mode': mode,
            'scope': 'server_configured_instance',
            'observed_at': datetime.now(timezone.utc).isoformat()}


def item(kind: str, status: str, source: str, observed_at: str, reason: str) -> dict:
    return {'kind': kind, 'status': status, 'source': source,
            'observed_at': observed_at, 'reason': reason}


def unavailable(client: str, reason: str, *, observed_identity=None) -> dict:
    context = observed_identity or identity(client)
    stamp = context['observed_at']
    return {'status': 'unavailable', 'actual_node': None, 'message': reason,
            'identity': context, 'evidence': [
                item('group_selection', 'unavailable', client, stamp, reason),
                item('rule_match', 'not_tested', client, stamp, '未观测关联请求的规则命中。'),
                item('probe', 'not_tested', client, stamp, '尚未执行指定节点探针。'),
                configuration_identity(context),
            ]}


def configuration_identity(context: dict) -> dict:
    return item('configuration_identity', 'unknown', context['client'], context['observed_at'],
                '客户端接口未暴露可核对的发布标识；无法确认当前发布配置，也不能由节点名推断。')


def safe_version(value) -> str | None:
    # Versions are identifiers, never arbitrary controller messages or URLs.
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+() -]{0,95}', value) and any(c.isdigit() for c in value):
        return value
    return None


def public_text(value: str) -> str:
    """Discard provider URLs, including query credentials, from permitted text fields."""
    return re.sub(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s,]+', '<RuleProvider>', value)
