"""
FunASR 语音识别模块
- 阿里达摩院开源 paraformer-zh 模型
- 中文语音 → 文字转换
- 懒加载 + 单例模式，首次调用自动下载模型（~300MB）
"""
import logging
import os
import threading
import time
import tempfile

logger = logging.getLogger(__name__)

# 单例
_asr_instance = None
_asr_lock = threading.Lock()
_model_error = None  # 记录模型加载失败原因

# 需转码为 WAV 的格式
_CONVERT_FORMATS = {'mp3', 'm4a', 'aac', 'ogg', 'flac', 'wma', 'opus', 'webm'}

# 音频大小限制
_MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 10MB
_MIN_AUDIO_BYTES = 512                # 太小肯定无效


def _is_debug():
    try:
        from django.conf import settings
        return getattr(settings, 'ASR_DEBUG', False)
    except Exception:
        return False


def _has_cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _load_model():
    """懒加载 FunASR 模型（首次调用时下载，仅加载一次）"""
    global _model_error
    from django.conf import settings

    model_name = getattr(
        settings, 'ASR_MODEL_NAME',
        'damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404',
    )
    device = 'cuda:0' if _has_cuda() else 'cpu'
    logger.info(f'正在加载 ASR 模型: {model_name} (device={device}, 首次需下载 ~300MB)...')
    t0 = time.time()
    try:
        from funasr import AutoModel
        model = AutoModel(
            model=model_name,
            device=device,
            disable_update=True,
        )
        logger.info(f'ASR 模型加载完成，耗时 {time.time() - t0:.1f}s, device={device}')
        _model_error = None
        return model
    except ImportError as e:
        _model_error = f'依赖缺失: {e}。请执行 pip install funasr torch'
        logger.error(f'ASR 模型加载失败: {_model_error}')
        raise
    except Exception as e:
        _model_error = f'{type(e).__name__}: {e}'
        logger.error(f'ASR 模型加载失败: {_model_error}')
        raise


def get_asr_model():
    global _asr_instance, _model_error
    if _asr_instance is None:
        with _asr_lock:
            if _asr_instance is None:
                try:
                    _asr_instance = _load_model()
                except Exception:
                    _asr_instance = None  # 加载失败，下次调用重试
    return _asr_instance


def _convert_to_wav(src_path: str) -> str:
    """
    将音频文件转换为 16kHz 单声道 WAV。
    优先使用 pydub（支持 webm/ogg/opus），不可用时回退到 librosa。
    """
    basename = os.path.basename(src_path)
    logger.info(f'正在转换音频格式: {basename} → WAV 16kHz')
    tmp_path = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name

    # 1) 优先使用 pydub（支持 webm/ogg/opus 等浏览器原生格式）
    try:
        from pydub import AudioSegment
        logger.info('使用 pydub 进行音频转换')
        audio = AudioSegment.from_file(src_path)
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(tmp_path, format='wav')
        duration = len(audio) / 1000.0
        logger.info(f'pydub 转换完成: duration={duration:.1f}s, channels=1, rate=16000')
    except ImportError:
        logger.info('pydub 未安装，回退到 librosa')
        # 2) 回退到 librosa（不支持 webm/ogg）
        import librosa
        import soundfile

        audio, sr = librosa.load(src_path, sr=16000, mono=True)
        duration = len(audio) / 16000
        logger.info(f'librosa 音频信息: sr={sr}, duration={duration:.1f}s, samples={len(audio)}')
        soundfile.write(tmp_path, audio, 16000, format='WAV')
    except Exception as e:
        logger.warning(f'pydub 转换失败 ({type(e).__name__}: {e})，尝试回退到 librosa')
        try:
            import librosa
            import soundfile

            audio, sr = librosa.load(src_path, sr=16000, mono=True)
            duration = len(audio) / 16000
            logger.info(f'librosa 音频信息: sr={sr}, duration={duration:.1f}s')
            soundfile.write(tmp_path, audio, 16000, format='WAV')
        except Exception as e2:
            logger.error(f'librosa 回退也失败: {type(e2).__name__}: {e2}')
            raise

    # 校验转换结果
    wav_size = os.path.getsize(tmp_path)
    if wav_size < _MIN_AUDIO_BYTES:
        logger.warning(f'转换后 WAV 文件过小 ({wav_size} bytes)，可能无效')
    else:
        logger.info(f'音频转换完成: {tmp_path} ({wav_size} bytes)')
    return tmp_path


def _extract_text(result) -> str:
    """
    从 FunASR/SenseVoice 返回结果中提取纯文本。
    去除语言标签、情绪标签等标记符号。
    """
    import re

    raw = ''
    if isinstance(result, list) and len(result) > 0:
        first = result[0]
        if isinstance(first, dict):
            raw = first.get('text') or first.get('value') or first.get('content') or ''
        elif isinstance(first, str):
            raw = first
    elif isinstance(result, dict):
        raw = result.get('text') or result.get('value') or ''
    elif isinstance(result, str):
        raw = result

    if not raw:
        return ''

    # SenseVoice 标签格式：<|xxx|>，全部去除
    cleaned = re.sub(r'<\|[^|]+\|>', '', raw).strip()

    if _is_debug() and cleaned != raw:
        logger.debug(f'ASR 文本清理: raw={raw[:120]} → cleaned={cleaned[:120]}')

    return cleaned


def speech_to_text(audio_data: bytes, audio_format: str = 'wav') -> str:
    """
    语音 → 文字转换。
    webm/ogg/mp4/opus 用 pydub 内存转码为 WAV；
    mp3/m4a/aac/flac 等通过 _convert_to_wav 文件转码；
    WAV 格式直接识别。
    """
    import io

    fmt = (audio_format or 'wav').lower().lstrip('.')
    need_convert = fmt in _CONVERT_FORMATS
    ext = f'.{fmt}'
    raw_path = None
    wav_path = None

    try:
        # 0) webm/ogg/mp4 等浏览器原生格式，用 pydub 先转为 WAV
        if fmt in ('webm', 'ogg', 'mp4', 'opus'):
            try:
                from pydub import AudioSegment
                logger.info(f'pydub 内存转码: {fmt} → WAV (size={len(audio_data)} bytes)')
                audio = AudioSegment.from_file(io.BytesIO(audio_data), format=fmt)
                audio = audio.set_channels(1).set_frame_rate(16000)
                buffer = io.BytesIO()
                audio.export(buffer, format='wav')
                audio_data = buffer.getvalue()
                fmt = 'wav'
                ext = '.wav'
                need_convert = False
                logger.info(f'pydub 内存转码完成: {len(audio_data)} bytes')
            except ImportError:
                logger.error('pydub 未安装，无法处理 webm/ogg 格式。请执行: pip install pydub')
                return ''
            except Exception as e:
                logger.error(f'pydub 转码失败 fmt={fmt}: {type(e).__name__}: {e}')
                return ''

        # 1) 音频大小校验
        size = len(audio_data)
        if size < _MIN_AUDIO_BYTES:
            logger.warning(f'音频文件过小 ({size} bytes)，可能无效')
            return ''
        if size > _MAX_AUDIO_BYTES:
            logger.warning(f'音频文件过大 ({size} bytes > 10MB)')
            return ''

        # 2) 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_data)
            raw_path = f.name

        # 3) 非 WAV 格式转码
        input_path = _convert_to_wav(raw_path) if need_convert else raw_path
        if need_convert:
            wav_path = input_path

        # 4) 模型状态检查
        model = get_asr_model()
        if model is None:
            err = _model_error or '未知错误'
            logger.error(f'ASR 模型未加载: {err}')
            return ''

        # 5) 调用识别
        if _is_debug():
            logger.debug(f'调用 model.generate, input_path={input_path}')
        result = model.generate(input=input_path)

        # 6) 调试日志
        if _is_debug():
            logger.debug(f'FunASR raw result type={type(result).__name__}, len={len(result) if hasattr(result, "__len__") else "N/A"}')
            logger.debug(f'FunASR raw result[:200]={str(result)[:200]}')

        if not result:
            logger.warning(f'ASR 返回 None/空, fmt={fmt}')
            return ''

        # 7) 提取文字
        text = _extract_text(result)
        if not text:
            logger.warning(f'未能从结果中提取文字, result type={type(result).__name__}, preview={str(result)[:200]}')
        else:
            logger.info(f'ASR 识别成功: {text[:80]}...')
        return text

    except Exception as e:
        logger.error(f'ASR 识别失败 fmt={fmt}: {type(e).__name__}: {e}')
        return ''
    finally:
        for p in (raw_path, wav_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
