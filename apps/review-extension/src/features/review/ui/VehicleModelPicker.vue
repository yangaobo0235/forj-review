<script setup lang="ts">
import { computed } from "vue";
import type { PageData, ReviewTask } from "../../../shared/contracts/review";
import type { VehicleModelReview } from "../model/use-vehicle-model-review";
import { evidencePresentation } from "../presentation/evidence";

const props = defineProps<{model: VehicleModelReview; pageData?: PageData | null; disabled: boolean;
  step?: ReviewTask; onFocusImage?: (id: string) => Promise<void>}>();
// 同一张图上的两个型号共用原图入口，分别保留原始读值及不确定性。
const evidence = computed(() => {
  const facts = props.step?.evidence?.length ? props.step.evidence : props.step?.values || [];
  const groups = new Map<string, {source: string; image?: PageData["images"][number];
    values: {label: string; value: string; uncertain?: boolean}[]}>();
  for (const fact of facts) {
    if (fact.source === "申请页面字段" || fact.value == null || typeof fact.value === "object") continue;
    const image = props.pageData?.images?.find(item => fact.image_id ? item.imageId === fact.image_id : fact.image_index != null && item.index === fact.image_index);
    const key = image?.imageId || image?.src || fact.image_id || `${fact.source}:${fact.image_index ?? ""}`;
    const source = fact.source === "图片识别" ? image?.group || evidencePresentation(fact).label : fact.source;
    const group = groups.get(key) || {source, image, values: []};
    const field = "field" in fact ? fact.field : "";
    const label = field === "vehicle.engine_model" ? "发动机型号" : field === "vehicle.announcement_model" ? "整车型号" : "材料读值";
    group.values.push({label, value: String(fact.value), uncertain: fact.uncertain});
    groups.set(key, group);
  }
  return [...groups.entries()];
});
function gearbox(config: Record<string, unknown>) {
  return [config.gearboxModel, config.shiftWay, config.rearAxleRatio ? `后桥速比 ${config.rearAxleRatio}` : ""]
    .filter(value => value && !["--", "未知", "未提供"].includes(String(value))).join(" · ");
}
</script>

<template>
  <section class="model-picker" aria-label="车型候选与规格参数回填">
    <details v-if="evidence.length" class="model-evidence">
      <summary>材料依据 <span>{{ evidence.length }} 份 · 展开核对读值与原图</span></summary>
      <div v-for="[key, group] in evidence" :key="key" class="model-evidence-group">
        <strong>{{ group.source }}</strong>
        <div v-for="(value, index) in group.values" :key="index" class="model-evidence-value"><span>{{ value.label }}</span><b>{{ value.value }}</b><small v-if="value.uncertain" class="model-warning">识别不确定，请核对原图</small></div>
        <div v-if="group.image" class="model-evidence-image"><img :src="group.image.src" :alt="group.source"/><button v-if="group.image.imageId && onFocusImage" type="button" class="model-text-action" :disabled="disabled || model.operation.filling" @click="onFocusImage(group.image.imageId)">查看原图</button></div>
      </div>
    </details>
    <div class="model-section-title"><h3>匹配候选车型</h3><button v-if="model.state.manual" type="button" class="model-text-action" :disabled="disabled || model.operation.filling" @click="model.useMaterials">恢复材料条件</button></div>
    <p class="model-query-scope">查询范围：{{ model.pageScope.brandName || '未选择品牌' }} / {{ model.pageScope.seriesName || '未选择车系' }} / {{ model.pageScope.typeLevel || '未选择车辆类型' }}</p>
    <form class="model-search-form" @submit.prevent="model.search">
      <label for="model-announcement">整车型号</label><input id="model-announcement" :value="model.state.input.announcementModel" @input="model.edit('announcementModel', ($event.target as HTMLInputElement).value)" maxlength="80" :disabled="disabled || model.operation.filling" placeholder="请输入完整整车型号"/>
      <label for="model-engine">发动机型号</label><input id="model-engine" :value="model.state.input.engineModel" @input="model.edit('engineModel', ($event.target as HTMLInputElement).value)" maxlength="80" :disabled="disabled || model.operation.filling" placeholder="请输入完整发动机型号"/>
      <button type="submit" :disabled="disabled || !model.scopeReady || model.state.status === 'SEARCHING' || model.operation.filling || !model.state.input.announcementModel.trim() || !model.state.input.engineModel.trim()">{{ model.state.status === 'SEARCHING' ? '查询中…' : '搜索车型' }}</button>
    </form>
    <small class="model-condition-origin">{{ model.state.manual ? '使用手动输入条件；后续材料识别不会覆盖' : '默认使用材料提取值；两个型号齐全后自动查询' }}</small>
    <p v-for="issue in model.source?.issues || []" :key="issue" class="model-warning">{{ issue }}</p>
    <div class="model-search-status" :class="{'model-warning': model.state.status === 'ERROR'}" role="status" aria-live="polite">{{ model.state.message }}</div>
    <div v-if="model.state.candidates.length" class="model-options" role="radiogroup" aria-label="匹配车型">
      <label v-for="item in model.pageCandidates" :key="item.modelId" class="model-option" :class="{selected: model.state.selectedId === item.modelId}">
        <input :checked="model.state.selectedId === item.modelId" @change="model.select(item.modelId)" type="radio" name="vehicle-model-candidate" :value="item.modelId" :disabled="disabled || model.operation.filling"/>
        <span class="model-option-content"><strong>{{ item.modelName }}</strong><small v-if="gearbox(item.config)">{{ gearbox(item.config) }}</small><small v-if="item.sameNameCount > 1">车型编号 {{ item.modelId }} · 同名记录 {{ item.sameNameCount }} 条</small><b v-if="item.modelName === pageData?.pageFields['vehicle.model']" class="model-current">名称与页面一致</b><small v-for="warning in item.warnings" :key="warning" class="model-warning">{{ warning }}</small></span>
      </label>
    </div>
    <nav v-if="model.pageCount > 1" class="model-pagination" aria-label="候选车型分页">
      <small aria-live="polite">共 {{ model.state.candidates.length }} 条 · 当前 {{ model.range }} 条</small>
      <div><button type="button" aria-label="上一页" :disabled="disabled || model.operation.filling || model.state.page === 1" @click="model.setPage(model.state.page - 1)">上一页</button><template v-for="(page, index) in model.pageButtons" :key="index"><button v-if="typeof page === 'number'" type="button" :aria-current="page === model.state.page ? 'page' : undefined" :aria-label="`第 ${page} 页`" :disabled="disabled || model.operation.filling" @click="model.setPage(page)">{{ page }}</button><span v-else>…</span></template><button type="button" aria-label="下一页" :disabled="disabled || model.operation.filling || model.state.page === model.pageCount" @click="model.setPage(model.state.page + 1)">下一页</button></div>
    </nav>
    <div v-if="model.selected" class="model-selection"><small>已选车型 · 编号 {{ model.selected.modelId }}</small><strong>{{ model.selected.modelName }}</strong><small v-if="gearbox(model.selected.config)">{{ gearbox(model.selected.config) }}</small><button v-if="model.selectedPage !== model.state.page" type="button" class="model-text-action" :disabled="disabled || model.operation.filling" @click="model.setPage(model.selectedPage)">回到所选页 · 第 {{ model.selectedPage }} 页</button></div>
    <template v-if="model.selected && model.plan">
      <details class="model-preview" :key="model.state.selectedId"><summary>回填预览：将更新 {{ model.changedCount }} 项参数（共 {{ model.plan.values.length }} 项）</summary><table><thead><tr><th>字段</th><th>当前值</th><th>将回填的值</th></tr></thead><tbody><tr v-for="item in model.plan.values" :key="item.field"><td>{{ item.label }}</td><td>{{ pageData?.pageFields[item.field] || '未填写' }}</td><td>{{ item.value || '车型库未提供' }}</td></tr></tbody></table></details>
      <p v-for="warning in model.plan.warnings" :key="warning" class="model-warning">{{ warning }}</p>
    </template>
    <div class="model-fill-actions"><button type="button" :disabled="Boolean(model.fillDisabledReason)" aria-describedby="vehicle-model-fill-reason" @click="model.fill">{{ model.operation.filling ? '正在回填…' : '回填整个规格参数' }}</button><small id="vehicle-model-fill-reason">{{ model.fillDisabledReason || '将联动回填品牌、车系、车型及规格参数，并逐项回读确认' }}</small></div>
    <p v-if="model.operation.fillMessage" role="status" aria-live="polite">{{ model.operation.fillMessage }}</p>
  </section>
</template>

<style scoped>
.model-picker {font-size: 13px; line-height: 1.6; min-width: 0;}
.model-section-title {display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px; margin-top: 16px;}
h3 {font-size: 14px; margin: 0;}
.model-condition-origin, .model-fill-actions small, .model-pagination small, .model-selection small {color: #64748b;}
.model-query-scope {margin: 6px 0; color: #475569; overflow-wrap: anywhere;}
.model-search-form {display: grid; grid-template-columns: 70px minmax(0, 1fr) auto; gap: 8px; align-items: center; margin: 10px 0 6px; padding: 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;}
.model-search-form label {margin: 0; white-space: nowrap; font-weight: 500;}
.model-search-form input {width: 100%; min-width: 0; box-sizing: border-box; border: 1px solid #cbd5e1; border-radius: 5px; padding: 8px; font: inherit; height: 36px;}
.model-search-form label:nth-of-type(2) {grid-row: 2; grid-column: 1;}
.model-search-form input:nth-of-type(2) {grid-row: 2; grid-column: 2;}
.model-search-form button {grid-column: 3; grid-row: 1 / 3; align-self: center; white-space: nowrap;}
.model-search-status {margin: 12px 0; color: #475569;}
button {width: auto; border: 0; background: #1677ff; color: white; padding: 8px 12px; border-radius: 5px; cursor: pointer; font: inherit; line-height: 20px;}
button:disabled {opacity: .5; cursor: default;}
button.model-text-action {background: transparent; color: #1261bd; padding: 3px 0;}
.model-options {display: grid; gap: 8px;}
.model-option {display: flex; align-items: flex-start; gap: 9px; margin: 0; padding: 12px; border: 1px solid #dbe3e8; border-radius: 8px; cursor: pointer; font-weight: normal;}
.model-option.selected {border-color: #1677ff; background: #f0f7ff;}
.model-option input {width: 15px; height: 15px; flex-shrink: 0; margin: 4px 0 0; padding: 0;}
.model-option-content {min-width: 0; flex: 1;}
.model-option strong, .model-selection strong {display: block; overflow-wrap: break-word; word-break: normal; white-space: normal; font-weight: 600; line-height: 1.7;}
.model-option small {display: block; color: #64748b; margin-top: 4px;}
.model-current {display: inline-block; color: #1261bd; background: #eaf3ff; border-radius: 4px; padding: 1px 6px; font-size: 11px; margin-top: 4px;}
.model-warning, .model-option .model-warning {color: #95610d;}
.model-pagination {display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; margin-top: 12px;}
.model-pagination div {display: flex; flex-wrap: wrap; gap: 4px; align-items: center;}
.model-pagination button {padding: 4px 8px; color: #475569; background: white; border: 1px solid #dbe3e8; font-size: 12px;}
.model-pagination button[aria-current="page"] {color: #1261bd; background: #edf5ff; border-color: #1677ff;}
.model-selection {display: grid; justify-items: start; gap: 4px; margin-top: 14px; padding: 10px 12px; border-left: 3px solid #1677ff; background: #f8fafc; border-radius: 4px;}
.model-evidence {padding: 0; border: 0; margin-top: 12px;}
.model-evidence summary {color: #475569; cursor: pointer;}
.model-evidence summary span {color: #64748b; font-size: 12px; margin-left: 5px;}
.model-evidence-group {padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px; margin-top: 8px;}
.model-evidence-value {display: flex; flex-wrap: wrap; gap: 4px 8px; margin-top: 4px; overflow-wrap: anywhere;}
.model-evidence-value span {color: #64748b;}
.model-evidence-image {display: flex; align-items: center; gap: 10px; margin-top: 8px;}
.model-evidence-image img {width: 72px; height: 50px; object-fit: contain; background: #f8fafc; border-radius: 4px;}
.model-preview {margin-top: 14px;}
.model-preview summary {cursor: pointer; color: #1261bd;}
table {width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 12px; margin: 12px 0;}
th, td {border-bottom: 1px solid #e2e8f0; text-align: left; padding: 7px 5px; overflow-wrap: anywhere;}
.model-fill-actions {display: grid; justify-items: start; gap: 6px; margin-top: 16px;}
@media (max-width: 380px) {.model-search-form {grid-template-columns: 64px minmax(0, 1fr) auto; padding: 8px; gap: 6px;} .model-search-form button {padding: 8px;} }
</style>
