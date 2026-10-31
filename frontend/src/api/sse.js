// POST + SSE 客户端：后端 /api/v1/chat/stream 返回 text/event-stream。
// 使用 fetch + ReadableStream 手动解析（EventSource 只支持 GET）。
export async function streamChat({ sessionId, message, customerId, role = 'customer', onEvent, signal }) {
  const res = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Customer-Id': customerId || 'demo-customer',
      'X-Role': role,
    },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal,
  })

  if (!res.ok || !res.body) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = data.detail || detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatchData = (raw) => {
    const text = raw.trim()
    if (!text) return
    try {
      onEvent(JSON.parse(text))
    } catch {
      /* 忽略无法解析的行 */
    }
  }

  // 消费一个以空行分隔的 SSE 事件块
  const consumeBlock = (block) => {
    for (const line of block.split('\n')) {
      const t = line.trim()
      if (t.startsWith('data:')) dispatchData(t.slice(5))
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 事件分隔符可能是 \n 或 \r\n，统一规范为 \n 再切分
    buffer = buffer.replace(/\r\n/g, '\n')

    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      consumeBlock(buffer.slice(0, idx))
      buffer = buffer.slice(idx + 2)
    }
  }

  // 冲刷解码器残留字节，并兜底处理未消费数据
  buffer += decoder.decode()
  buffer = buffer.replace(/\r\n/g, '\n')
  if (buffer.trim()) consumeBlock(buffer)
}
