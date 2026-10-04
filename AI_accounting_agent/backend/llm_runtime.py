"""Optional model transport. Basic bookkeeping never requires model packages."""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from fastapi import HTTPException


def provider(model_dir=None):
    return os.getenv('LLM_PROVIDER', 'local' if model_dir else 'disabled').strip().lower()


def model_status(model_dir=None):
    mode = provider(model_dir)
    configured = bool(model_dir) if mode == 'local' else bool(os.getenv('LLM_BASE_URL') and os.getenv('LLM_MODEL')) if mode == 'api' else False
    status = {'provider': mode, 'configured': configured, 'loaded': False}
    health_url = os.getenv('LLM_HEALTH_URL', '')
    if mode == 'api' and configured and health_url:
        try:
            with urlopen(health_url, timeout=2) as response:
                remote = json.load(response)
            status.update(loaded=remote.get('loaded') is True, model=remote.get('model'),
                          fine_tuned=remote.get('fine_tuned') is True)
        except (URLError, TimeoutError, ValueError, OSError):
            status['available'] = False
    return status


def api_chat(messages, max_tokens=1024, temperature=0.7, top_p=0.9):
    if provider() != 'api':
        raise HTTPException(503, 'AI 尚未配置。请在项目 .env 设置本地模型或 API；账本与报表可正常使用。')
    base = os.getenv('LLM_BASE_URL', '').rstrip('/')
    model = os.getenv('LLM_MODEL', '')
    if not base or not model:
        raise HTTPException(503, '请在 .env 配置 LLM_BASE_URL 和 LLM_MODEL')
    if not base.startswith(('http://', 'https://')):
        raise HTTPException(503, 'LLM_BASE_URL 必须为 http 或 https 地址')
    headers = {'Content-Type': 'application/json'}
    key = os.getenv('LLM_API_KEY', '')
    if key:
        headers['Authorization'] = 'Bearer ' + key
    payload = {'model': model, 'messages': messages, 'max_tokens': max(32, min(max_tokens, 4096)), 'temperature': temperature, 'top_p': top_p, 'stream': False}
    request = Request(base + '/chat/completions', data=json.dumps(payload).encode(), headers=headers, method='POST')
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
        content = result['choices'][0]['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError('empty model response')
        return content
    except HTTPError as exc:
        raise HTTPException(502, f'模型服务返回 HTTP {exc.code}，请检查服务端配置。') from exc
    except (URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
        raise HTTPException(502, '模型服务不可用或响应格式不正确，请检查网络和模型配置。') from exc
