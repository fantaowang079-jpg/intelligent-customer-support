<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { http } from '../api/http'

// 坐席身份：演示环境用固定坐席 ID，生产可替换为登录态
const agentHeaders = { 'X-Role': 'agent', 'X-Customer-Id': 'agent-01' }

const activeTab = ref('return')

// ── 退换货审批 ─────────────────────────────
const returns = ref([])
const returnLoading = ref(false)
const returnDialog = ref(false)
const returnDetail = ref(null)
const returnDetailLoading = ref(false)
const confirmAction = ref('approve')
const confirmNote = ref('')
const confirmLoading = ref(false)

// ── 投诉接管 ─────────────────────────────
const complaints = ref([])
const complaintLoading = ref(false)

async function loadReturns() {
  returnLoading.value = true
  try {
    const data = await http.get('/return/pending', { headers: agentHeaders })
    returns.value = data.items || []
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    returnLoading.value = false
  }
}

async function loadComplaints() {
  complaintLoading.value = true
  try {
    const data = await http.get('/complaint/pending', { headers: agentHeaders })
    complaints.value = data.items || []
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    complaintLoading.value = false
  }
}

async function openReview(row) {
  returnDialog.value = true
  returnDetailLoading.value = true
  returnDetail.value = null
  confirmAction.value = 'approve'
  confirmNote.value = ''
  try {
    returnDetail.value = await http.get(`/return/${row.application_id}/review`, { headers: agentHeaders })
  } catch (e) {
    ElMessage.error(e.message)
    returnDialog.value = false
  } finally {
    returnDetailLoading.value = false
  }
}

async function submitReview() {
  if (!returnDetail.value) return
  confirmLoading.value = true
  try {
    const res = await http.post(
      `/return/${returnDetail.value.application_id}/confirm`,
      { action: confirmAction.value, note: confirmNote.value },
      { headers: agentHeaders },
    )
    ElMessage.success(res.message || '审批完成')
    returnDialog.value = false
    loadReturns()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    confirmLoading.value = false
  }
}

async function resolveComplaint(row) {
  let note = ''
  try {
    const { value } = await ElMessageBox.prompt('请输入接管备注（可留空）', '接管投诉', {
      confirmButtonText: '确认接管',
      cancelButtonText: '取消',
      inputValue: '',
    })
    note = value || ''
  } catch {
    return // 用户取消
  }

  try {
    const res = await http.post(
      `/complaint/${row.escalation_id}/resolve`,
      { note },
      { headers: agentHeaders },
    )
    ElMessage.success(res.message || '已接管')
    loadComplaints()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(() => {
  loadReturns()
  loadComplaints()
})
</script>

<template>
  <div class="workbench">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="退换货审批" name="return">
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">待审批申请</span>
            <el-button size="small" :loading="returnLoading" @click="loadReturns">刷新</el-button>
          </div>
          <el-table :data="returns" v-loading="returnLoading" empty-text="暂无待审批申请" border>
            <el-table-column prop="order_no" label="订单号" width="120" />
            <el-table-column prop="customer_id" label="客户" width="150" />
            <el-table-column prop="reason" label="退货原因" min-width="200" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="140">
              <template #default="{ row }">
                <el-tag size="small" type="warning">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" link @click="openReview(row)">审批</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="投诉接管" name="complaint">
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">待接管投诉</span>
            <el-button size="small" :loading="complaintLoading" @click="loadComplaints">刷新</el-button>
          </div>
          <el-table :data="complaints" v-loading="complaintLoading" empty-text="暂无待接管投诉" border>
            <el-table-column prop="customer_id" label="客户" width="150" />
            <el-table-column prop="message" label="投诉内容" min-width="220" show-overflow-tooltip />
            <el-table-column label="风险等级" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.risk_level === 'high' ? 'danger' : 'warning'">
                  {{ row.risk_level }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="识别原因" min-width="180" show-overflow-tooltip />
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" @click="resolveComplaint(row)">接管</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 退换货审批详情 -->
    <el-dialog v-model="returnDialog" title="退换货审批详情" width="520px" :close-on-click-modal="false">
      <div v-loading="returnDetailLoading">
        <template v-if="returnDetail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="订单号">{{ returnDetail.order_no }}</el-descriptions-item>
            <el-descriptions-item label="商品">{{ returnDetail.product_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="金额">¥{{ returnDetail.amount }}</el-descriptions-item>
            <el-descriptions-item label="退货原因">{{ returnDetail.reason }}</el-descriptions-item>
          </el-descriptions>

          <div class="audit-box">
            <div class="audit-title">AI 规则审核</div>
            <el-alert
              v-if="returnDetail.rule_audit"
              :type="returnDetail.rule_audit.needs_review ? 'warning' : 'success'"
              :title="returnDetail.rule_audit.reason || ''"
              :closable="false"
              show-icon
            />
          </div>

          <div class="review-form">
            <el-radio-group v-model="confirmAction">
              <el-radio value="approve">同意</el-radio>
              <el-radio value="reject">驳回</el-radio>
            </el-radio-group>
            <el-input v-model="confirmNote" type="textarea" :rows="2" placeholder="审批备注（可留空）" />
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="returnDialog = false">取消</el-button>
        <el-button type="primary" :loading="confirmLoading" @click="submitReview">提交审批</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.workbench {
  height: 100%;
  overflow-y: auto;
}

.panel {
  padding-top: 4px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.audit-box {
  margin-top: 16px;
}

.audit-title {
  font-size: 13px;
  color: #606266;
  margin-bottom: 8px;
}

.review-form {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>
