<script setup>
import { ref, watch, nextTick } from 'vue'
import GuidanceCard from './GuidanceCard.vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
})
const emit = defineEmits(['apply-return'])

const listRef = ref(null)

function scrollToBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function pct(v) {
  if (v == null) return ''
  return Math.round(v * 100) + '%'
}

watch(() => props.messages, scrollToBottom, { deep: true })
</script>

<template>
  <div ref="listRef" class="chat-window">
    <el-empty v-if="!messages.length" description="开始对话吧，试试下方快捷示例" />

    <div v-for="m in messages" :key="m.id" class="message" :class="'msg-' + m.role">
      <div v-if="m.routing" class="routing-line">
        <el-tag size="small" type="info" effect="plain">
          {{ m.routing.agent_display || m.routing.route_to }}
        </el-tag>
        <el-tag v-if="m.routing.urgency === 'high'" size="small" type="danger" effect="dark">紧急</el-tag>
        <span v-if="m.routing.reason" class="routing-reason">{{ m.routing.reason }}</span>
      </div>

      <div class="bubble">
        <span v-if="m.role === 'user'" class="bubble-text">{{ m.content }}</span>

        <template v-else>
          <span class="bubble-text">{{ m.content }}</span>
          <span v-if="m.streaming && !m.content" class="typing"><i></i><i></i><i></i></span>

          <GuidanceCard v-if="m.guidance" :guidance="m.guidance" @apply-return="emit('apply-return')" />

          <div v-if="m.meta" class="meta-line">
            <template v-if="m.meta.sources && m.meta.sources.length">
              <el-tag v-for="(s, i) in m.meta.sources" :key="'s' + i" size="small" type="success" effect="plain">{{ s }}</el-tag>
            </template>
            <el-tag v-if="m.meta.answer_mode" size="small" type="primary" effect="plain">{{ m.meta.answer_mode }}</el-tag>
            <el-tag v-if="m.meta.confidence != null" size="small" type="warning" effect="plain">置信度 {{ pct(m.meta.confidence) }}</el-tag>
            <el-tag v-if="m.meta.stage" size="small" type="primary" effect="plain">{{ m.meta.stage }}</el-tag>
            <el-tag v-if="m.meta.order_no" size="small" type="info" effect="plain">订单 {{ m.meta.order_no }}</el-tag>
            <el-tag v-if="m.meta.risk_level" size="small" :type="m.meta.risk_level === 'high' ? 'danger' : 'warning'" effect="plain">风险 {{ m.meta.risk_level }}</el-tag>
            <el-tag v-if="m.meta.escalated" size="small" type="danger" effect="dark">已转人工</el-tag>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-window {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}

.message {
  margin-bottom: 18px;
  display: flex;
  flex-direction: column;
}

.msg-user {
  align-items: flex-end;
}

.msg-assistant {
  align-items: flex-start;
}

.routing-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
  color: #909399;
}

.routing-reason {
  color: #909399;
  font-size: 12px;
}

.bubble {
  max-width: 72%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.msg-user .bubble {
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 2px;
}

.msg-assistant .bubble {
  background: #f4f4f5;
  color: #303133;
  border-bottom-left-radius: 2px;
}

.meta-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.typing {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 4px 0;
}

.typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: blink 1.2s infinite both;
}

.typing i:nth-child(2) {
  animation-delay: 0.2s;
}

.typing i:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.2;
  }
  40% {
    opacity: 1;
  }
}
</style>
