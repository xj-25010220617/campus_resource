# 语音问答接口前后端对接文档

## 1. 接口概述

| 项目 | 内容 |
|------|------|
| **接口地址** | `POST /api/chat/voice-ask/` |
| **功能** | 接收音频文件 → FunASR 语音转文字 → RAG 检索 → LLM 流式 SSE 回答 |
| **认证方式** | Bearer Token（必须登录） |
| **Content-Type** | `multipart/form-data` |

---

## 2. 请求参数

### 2.1 请求格式

必须使用 `multipart/form-data` 格式发送请求，不支持 JSON 格式。

### 2.2 参数说明

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `audio_file` | File | 是 | 音频文件，支持浏览器原生格式（webm/ogg）及常见格式（WAV/MP3/M4A），最大 10MB |
| `conversation_id` | Integer | 否 | 会话 ID，不传则新建会话 |

### 2.3 音频格式支持

后端通过**文件头自动检测**格式，不依赖文件名扩展名：

| 格式 | 文件头特征 | 是否支持 | 备注 |
|------|-----------|----------|------|
| webm | `0x1a 0x45 0xdf 0xa3` (EBML) | ✅ | Chrome/Edge 原生输出 |
| ogg | `OggS` | ✅ | Firefox 原生输出 |
| wav | `RIFF....WAVE` | ✅ | 无需转换 |
| mp3 | `ID3` 或 `0xFF 0xFB` | ✅ | 需要转换 |
| m4a | `ftypmp4` | ✅ | 需要转换 |

### 2.4 请求示例（curl）

```bash
curl -X POST http://localhost:8000/api/chat/voice-ask/ \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -F "audio_file=@recording.webm" \
  -F "conversation_id=123"
```

---

## 3. 响应格式

### 3.1 成功响应（SSE 流式）

响应类型：`text/event-stream`

**事件流顺序**：

| 事件名 | 数据格式 | 说明 |
|--------|---------|------|
| `asr_text` | `{"text": "识别出的文字"}` | 语音转文字结果（第一个事件） |
| `token` | `{"content": "单个字符"}` | AI 回答内容（逐字输出） |
| `references` | `{"count": 1, "summary": "参考了1份文档"}` | 引用概览（可选） |
| `done` | `{"message_id": 80}` | 回答完成（最后一个事件） |
| `error` | `{"message": "错误信息", "code": "ERROR_CODE"}` | 错误（异常时出现） |

**完整响应示例**：

```
event: asr_text
data: {"text": "学校地址在哪里？"}

event: token
data: {"content": "成"}

event: token
data: {"content": "都"}

event: token
data: {"content": "东"}

event: references
data: {"count": 1, "summary": "参考了1份文档"}

event: done
data: {"message_id": 80}
```

### 3.2 错误响应（非流式）

| HTTP 状态码 | 错误码 | 说明 |
|------------|--------|------|
| 400 | `FILE_TOO_LARGE` | 音频文件超过 10MB |
| 401 | `UNAUTHORIZED` | 未登录或 Token 过期 |
| 500 | `ASR_FAILED` | 语音识别失败 |

**错误响应示例**：

```json
{
  "detail": "语音识别失败，请确认音频清晰且包含中文语音",
  "code": "ASR_FAILED"
}
```

---

## 4. 前端实现

### 4.1 录音功能（JavaScript）

```javascript
class VoiceRecorder {
  constructor() {
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.stream = null;
  }

  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          sampleRate: 16000,       // 推荐 16kHz（与模型匹配）
          channelCount: 1,         // 单声道
          echoCancellation: true,  // 开启回声消除
          noiseSuppression: true,  // 开启降噪
        } 
      });
      
      // 浏览器自动选择格式
      this.mediaRecorder = new MediaRecorder(this.stream);
      
      this.audioChunks = [];
      this.mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          this.audioChunks.push(event.data);
        }
      };
      
      this.mediaRecorder.start();
      console.log('录音开始');
    } catch (err) {
      console.error('录音失败:', err);
      throw err;
    }
  }

  async stop() {
    return new Promise((resolve) => {
      if (!this.mediaRecorder) {
        resolve(null);
        return;
      }

      this.mediaRecorder.onstop = () => {
        const audioBlob = new Blob(this.audioChunks, { 
          type: this.mediaRecorder.mimeType 
        });
        console.log(`录音完成: ${audioBlob.size} bytes, type: ${audioBlob.type}`);
        resolve(audioBlob);
        
        // 释放资源
        this.stream.getTracks().forEach(track => track.stop());
        this.mediaRecorder = null;
      };
      
      this.mediaRecorder.stop();
    });
  }
}
```

### 4.2 发送请求（JavaScript）

```javascript
async function sendVoiceAsk(audioBlob, conversationId = null) {
  const formData = new FormData();
  formData.append('audio_file', audioBlob, 'recording');
  
  if (conversationId) {
    formData.append('conversation_id', conversationId.toString());
  }

  const response = await fetch('/api/chat/voice-ask/', {
    method: 'POST',
    body: formData,
    headers: {
      'Authorization': 'Bearer ' + localStorage.getItem('token'),
      // 注意：不要设置 Content-Type，浏览器会自动处理
    },
  });

  if (!response.ok) {
    // 非流式错误（如 ASR 失败）
    const error = await response.json();
    throw new Error(error.detail || '请求失败');
  }

  // 处理 SSE 流式响应
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() || '';

    for (const eventStr of events) {
      if (!eventStr.trim()) continue;
      
      const event = parseSSE(eventStr);
      handleSSEEvent(event);
    }
  }
}

function parseSSE(eventStr) {
  const lines = eventStr.split('\n');
  let eventName = 'message';
  let data = '';

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.substring(6).trim();
    } else if (line.startsWith('data:')) {
      data = line.substring(5).trim();
    }
  }

  try {
    return { event: eventName, data: JSON.parse(data) };
  } catch {
    return { event: eventName, data };
  }
}

function handleSSEEvent(event) {
  switch (event.event) {
    case 'asr_text':
      console.log('语音识别结果:', event.data.text);
      // 更新 UI：显示识别出的文字
      break;
    case 'token':
      console.log('收到 AI 回复:', event.data.content);
      // 更新 UI：追加字符到回答区域
      break;
    case 'references':
      console.log('引用信息:', event.data);
      // 更新 UI：显示参考资料数量
      break;
    case 'done':
      console.log('回答完成:', event.data.message_id);
      // 更新 UI：标记回答完成
      break;
    case 'error':
      console.error('发生错误:', event.data);
      // 更新 UI：显示错误信息
      break;
    default:
      console.log('未知事件:', event);
  }
}
```

### 4.3 使用示例

```javascript
const recorder = new VoiceRecorder();

// 开始录音
document.getElementById('start-btn').addEventListener('click', async () => {
  await recorder.start();
});

// 停止录音并发送
document.getElementById('stop-btn').addEventListener('click', async () => {
  const audioBlob = await recorder.stop();
  if (!audioBlob) return;

  // 检查文件大小
  if (audioBlob.size < 512) {
    alert('录音时间太短，请重新录制');
    return;
  }

  if (audioBlob.size > 10 * 1024 * 1024) {
    alert('录音文件太大，限制为 10MB');
    return;
  }

  try {
    await sendVoiceAsk(audioBlob);
  } catch (error) {
    console.error('发送失败:', error);
    alert(error.message);
  }
});
```

---

## 5. 浏览器兼容性

### 5.1 MediaRecorder 输出格式

| 浏览器 | 输出格式 | MIME Type |
|--------|----------|-----------|
| Chrome | WebM (Opus) | `audio/webm;codecs=opus` |
| Edge | WebM | `audio/webm` |
| Firefox | Ogg (Opus) | `audio/ogg;codecs=opus` |
| Safari | WebM (AAC) | `audio/webm;codecs=aac` |

**注意**：后端已支持自动检测，前端无需关心具体格式。

### 5.2 权限要求

- 需要用户授权麦克风权限
- 必须使用 HTTPS 或 localhost
- iOS Safari 需要用户交互触发（不能自动播放）

---

## 6. 注意事项

### 6.1 录音建议

| 项目 | 建议值 | 说明 |
|------|--------|------|
| 时长 | 1-60 秒 | 太短无法识别，太长文件过大 |
| 采样率 | 16kHz | 与模型匹配，效果最佳 |
| 通道数 | 单声道 | 模型只处理单声道 |
| 环境 | 安静环境 | 嘈杂环境影响识别率 |
| 语言 | 普通话 | 模型仅支持中文语音 |

### 6.2 常见问题

**Q: 前端文件名没有扩展名怎么办？**

A: 后端通过文件头自动检测格式，**无需文件名扩展名**。直接发送 blob 即可。

**Q: ASR 识别失败（ASR_FAILED）怎么办？**

A: 可能原因：
1. 音频文件过小（< 512 bytes）
2. 环境太嘈杂或没有说话
3. 模型未正确加载（查看后端日志）
4. 音频格式不支持（极少数情况）

**Q: SSE 连接中断怎么办？**

A: 建议前端实现重连机制，最多重试 3 次。

### 6.3 后端日志

启动时观察模型加载状态：

```
正在加载 ASR 模型: damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404 (device=cpu, 首次需下载 ~300MB)...
ASR 模型加载完成，耗时 120.5s, device=cpu
```

识别时观察转换和识别日志：

```
文件头检测格式: webm
正在转换音频格式: recording.webm → WAV 16kHz
使用 pydub 进行音频转换
pydub 转换完成: duration=3.5s, channels=1, rate=16000
ASR 识别成功: 你好，请问有什么可以帮助你的？...
```

---

## 7. 后端代码参考

### 7.1 核心流程

```
VoiceAskView.post()
    ↓
serializer 验证参数
    ↓
读取 audio_file 字节数据
    ↓
speech_to_text(audio_data)  ← 自动检测格式 + 格式转换 + FunASR 识别
    ↓
_stream_answer(text)        ← RAG 检索 + LLM 流式输出
    ↓
SSE 响应 (text/event-stream)
```

### 7.2 文件位置

| 文件 | 说明 |
|------|------|
| `apps/chat/views.py` | VoiceAskView 视图 |
| `apps/chat/serializers.py` | VoiceAskSerializer |
| `utils/asr.py` | 语音识别核心逻辑 |
| `utils/sse.py` | SSE 格式工具函数 |

---

## 8. 错误码对照表

| 错误码 | HTTP 状态码 | 说明 | 前端处理建议 |
|--------|------------|------|-------------|
| `UNAUTHORIZED` | 401 | 未登录或 Token 过期 | 跳转登录页 |
| `FILE_TOO_LARGE` | 400 | 文件超过 10MB | 提示用户缩短录音时长 |
| `ASR_FAILED` | 500 | 语音识别失败 | 提示用户重新录制，检查环境 |
| `SSE_ERROR` | - | LLM 回答失败（SSE 事件） | 提示用户重试 |