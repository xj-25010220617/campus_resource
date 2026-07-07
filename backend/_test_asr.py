import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from utils.asr import get_asr_model, _model_error, _extract_text, speech_to_text

# 1) 模型加载
model = get_asr_model()
if model:
    print('[OK] model loaded')
else:
    print(f'[FAIL] model not loaded: {_model_error}')
    sys.exit(1)

# 2) 测试一段 1 秒 16kHz 静音
import tempfile, soundfile, numpy as np
path = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
audio = np.zeros(16000, dtype=np.float32)
soundfile.write(path, audio, 16000)
result = model.generate(input=path)
print(f'result type: {type(result).__name__}')
print(f'result repr: {repr(result)[:300]}')
os.unlink(path)

# 3) 测试一段简单人声（合成一个 440Hz 正弦波模拟）
path2 = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
tone = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32)
soundfile.write(path2, tone, 16000)
result2 = model.generate(input=path2)
print(f'tone result type: {type(result2).__name__}')
print(f'tone result: {str(result2)[:300]}')
os.unlink(path2)
print('[DONE]')
