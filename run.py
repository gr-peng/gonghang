#!/usr/bin/env python3
"""Start both original FastAPI services and the integrated mobile frontend."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / 'AI_accounting_agent' / 'backend'


def read_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


def main():
    parser = argparse.ArgumentParser(description='FinPilot / FinTechathon 一键启动')
    parser.add_argument('--no-install', action='store_true', help='使用当前 Python 中已安装的依赖')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--host', default='127.0.0.1', help='监听地址；局域网演示可设为 0.0.0.0')
    parser.add_argument('--demo', action='store_true', help='使用独立示例账本，不写入正式账本')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('需要 Python 3.11 或更新版本，推荐 Python 3.11 / 3.12。')
    read_env()
    if not args.no_install:
        env_dir = ROOT / '.venv'
        python = env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        marker = env_dir / '.core-requirements'
        requirements = BACKEND / 'requirements-core.txt'
        if not python.exists():
            print('正在创建 Python 虚拟环境…', flush=True)
            venv.EnvBuilder(with_pip=True).create(env_dir)
        if not marker.exists() or marker.read_text() != requirements.read_text():
            print('首次运行：安装基础依赖（无需 Node.js 或 GPU）…', flush=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
            marker.write_text(requirements.read_text())
        child = subprocess.Popen([str(python), str(Path(__file__).resolve()), '--no-install', *sys.argv[1:]], cwd=ROOT)
        try:
            return child.wait()
        except KeyboardInterrupt:
            # Console Ctrl+C reaches both parent and child; let the child close services.
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait()
            return 0
    try:
        import fastapi, uvicorn, multipart  # noqa: F401
    except ImportError as exc:
        raise SystemExit('依赖未安装。请运行 python run.py 自动安装，或安装 requirements-core.txt。') from exc
    book_port = int(os.getenv('BOOKKEEPER_PORT', '8010'))
    trader_port = int(os.getenv('TRADER_PORT', '8020'))
    frontend_port = int(os.getenv('FRONTEND_PORT', '5500'))
    ports = [book_port, trader_port, frontend_port]
    managed_model = os.getenv('ACCOUNTING_MODEL_SERVER', '').lower() in {'1', 'true', 'yes'}
    model_port = int(os.getenv('ACCOUNTING_MODEL_PORT', '28030'))
    if managed_model:
        ports.append(model_port)
    for port in ports:
        try:
            with socket.socket() as sock:
                # Match uvicorn/http.server: recently closed connections must not
                # be mistaken for a running listener during a managed restart.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((args.host, port))
        except OSError:
            raise SystemExit(f'端口 {port} 已被占用。请关闭旧服务后重试；启动器不会终止其他进程。')
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    (runtime/'launcher.pid').write_text(str(os.getpid())+'\n')
    env = {**os.environ, 'PYTHONUNBUFFERED':'1', 'PYTHONUTF8':'1'}
    book_env = {**env, 'QWEN_MODEL_DIR': os.getenv('BOOKKEEPER_MODEL_DIR', os.getenv('QWEN_MODEL_DIR', ''))}
    trader_env = {**env, 'QWEN_MODEL_DIR': os.getenv('TRADER_MODEL_DIR', os.getenv('QWEN_MODEL_DIR', ''))}
    # Independent book databases must not hide the packaged investment data files.
    trader_env['AI_BOOKKEEPER_DATA_DIR'] = os.getenv('AI_TRADER_DATA_DIR', str(BACKEND / 'data'))
    if args.demo:
        book_env['AI_BOOKKEEPER_DATA_DIR'] = str(runtime / 'demo-bookkeeper')
        book_env.pop('AI_BOOKKEEPER_JSONL_PATH', None)
    commands = [
        ('bookkeeper', [sys.executable, '-m', 'uvicorn', 'app:app', '--host', args.host, '--port', str(book_port)], BACKEND, book_env),
        ('investment', [sys.executable, '-m', 'uvicorn', 'investment_app:app', '--host', args.host, '--port', str(trader_port)], BACKEND, trader_env),
        ('frontend', [sys.executable, str(ROOT / 'scripts' / 'serve_frontend.py'), '--host', args.host, *(['--demo'] if args.demo else [])], ROOT, env),
    ]
    if managed_model:
        model_python = os.getenv('ACCOUNTING_MODEL_PYTHON', str(BACKEND / '.venv/bin/python'))
        model_env = {**env, 'CUDA_VISIBLE_DEVICES': os.getenv('ACCOUNTING_MODEL_GPU', '2'),
                     'PYTHONPATH': str(ROOT / '.runtime/ml-deps'), 'HF_HUB_OFFLINE': '1',
                     'TOKENIZERS_PARALLELISM': 'false'}
        commands.insert(0, ('model', [model_python, '-m', 'uvicorn', 'scripts.serve_accounting_model:app',
                                    '--host', '127.0.0.1', '--port', str(model_port)], ROOT, model_env))
    children = []
    logs = []
    try:
        for name, command, cwd, child_env in commands:
            log = (runtime / f'{name}.log').open('w', encoding='utf-8')
            logs.append(log)
            children.append(subprocess.Popen(command, cwd=cwd, env=child_env, stdout=log, stderr=subprocess.STDOUT))
        for _ in range(600 if managed_model else 80):
            if any(p.poll() is not None for p in children):
                raise RuntimeError('服务提前退出。请查看 .runtime 中的日志。')
            try:
                with urlopen(f'http://127.0.0.1:{book_port}/health', timeout=1) as r:
                    assert json.load(r)['status'] == 'ok'
                with urlopen(f'http://127.0.0.1:{trader_port}/health', timeout=1) as r:
                    assert json.load(r)['status'] == 'ok'
                with urlopen(f'http://127.0.0.1:{frontend_port}/', timeout=1) as r:
                    assert r.status == 200
                if managed_model:
                    with urlopen(f'http://127.0.0.1:{model_port}/health', timeout=1) as r:
                        assert json.load(r)['loaded'] is True
                break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError('服务启动超时，请查看 .runtime 中的日志。')
        if args.demo:
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'seed_demo.py')], check=True, env=env)
        print(f'\nFinPilot已启动：http://localhost:{frontend_port}', flush=True)
        print(f'记账 API：http://localhost:{book_port}/docs\n投研 API：http://localhost:{trader_port}/docs', flush=True)
        print('按 Ctrl+C 同时关闭三个服务。日志位于 .runtime/。', flush=True)
        if not args.no_browser:
            webbrowser.open(f'http://localhost:{frontend_port}')
        while all(p.poll() is None for p in children):
            time.sleep(.5)
        raise RuntimeError('一个服务已停止，其他服务将一并关闭。请查看 .runtime 日志。')
    except KeyboardInterrupt:
        print('\n正在关闭服务…', flush=True)
    finally:
        for p in children:
            if p.poll() is None:
                p.terminate()
        for p in children:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for log in logs:
            log.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\n启动已取消。')
        raise SystemExit(0)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'启动失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
