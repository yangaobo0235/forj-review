interface Change {before: Record<string, unknown>; after: Record<string, unknown>}
interface FieldDiff {
  added?: Record<string, Record<string, unknown>>;
  removed?: Record<string, Record<string, unknown>>;
  changed?: Record<string, Change>;
}
const labels: Record<string, string> = {
  label: "名称", aliases: "页面别名", page_section: "页面区域", section: "审核分区",
  locator_id: "控件 ID", locator_name: "控件名称", occurrence: "同名序号",
  sources: "材料来源", mode: "比较方式", normalizer: "规范化", max_age_days: "日期范围",
  evidence: "材料字段与证据映射",
};
export function fieldChanges(diff: unknown) {
  const value = diff as {field_changes?: FieldDiff & {page_id: string}; pages?: {changed?: Record<string, {fields?: FieldDiff}>}} | null;
  const pages = value?.field_changes ? {[value.field_changes.page_id]: {fields: value.field_changes}} : value?.pages?.changed ?? {};
  return Object.entries(pages).flatMap(([page, change]) => {
    const fields = change.fields ?? {};
    return [
      ...Object.entries(fields.added ?? {}).map(([key, field]) => ({page, key, kind: "新增", label: String(field.label ?? key), detail: "自动生成采集项、材料提取和审核展示"})),
      ...Object.entries(fields.removed ?? {}).map(([key, field]) => ({page, key, kind: "删除", label: String(field.label ?? key), detail: "移除字段及其拥有的路由；共享输入保留"})),
      ...Object.entries(fields.changed ?? {}).map(([key, field]) => ({page, key, kind: "修改", label: String(field.after.label ?? key), detail: [...new Set([...Object.keys(field.before), ...Object.keys(field.after)])].filter(name => JSON.stringify(field.before[name]) !== JSON.stringify(field.after[name])).map(name => labels[name] ?? name).join("、")})),
    ];
  });
}
