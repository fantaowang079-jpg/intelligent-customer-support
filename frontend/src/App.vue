<script setup>
import { ref } from 'vue'
import CustomerChat from './views/CustomerChat.vue'
import AgentWorkbench from './views/AgentWorkbench.vue'

const active = ref('customer')
</script>

<template>
  <div class="app">
    <header class="app-header">
      <div class="brand">
        <span class="brand-dot"></span>
        <span class="brand-name">智能客服中台</span>
        <span class="brand-sub">CustomerService Agent</span>
      </div>
      <el-tag type="info" effect="plain" size="small">LangChain · LangGraph · FastAPI</el-tag>
    </header>

    <main class="app-main">
      <el-tabs v-model="active" stretch class="app-tabs">
        <el-tab-pane label="客户聊天" name="customer">
          <CustomerChat />
        </el-tab-pane>
        <el-tab-pane label="坐席工作台" name="agent">
          <AgentWorkbench />
        </el-tab-pane>
      </el-tabs>
    </main>
  </div>
</template>

<style scoped>
.app {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.app-header {
  height: 56px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.brand-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #409eff;
  align-self: center;
}

.brand-name {
  font-size: 18px;
  font-weight: 600;
}

.brand-sub {
  font-size: 13px;
  color: #909399;
}

.app-main {
  flex: 1;
  min-height: 0;
  padding: 16px 24px 24px;
}

.app-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.app-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
}

.app-tabs :deep(.el-tab-pane) {
  height: 100%;
}
</style>
