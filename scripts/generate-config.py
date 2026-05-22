#!/usr/bin/env python3
"""Generate config/config.yaml from models.yaml + base settings"""
import yaml, os

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
    mid = item['id']
    name = item['name']
    api_route = item.get('api_route', 'bedrock').rstrip('/') + '/'

    entry = {
        'model_name': name,
        'litellm_params': {
            'model': f'{api_route}{mid}',
            'aws_region_name': region,
        }
    }
    if 'embed' in mid.lower():
        entry['litellm_params']['mode'] = 'embedding'
        entry['model_info'] = {'mode': 'embedding'}

    model_list.append(entry)

config['model_list'] = model_list

# Timezone: LITELLM_TIMEZONE env > local machine timezone > UTC
import subprocess
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

with open(output_path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f"Generated {output_path}: {len(model_list)} models, region={region}")
