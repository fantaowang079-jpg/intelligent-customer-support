<script setup>
import { ref } from 'vue'
import { streamChat } from '../api/sse'
import ChatWindow from '../components/ChatWindow.vue'
import ReturnApplyForm from '../components/ReturnApplyForm.vue'

const customerId = ref('demo-customer')
const sessionId = ref(newSessionId())
const messages = ref([])
const input = ref('')
const sending = ref(false)
const showReturnForm = ref(false)

const QUICK_EXAMPLES = [
  { label: '你好', text: '你好' },
  { label: '查订单', text: '查一下我的订单' },
  { label: '退换货政策', text: '七天无理由退货要什么条件？' },
  { label: '我要投诉', text: '我要投诉，太差劲了' },
  { label: '转人工', text: '转人工' },
]

function newSessionId() {
  return 's-' + Math.random().toString(36).slice(2, 10)
}

function uid() {
  return Math.random().toString(36).slice(2)
}

function resetSession() {
  messages.value = []
  sessionId.value = newSessionId()
}

async function send(text) {
  const msg = (text ?? input.value).trim()
  if (!msg || sending.value) return
  input.value = ''
  sending.value = true

  messages.value.push({ id: uid(), role: 'user', content: msg })
  // 用索引记录 assistant 气泡位置：必须通过 messages.value[index] 拿到「响应式代理」再改，
  // 直接改原始对象不会触发重新渲染，会导致流式失效。
  const assistantIndex = messages.value.length
  messages.value.push({
    id: uid(),
    role: 'assistant',
    content: '',
    streaming: true,
    routing: null,
    meta: null,
    guidance: null,
  })

  try {
    await streamChat({
      sessionId: sessionId.value,
      message: msg,
      customerId: customerId.value,
      role: 'customer',
      onEvent: (ev) => handleEvent(assistantIndex, ev),
    })
  } catch (e) {
    messages.value[assistantIndex].content = '请求失败：' + e.message
  } finally {
    messages.value[assistantIndex].streaming = false
    sending.value = false
  }
}

function handleEvent(index, ev) {
  const assistant = messages.value[index]
  switch (ev.type) {
    case 'routing_decision':
      assistant.routing = ev
      break
    case 'token':
      assistant.content += ev.content
      break
    case 'guidance':
      assistant.guidance = ev
      break
    case 'meta':
      assistant.meta = ev
      break
    case 'error':
      assistant.content += (assistant.content ? '\n' : '') + '⚠️ ' + ev.message
      break
    case 'done':
      break
  }
}
</script>

<template>
  <div class="chat-page">
    <div class="chat-toolbar">
      <div class="toolbar-left">
        <span class="field-label">客户 ID</span>
        <el-input v-model="customerId" size="small" style="width: 160px" placeholder="demo-customer" />
        <span class="field-label">会话 ID</span>
        <el-input :model-value="sessionId" size="small" style="width: 160px" readonly />
        <el-button size="small" @click="resetSession">新会话</el-button>
      </div>
    </div>

    <ChatWindow :messages="messages" @apply-return="showReturnForm = true" />

    <div class="chat-input">
      <div class="quick-examples">
        <el-button
          v-for="ex in QUICK_EXAMPLES"
          :key="ex.text"
          size="small"
          round
          @click="send(ex.text)"
        >{{ ex.label }}</el-button>
      </div>
      <div class="input-row">
        <div class="input-box">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="请输入您的问题，回车发送（Shift+Enter 换行）"
            @keydown.enter.exact.prevent="send()"
          />
        </div>
        <el-button type="primary" :loading="sending" @click="send()">发送</el-button>
      </div>
    </div>

    <ReturnApplyForm v-model="showReturnForm" :customer-id="customerId" />
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-toolbar {
  flex: none;
  display: flex;
  align-items: center;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.field-label {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
}

.chat-input {
  flex: none;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px;
}

.quick-examples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.input-box {
  flex: 1;
}
</style>
