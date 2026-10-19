"""服务器切换：gunicorn(2 workers) → daphne(ASGI 单进程)

背景：Milvus Lite 是单进程嵌入式库，gunicorn 2 个 worker 争抢文件锁，
抢不到锁的 worker 向量检索全空——请求轮询到坏 worker 时 top-5 全变成
关键词通道的 0.55 垃圾命中，目标文档根本进不了检索结果。
项目规范（deploy.md/经验）本就要求生产用 daphne ASGI 部署。
"""
import os
import subprocess
import sys
import time


def run(cmd, timeout=600):
    print(f'$ {cmd}')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or '') + (r.stderr or '')
    print(out[-2000:])
    return r.returncode, out


UNIT = '/etc/systemd/system/campus-backend.service'

# 1. 安装 daphne（幂等）
rc, _ = run('pip show daphne >/dev/null 2>&1 || '
            'pip install daphne -i https://mirrors.aliyun.com/pypi/simple/ --prefer-binary -q')
if rc != 0:
    print('!! daphne 安装失败')
    sys.exit(1)
rc, out = run('venv/bin/daphne --help')
if rc != 0:
    print('!! daphne 不可用')
    sys.exit(1)

# 2. 备份并改写 unit 的 ExecStart
with open(UNIT) as f:
    content = f.read()
if 'daphne' in content:
    print('unit 已是 daphne，跳过改写')
else:
    backup = UNIT + '.bak.gunicorn'
    if not os.path.exists(backup):
        with open(backup, 'w') as f:
            f.write(content)
    lines = content.splitlines()
    new_lines = []
    in_exec = False
    for ln in lines:
        if ln.startswith('ExecStart='):
            in_exec = True
            new_lines.append(
                'ExecStart=/www/wwwroot/campus_resource/backend/venv/bin/daphne '
                '-b 127.0.0.1 -p 3000 --access-log /tmp/campus-access.log '
                'config.asgi:application'
            )
            continue
        if in_exec and ln.startswith(' '):
            continue  # 去掉 gunicorn ExecStart 的续行
        in_exec = False
        new_lines.append(ln)
    with open(UNIT, 'w') as f:
        f.write('\n'.join(new_lines) + '\n')
    print('unit 已改写（备份:', backup + '）')

rc, _ = run('systemctl daemon-reload && systemctl restart campus-backend')
if rc != 0:
    print('!! 服务重启失败，检查 unit')
    sys.exit(1)

# 3. 等待端口 + 验证进程形态
ok = False
for i in range(15):
    time.sleep(4)
    rc, out = run('ss -ltn | grep -q :3000 && echo UP')
    if 'UP' in out:
        ok = True
        break
print('端口 3000:', 'UP' if ok else 'DOWN')
run('systemctl is-active campus-backend')
run('ps aux | grep -E "daphne|gunicorn" | grep -v grep | head -4')
sys.exit(0 if ok else 1)
