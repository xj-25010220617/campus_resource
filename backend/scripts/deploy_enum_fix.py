"""服务器部署脚本：jieba 安装 + 后端重启 + 穷举扩展单元测试"""
import subprocess
import sys


def run(cmd, timeout=600):
    print(f'$ {cmd}')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or '') + (r.stderr or '')
    print(out[-3000:])
    return r.returncode, out


# 1. jieba 安装（幂等）
rc, _ = run('pip show jieba >/dev/null 2>&1 || '
            'pip install jieba -i https://mirrors.aliyun.com/pypi/simple/ --prefer-binary -q')
if rc != 0:
    print('!! jieba 安装失败')
    sys.exit(1)

# 2. 验证 jieba 可用 + 对真实查询分词
rc, out = run('python -c "import jieba; print(\', \'.join(jieba.cut(\'15级优秀毕业生有谁\')))"')
if rc != 0:
    print('!! jieba 导入失败')
    sys.exit(1)

# 3. 重启后端
rc, _ = run('systemctl restart campus-backend')
if rc != 0:
    print('!! 后端重启失败')
    sys.exit(1)

# 4. 等待端口就绪
import time
ok = False
for i in range(12):
    time.sleep(5)
    rc, out = run('ss -ltn | grep -q :3000 && echo UP')
    if 'UP' in out:
        ok = True
        break
print('端口 3000:', 'UP' if ok else 'DOWN')
run('systemctl is-active campus-backend')

# 5. 单元测试（sqlite 测试后端，不触碰 Milvus/MySQL）
rc, out = run('cd /www/wwwroot/campus_resource/backend && '
              'python manage.py test apps.knowledge.tests.test_enum_expansion -v 1 '
              '--settings=config.settings_test 2>&1 | tail -12')
print('测试退出码:', rc)
sys.exit(0 if (ok and rc == 0) else 1)
