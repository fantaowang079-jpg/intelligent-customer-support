<script setup>
defineProps({
  guidance: { type: Object, required: true },
})
defineEmits(['apply-return'])
</script>

<template>
  <div class="guidance-card">
    <div class="guidance-msg">{{ guidance.message }}</div>
    <div v-if="guidance.action_url === '/return/apply'" class="guidance-actions">
      <el-button type="primary" size="small" @click="$emit('apply-return')">
        {{ guidance.action_label || '发起退换货申请' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.guidance-card {
  margin-top: 10px;
  padding: 10px 12px;
  background: #f0f7ff;
  border: 1px solid #d9ecff;
  border-radius: 8px;
}

.guidance-msg {
  font-size: 13px;
  color: #409eff;
  line-height: 1.6;
}

.guidance-actions {
  margin-top: 8px;
}
</style>
