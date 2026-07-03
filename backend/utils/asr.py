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


# 需转码为 WAV 的格式
_CONVERT_FORMATS = {'mp3', 'm4a', 'aac', 'ogg', 'flac', 'wma', 'opus', 'webm'}


def _convert_to_wav(src_path: str) -> str:
    """
    使用 librosa 将音频文件转换为 16kHz 单声道 WAV。

    Args:
        src_path: 原始音频文件路径

    Returns:
        转换后的 WAV 文件路径
    """
    import os
    import tempfile

    import librosa
    import soundfile

    logger.info(f'正在转换音频格式: {os.path.basename(src_path)} → WAV 16kHz')
    audio, sr = librosa.load(src_path, sr=16000, mono=True)
    tmp_path = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
    soundfile.write(tmp_path, audio, 16000, format='WAV')
    logger.info(f'音频转换完成: {tmp_path}')
    return tmp_path


def speech_to_text(audio_data: bytes, audio_format: str = 'wav') -> str:
    """
    语音 → 文字转换。MP3/M4A/AAC/OGG 等格式自动转为 16kHz WAV 后识别。

    Args:
        audio_data: 音频文件二进制内容
        audio_format: 格式（wav/pcm/mp3/m4a/aac/ogg/flac）

    Returns:
        识别出的文字；失败返回空字符串
    """
    import tempfile
    import os

    fmt = (audio_format or 'wav').lower().lstrip('.')
    need_convert = fmt in _CONVERT_FORMATS
    ext = f'.{fmt}'
    raw_path = None
    wav_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_data)
            raw_path = f.name

        # 非 WAV/PCM 格式先转码
        input_path = _convert_to_wav(raw_path) if need_convert else raw_path
        if need_convert:
            wav_path = input_path

        model = get_asr_model()
        result = model.generate(input=input_path)
        if result and len(result) > 0:
            text = result[0].get('text', '')
            logger.info(f'ASR 识别成功: {text[:80]}...')
            return text
        logger.warning(f'ASR 返回空结果, fmt={fmt}')
        return ''
    except Exception as e:
        logger.error(f'ASR 识别失败 fmt={fmt}: {e}')
        return ''
    finally:
        for p in (raw_path, wav_path):
            if p and os.path.exists(p):
                os.unlink(p)
