#!/usr/bin/env python3
"""Generate config/config.yaml from models.yaml + base settings"""
import yaml, os, subprocess

base_path = "config/default-config-base.yaml"
models_path = "config/models.yaml"
output_path = "config/config.yaml"

with open(base_path) as f:
    config = yaml.safe_load(f) or {}

with open(models_path) as f:
    raw = yaml.safe_load(f)

region = os.environ.get('BEDROCK_INFERENCE_REGION', '').strip() or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')

model_list = []
for item in raw:
    if 'litellm_params' in item:
        # Full format - pass through as-is
        model_list.append(item)
    else:
        # Simple format - auto-generate litellm_params
        mid = item['id']
        name = item['name']
        api_route = item.get('api_route', 'bedrock').rstrip('/') + '/'

        entry = {
            'model_name': name,
            'litellm_params': {
                'model': f'{api_route}{mid}',
            }
        }
        if api_route.startswith('bedrock/'):
            entry['litellm_params']['aws_region_name'] = region
        if item.get('api_base'):
            entry['litellm_params']['api_base'] = item['api_base']
        if item.get('api_key'):
            entry['litellm_params']['api_key'] = item['api_key']
        if 'embed' in mid.lower():
            entry['litellm_params']['mode'] = 'embedding'
            entry['model_info'] = {'mode': 'embedding'}

        model_list.append(entry)

config['model_list'] = model_list

# Timezone: LITELLM_TIMEZONE env > local machine timezone > UTC
def detect_local_tz():
    try:
        return subprocess.check_output(['timedatectl', 'show', '-p', 'Timezone', '--value'], text=True).strip()
    except Exception:
        pass
    try:
        import time
        return time.tzname[0]
    except Exception:
        return 'UTC'

tz = os.environ.get('LITELLM_TIMEZONE', '').strip() or detect_local_tz()
config.setdefault('litellm_settings', {})['timezone'] = tz

# Alerting: Slack and/or Lark (Feishu), independent and non-conflicting.
#   - SLACK_WEBHOOK_URL  -> real Slack (native LiteLLM slack alerting)
#   - LARK_WEBHOOK_URL    -> routed through the middleware's internal slack->lark
#     bridge (http://localhost:3000/webhook/slack-to-lark), which translates the
#     Slack-format payload to a Lark message.
# Both use LiteLLM's "slack" alerting channel (Lark isn't Slack-compatible, so the middleware bridges it). 
# If BOTH are configured, alerts fan out to both via alert_to_webhook_url lists. 
# Covers all alert types (budget, exceptions, slow requests, daily/weekly reports, etc.). 
# Empty values disable the respective sink.
_LARK_BRIDGE_URL = 'http://localhost:3000/webhook/slack-to-lark'
_ALERT_TYPES = [
    'llm_exceptions', 'llm_too_slow', 'llm_requests_hanging', 'budget_alerts',
    'spend_reports', 'db_exceptions', 'daily_reports', 'cooldown_deployment',
    'new_model_added', 'outage_alerts',
]
_alert_sinks = []
if os.environ.get('SLACK_WEBHOOK_URL', '').strip():
    _alert_sinks.append(os.environ['SLACK_WEBHOOK_URL'].strip())
if os.environ.get('LARK_WEBHOOK_URL', '').strip():
    _alert_sinks.append(_LARK_BRIDGE_URL)

if _alert_sinks:
    gs = config.setdefault('general_settings', {})
    if 'slack' not in gs.setdefault('alerting', []):
        gs['alerting'].append('slack')
    if len(_alert_sinks) == 1:
        # Single destination: the simple SLACK_WEBHOOK_URL env is enough.
        config.setdefault('environment_variables', {})['SLACK_WEBHOOK_URL'] = _alert_sinks[0]
    else:
        # Multiple destinations: fan out per alert type to all sinks.
        gs['alert_to_webhook_url'] = {t: list(_alert_sinks) for t in _ALERT_TYPES}

with open(output_path, 'w') as f:
    # Resolve os.environ/ placeholders in model params
    for m in model_list:
        params = m.get('litellm_params', {})
        for k, v in list(params.items()):
            if isinstance(v, str) and v.startswith('os.environ/'):
                env_name = v[len('os.environ/'):]
                env_val = os.environ.get(env_name, '')
                if env_val:
                    params[k] = env_val
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f"Generated {output_path}: {len(model_list)} models, region={region}")
