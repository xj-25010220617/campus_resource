"""
faster-whisper 语音识别模块
- OpenAI Whisper 的 CTranslate2 高性能重实现
- 语音 → 文字转换，内置 VAD 静音过滤
- 懒加载 + 单例模式，首次调用自动下载模型
"""
from __future__ import annotations

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

# 音频文件魔数（文件头特征）用于自动检测格式
_MAGIC_BYTES = {
    b'RIFF': 'wav',          # WAV
    b'ID3': 'mp3',           # MP3 with ID3
    b'\xff\xfb': 'mp3',      # MP3 without ID3 (MPEG audio frame sync)
    b'\xff\xf3': 'mp3',      # MP3 without ID3
    b'\xff\xf2': 'mp3',      # MP3 without ID3
    b'\xff\xfa': 'mp3',      # MP3 without ID3
    b'OggS': 'ogg',          # OGG/Opus
    b'ftyp': 'm4a',          # M4A/AAC
    b'\x1a\x45\xdf\xa3': 'webm',  # WebM/Opus (EBML header)
    # WAV also starts with RIFF....WAVE
}

def _detect_format_from_header(audio_data: bytes) -> str | None:
    """
    通过文件头魔数自动检测音频格式。

    Returns:
        检测到的格式字符串（如 'wav', 'webm', 'ogg', 'mp3', 'm4a'），未知则返回 None。
    """
    for magic, fmt in _MAGIC_BYTES.items():
        if audio_data.startswith(magic):
            return fmt
    return None

# 格式到 pydub format 的映射（处理带参数的 mime type）
_FORMAT_TO_PYDUB = {
    'webm': 'webm',
    'ogg': 'ogg',
    'opus': 'ogg',     # opus 在 ogg 容器中
    'mp3': 'mp3',
    'm4a': 'm4a',
    'aac': 'aac',
    'wav': 'wav',
    'flac': 'flac',
}

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
    """懒加载 faster-whisper 模型（首次调用时下载，仅加载一次）"""
    global _model_error
    from django.conf import settings

    model_name = getattr(settings, 'ASR_MODEL_NAME', 'small')
    if _has_cuda():
        device = 'cuda'
        compute_type = 'float16'
    else:
        device = 'cpu'
        compute_type = 'int8'
    logger.info(f'正在加载 ASR 模型: {model_name} (device={device}, compute_type={compute_type}, 首次需下载)...')
    t0 = time.time()
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
        logger.info(f'ASR 模型加载完成，耗时 {time.time() - t0:.1f}s, device={device}')
        _model_error = None
        return model
    except ImportError as e:
        _model_error = f'依赖缺失: {e}。请执行 pip install faster-whisper'
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
        import librosa
        import soundfile

        audio, sr = librosa.load(src_path, sr=16000, mono=True)
        duration = len(audio) / 16000
        logger.info(f'librosa 音频信息: sr={sr}, duration={duration:.1f}s, samples={len(audio)}')
        soundfile.write(tmp_path, audio, 16000, format='WAV')
    except Exception as e:
        # pydub 失败（含 ffmpeg 缺失导致的 FileNotFoundError），尝试回退到 librosa
        is_ffmpeg_missing = isinstance(e, FileNotFoundError)
        logger.warning(
            f'pydub 转换失败 ({type(e).__name__}: {e})，尝试回退到 librosa'
            + ('（ffmpeg 未安装）' if is_ffmpeg_missing else '')
        )
        try:
            import librosa
            import soundfile

            audio, sr = librosa.load(src_path, sr=16000, mono=True)
            duration = len(audio) / 16000
            logger.info(f'librosa 音频信息: sr={sr}, duration={duration:.1f}s')
            soundfile.write(tmp_path, audio, 16000, format='WAV')
        except Exception as e2:
            # pydub 和 librosa 都失败了
            if is_ffmpeg_missing:
                logger.error(f'librosa 回退也失败（很可能也因缺 ffmpeg）: {type(e2).__name__}: {e2}')
                raise RuntimeError(
                    f'音频转码失败：系统中未找到 ffmpeg。'
                    f'请执行 sudo apt install ffmpeg (Linux) 或下载 ffmpeg.exe 并添加到 PATH (Windows)。'
                    f'原始错误: {e}'
                ) from e
            else:
                logger.error(f'librosa 回退也失败: {type(e2).__name__}: {e2}')
                raise

    # 校验转换结果
    wav_size = os.path.getsize(tmp_path)
    if wav_size < _MIN_AUDIO_BYTES:
        logger.warning(f'转换后 WAV 文件过小 ({wav_size} bytes)，可能无效')
    else:
        logger.info(f'音频转换完成: {tmp_path} ({wav_size} bytes)')
    return tmp_path


def speech_to_text(audio_data: bytes, audio_format: str = 'wav') -> tuple[str, dict | None]:
    """
    语音 → 文字转换。

    webm/ogg/mp4/opus 用 pydub 内存转码为 WAV；
    mp3/m4a/aac/flac 等通过 _convert_to_wav 文件转码；
    WAV 格式直接识别。

    Returns:
        (text, error_info) 元组：
        - text: 识别成功时返回文字，失败时返回空字符串 ''
        - error_info: 成功时为 None；失败时为包含详细错误信息的 dict：
            {
                'stage': str,        # 失败阶段（format_check / size_check / convert / model_load / inference / extract）
                'error_type': str,   # 错误类型标识
                'detail': str,       # 人类可读错误描述
                'suggestion': str,   # 建议的解决方法
                'error_log': [       # 完整处理时间线（含成功步骤 + 失败步骤）
                    {'step': str, 'status': 'ok'|'fail', 'message': str, 'detail': str|None},
                    ...
                ],
            }
    """
    import io

    raw_path = None
    wav_path = None
    error_log = []  # 记录每一步的执行情况

    def _add_log(step: str, status: str, message: str, detail: str | None = None):
        error_log.append({'step': step, 'status': status, 'message': message, 'detail': detail})
        if status == 'fail':
            logger.error(f'[{step}] {message}' + (f' | {detail}' if detail else ''))
        else:
            logger.info(f'[{step}] {message}')

    def _make_error(stage: str, error_type: str, detail: str, suggestion: str) -> dict:
        return {
            'stage': stage,
            'error_type': error_type,
            'detail': detail,
            'suggestion': suggestion,
            'error_log': error_log,
        }

    def _pydub_convert_to_wav(data: bytes, try_formats: list[str]) -> bytes | None:
        """
        尝试多种 pydub format 参数进行内存转码。
        返回转换后的 WAV bytes，失败返回 None。
        """
        from pydub import AudioSegment
        for pydub_fmt in try_formats:
            try:
                audio = AudioSegment.from_file(io.BytesIO(data), format=pydub_fmt)
                audio = audio.set_channels(1).set_frame_rate(16000)
                buf = io.BytesIO()
                audio.export(buf, format='wav')
                logger.info(f'pydub 内存转码成功: format={pydub_fmt}, duration={len(audio)/1000:.1f}s')
                return buf.getvalue()
            except Exception as e:
                logger.warning(f'pydub format={pydub_fmt} 失败: {type(e).__name__}: {e}')
                continue
        return None

    # 0) 优先用文件头魔数检测格式，其次用传入的 audio_format
    detected_fmt = _detect_format_from_header(audio_data)
    if detected_fmt:
        fmt = detected_fmt
        _add_log('format_detect', 'ok', f'文件头魔数检测: {fmt}', None)
    else:
        fmt = (audio_format or 'wav').lower().lstrip('.')
        _add_log('format_detect', 'ok', f'使用传入格式: {fmt}（魔数检测失败）', None)

    need_convert = fmt in _CONVERT_FORMATS
    ext = f'.{fmt}'

    try:
        # 1) webm/ogg/mp4 等浏览器原生格式，用 pydub 内存转码为 WAV
        if fmt in ('webm', 'ogg', 'mp4', 'opus'):
            # 浏览器 webm/ogg 可能带不同编解码器，尝试多种 format 参数
            pydub_formats = ['webm', 'ogg', 'opus', 'mp4'] if fmt == 'webm' else [fmt, 'ogg', 'opus', 'mp4']
            try:
                from pydub import AudioSegment
                _add_log('format_convert', 'ok', f'开始 pydub 内存转码: {fmt} → WAV',
                         f'原始大小={len(audio_data)} bytes, 尝试 formats={pydub_formats}')
                converted = _pydub_convert_to_wav(audio_data, pydub_formats)
                if converted is None:
                    raise ValueError('所有 pydub format 均失败')
                audio_data = converted
                fmt = 'wav'
                ext = '.wav'
                need_convert = False
                _add_log('format_convert', 'ok', f'pydub 内存转码完成',
                         f'转换后大小={len(audio_data)} bytes, channels=1, rate=16000')
            except ImportError:
                _add_log('format_convert', 'fail',
                         f'pydub 未安装，无法处理 {fmt} 格式',
                         '请执行: pip install pydub')
                return '', _make_error(
                    stage='format_convert',
                    error_type='PYDUB_NOT_INSTALLED',
                    detail=f'音频格式 {fmt} 需要 pydub 库支持，但 pydub 未安装',
                    suggestion='请联系管理员安装 pydub 库：pip install pydub',
                )
            except FileNotFoundError as e:
                _add_log('format_convert', 'fail',
                         f'ffmpeg 未安装或不在 PATH 中，pydub 无法处理 {fmt} 格式',
                         str(e))
                return '', _make_error(
                    stage='format_convert',
                    error_type='FFMPEG_NOT_FOUND',
                    detail=f'{fmt} 格式转码需要 ffmpeg，但系统中未找到 ffmpeg（{e}）',
                    suggestion='请在服务器上安装 ffmpeg：Ubuntu/Debian 执行 sudo apt install ffmpeg，'
                               'Windows 下载 ffmpeg.exe 并添加到 PATH 环境变量，'
                               '或改用 WAV 格式录制音频',
                )
            except Exception as e:
                _add_log('format_convert', 'fail',
                         f'pydub 转码失败 ({type(e).__name__})',
                         str(e))
                return '', _make_error(
                    stage='format_convert',
                    error_type='FORMAT_CONVERT_FAILED',
                    detail=f'{fmt} 格式音频转码失败: {type(e).__name__}: {e}',
                    suggestion='请确认音频文件未损坏，或尝试使用 WAV 格式重新录制',
                )

        # 1) 音频大小校验
        size = len(audio_data)
        _add_log('size_check', 'ok', f'音频大小校验通过', f'size={size} bytes ({size / 1024:.1f} KB)')
        if size < _MIN_AUDIO_BYTES:
            _add_log('size_check', 'fail', f'音频文件过小',
                     f'size={size} bytes, 最小要求={_MIN_AUDIO_BYTES} bytes')
            return '', _make_error(
                stage='size_check',
                error_type='AUDIO_TOO_SMALL',
                detail=f'音频文件过小（{size} bytes），可能为空文件或录音时长过短',
                suggestion='请重新录制，确保录音时长至少 0.5 秒',
            )
        if size > _MAX_AUDIO_BYTES:
            _add_log('size_check', 'fail', f'音频文件过大',
                     f'size={size} bytes, 最大限制={_MAX_AUDIO_BYTES} bytes (10MB)')
            return '', _make_error(
                stage='size_check',
                error_type='AUDIO_TOO_LARGE',
                detail=f'音频文件超过 10MB 限制（{size / 1024 / 1024:.1f} MB）',
                suggestion='请压缩音频或缩短录音时长后重试',
            )

        # 2) 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_data)
            raw_path = f.name
        _add_log('temp_file', 'ok', f'临时文件已写入', f'path={raw_path}, ext={ext}')

        # 3) 非 WAV 格式转码
        if need_convert:
            _add_log('audio_convert', 'ok', f'开始音频格式转换: {fmt} → WAV 16kHz 单声道', None)
            try:
                input_path = _convert_to_wav(raw_path)
                wav_path = input_path
                wav_size = os.path.getsize(wav_path)
                _add_log('audio_convert', 'ok', f'音频格式转换完成',
                         f'wav_size={wav_size} bytes ({wav_size / 1024:.1f} KB)')
            except Exception as e:
                _add_log('audio_convert', 'fail',
                         f'音频格式转换失败 ({type(e).__name__})', str(e))
                # 判断是否是 ffmpeg 缺失导致的
                if 'ffmpeg' in str(e).lower():
                    return '', _make_error(
                        stage='audio_convert',
                        error_type='FFMPEG_NOT_FOUND',
                        detail=f'{fmt} → WAV 转换失败：系统中未找到 ffmpeg（{e}）',
                        suggestion='请在服务器上安装 ffmpeg：Ubuntu/Debian 执行 sudo apt install ffmpeg，'
                                   'Windows 下载 ffmpeg.exe 并添加到 PATH，或改用 WAV 格式录制音频',
                    )
                return '', _make_error(
                    stage='audio_convert',
                    error_type='AUDIO_CONVERT_FAILED',
                    detail=f'{fmt} → WAV 转换失败: {type(e).__name__}: {e}',
                    suggestion='请确认音频文件编码格式正确，或尝试使用 WAV 格式录制',
                )
        else:
            input_path = raw_path
            _add_log('audio_convert', 'ok', f'已是 WAV 格式，跳过转码', None)

        # 4) 模型状态检查
        model = get_asr_model()
        if model is None:
            err = _model_error or '未知错误'
            _add_log('model_load', 'fail', f'ASR 模型未加载', err)
            return '', _make_error(
                stage='model_load',
                error_type='ASR_MODEL_NOT_LOADED',
                detail=f'语音识别模型加载失败: {err}',
                suggestion='请联系管理员检查 faster-whisper 模型是否正确安装，或检查服务器日志',
            )
        _add_log('model_load', 'ok', 'ASR 模型已就绪', None)

        # 5) 调用识别
        if _is_debug():
            logger.debug(f'调用 model.transcribe, input_path={input_path}')
        _add_log('inference', 'ok', '开始语音识别推理', f'input_path={input_path}')
        segments, info = model.transcribe(input_path, vad_filter=True)
        # segments 是生成器，迭代时才真正执行推理，需先物化
        text = ''.join(segment.text for segment in segments).strip()

        # 6) 调试日志
        if _is_debug():
            lang = getattr(info, 'language', 'N/A')
            duration = getattr(info, 'duration', 'N/A')
            logger.debug(f'faster-whisper info: language={lang}, duration={duration}')
            logger.debug(f'faster-whisper text[:200]={text[:200]}')

        _add_log('inference', 'ok', 'ASR 模型推理完成',
                 f'language={getattr(info, "language", "N/A")}')

        # 7) 校验识别文字
        if not text:
            _add_log('extract_text', 'fail', '未能从识别结果中提取到有效文字',
                     f'language={getattr(info, "language", "N/A")}')
            return '', _make_error(
                stage='extract_text',
                error_type='ASR_NO_TEXT_EXTRACTED',
                detail='音频已识别但未提取到有效文字，音频中可能无有效语音或背景噪音过大',
                suggestion='请在安静环境中重新录制，确保音频中包含清晰的语音，发音清晰、语速适中',
            )

        _add_log('extract_text', 'ok', f'文字提取成功', f'text={text[:80]}...')
        logger.info(f'ASR 识别成功: {text[:80]}...')
        return text, None

    except Exception as e:
        _add_log('exception', 'fail', f'ASR 未知异常 ({type(e).__name__})', str(e))
        return '', _make_error(
            stage='exception',
            error_type='ASR_UNKNOWN_ERROR',
            detail=f'语音识别过程发生未知错误: {type(e).__name__}: {e}',
            suggestion='请重试，如持续失败请联系管理员查看服务器日志',
        )
    finally:
        for p in (raw_path, wav_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
