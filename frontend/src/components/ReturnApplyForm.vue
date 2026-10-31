<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '../api/http'

const visible = defineModel({ type: Boolean, default: false })

const props = defineProps({
  customerId: { type: String, default: 'demo-customer' },
})

const emit = defineEmits(['submitted'])

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ order_no: '', reason: '' })

const rules = {
  order_no: [{ required: true, message: '请输入订单号', trigger: 'blur' }],
  reason: [{ required: true, message: '请输入退货原因', trigger: 'blur' }],
}

function reset() {
  form.order_no = ''
  form.reason = ''
}

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    const res = await http.post(
      '/return/apply',
      { order_no: form.order_no.trim(), reason: form.reason.trim() },
      { headers: { 'X-Customer-Id': props.customerId, 'X-Role': 'customer' } },
    )
    ElMessage.success(res.message || '申请已提交')
    emit('submitted', res)
    visible.value = false
    reset()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="发起退换货申请" width="440px" :close-on-click-modal="false" @closed="reset">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
      <el-form-item label="订单号" prop="order_no">
        <el-input v-model="form.order_no" placeholder="例如 88001" />
      </el-form-item>
      <el-form-item label="退货原因" prop="reason">
        <el-input v-model="form.reason" type="textarea" :rows="3" placeholder="请描述退货原因" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">提交申请</el-button>
    </template>
  </el-dialog>
</template>
