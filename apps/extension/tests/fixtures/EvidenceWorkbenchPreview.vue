<script setup>
/* Visual fixture: synthetic data only; no model, page writes or external requests. */

import EvidenceReviewWorkbench from "../../src/features/review/ui/EvidenceReviewWorkbench.vue";
import "../../src/app/theme.css";
import "../../src/app/App.css";

const field = (key, label, status, value, reason) => ({
  step_id: `FIELD-${key}`, sequence: key === "vehicle.vin" ? 1 : 2, category: "FIELD", display_target: "PAGE_FIELD",
  page_field: key, label, result_status: status, requires_reviewer_action: status !== "MATCH", reason, writable: true,
  values: [{source: "机动车登记证书 · 当前车辆", value, image_id: "registration", uncertain: key === "vehicle.owner"}], evidence: [],
});
const previewImage = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="180"><rect width="300" height="180" fill="#e5edf8"/><text x="150" y="85" text-anchor="middle" font-size="18" fill="#334155">合成登记证书</text><text x="150" y="115" text-anchor="middle" font-size="13" fill="#64748b">仅用于界面验证</text></svg>');
const review = {business_type: "synthetic", region: "default", profile_version: "config-synthetic", qr_checks: [], review_tasks: [
  field("vehicle.vin", "车辆识别代号", "CONFLICT", "LSVAA123456789012", "页面值与登记证书有一位字符不同，请核对原图后选择是否回填。"),
  field("vehicle.owner", "机动车所有人", "INSUFFICIENT", "合成物流有限公司", "材料识别存在不确定字符，请查看原图人工核对。"),
  {step_id: "MATERIAL-GROUP", sequence: 3, category: "MATERIAL", display_target: "ASSISTANT", label: "材料完整性", result_status: "INSUFFICIENT", requires_reviewer_action: true, reason: "本次样本缺少发票，尚不能完成金额核对。", values: [], evidence: []},
]};
const pageData = {collectionId: "synthetic", pageFields: {"vehicle.vin": "LSVAA123456789013", "vehicle.owner": "合成物流有限公司"}, images: [{imageId: "registration", src: previewImage}]};
const narrow = new URLSearchParams(location.search).has("narrow");
const focusImage = async () => ({ok: true});
const fillValue = async () => ({ok: true, message: "合成回读成功"});
const rerun = async () => {};
</script>

<template>
  <main :style="{maxWidth: narrow ? '390px' : '1040px', margin: '24px auto', padding: '16px'}">
    <p :style="{fontSize: '12px', color: '#64748b'}">合成样本 · {{ narrow ? "390px 侧栏" : "桌面" }} · 页面动作使用模拟回执</p>
    <EvidenceReviewWorkbench :review="review" :page-data="pageData" :on-focus-image="focusImage" :on-apply-page-field-value="fillValue" :on-rerun="rerun" />
  </main>
</template>
