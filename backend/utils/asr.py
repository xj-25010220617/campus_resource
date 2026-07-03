"""
FunASR 语音识别模块
- 阿里达摩院开源 paraformer-zh 模型
- 中文语音 → 文字转换
- 懒加载 + 单例模式，首次调用自动下载模型（~300MB）
"""
import logging
import time
import threading

logger = logging.getLogger(__name__)

# 单例
_asr_instance = None
_asr_lock = threading.Lock()


def _load_model():
    """懒加载 FunASR 模型（首次调用时下载，仅加载一次）"""
    from funasr import AutoModel
    from django.conf import settings

    model_name = getattr(
        settings, 'ASR_MODEL_NAME',
        'damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404',
    )
    logger.info(f'正在加载 ASR 模型: {model_name}（首次需下载 ~300MB）...')
    t0 = time.time()
    model = AutoModel(
        model=model_name,
        vad_model='fsmn-vad',
        punc_model='ct-punc',
        device='cuda:0' if _has_cuda() else 'cpu',
    )
    logger.info(f'ASR 模型加载完成，耗时 {time.time() - t0:.1f}s')
    return model


def _has_cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def get_asr_model():
    global _asr_instance
    if _asr_instance is None:
        with _asr_lock:
            if _asr_instance is None:
                _asr_instance = _load_model()
    return _asr_instance


def speech_to_text(audio_data: bytes, audio_format: str = 'wav') -> str:
    """
    语音 → 文字转换。

    Args:
        audio_data: 音频文件二进制内容
        audio_format: 格式（wav/pcm/mp3）

    Returns:
        识别出的文字；失败返回空字符串
    """
    import tempfile
    import os

    ext = audio_format if audio_format.startswith('.') else f'.{audio_format}'
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name

        model = get_asr_model()
        result = model.generate(input=tmp_path)
        if result and len(result) > 0:
            text = result[0].get('text', '')
            logger.info(f'ASR 识别成功: {text[:50]}...')
            return text
        return ''
    except Exception as e:
        logger.error(f'ASR 识别失败: {e}')
        return ''
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
