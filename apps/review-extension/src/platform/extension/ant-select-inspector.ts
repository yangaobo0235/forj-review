/** 在 MAIN world 读取 Vue/Ant 控件身份。只读组件状态，滚动并标记真实选项供 DOM 点击。 */
export function inspectAntSelect(token: string, expectedUrl: string,
  target?: {id: string; label: string; prepare: boolean}) {
  // 此函数被 chrome.scripting 序列化，所有运行依赖必须放在函数内部。
  interface VNode {
    key?: unknown; el?: Element; children?: unknown; component?: Instance;
    suspense?: {activeBranch?: VNode};
  }
  interface Instance {
    type?: {name?: string}; subTree?: VNode;
    props: {value?: unknown; options?: Record<string, unknown>[]; fieldNames?: {value?: string; label?: string}; disabled?: boolean;
      itemKey?: string; data?: {key: unknown; value: unknown; data: Record<string, unknown>}[]};
    exposed?: {scrollTo?: (value: {key: unknown}) => void};
  }
  const fail = (message: string) => ({ok: false as const, message});
  if (location.href !== expectedUrl || !/^[a-zA-Z0-9-]{1,100}$/.test(token)) return fail("页面或控件身份已变化");
  const controls = document.querySelectorAll(`[data-review-select-token="${token}"]`);
  if (controls.length !== 1) return fail("未找到唯一的当前车型控件");
  const control = controls[0] as HTMLElement;
  if (!control.isConnected || (control.getAttribute("role") !== "combobox" && !control.closest(".ant-select"))) return fail("下拉控件已变化");
  const walk = (roots: VNode[], visit: (node: VNode) => void) => {
    const seen = new Set<VNode>(), pending = [...roots];
    while (pending.length && seen.size < 100000) {
      const node = pending.pop();
      if (!node || typeof node !== "object" || seen.has(node)) continue;
      seen.add(node); visit(node);
      if (node.component?.subTree) pending.push(node.component.subTree);
      if (node.suspense?.activeBranch) pending.push(node.suspense.activeBranch);
      if (Array.isArray(node.children)) pending.push(...node.children);
    }
  };
  // _vnode 在 Vue 生产构建也存在，不依赖仅开发模式提供的 __vueParentComponent。
  const roots = Array.from(document.querySelectorAll("[data-v-app], #app"))
    .map(el => (el as Element & {_vnode?: VNode})._vnode).filter((node): node is VNode => Boolean(node));
  const instances = new Set<Instance>();
  walk(roots, node => {
    const instance = node.component;
    if (instance?.type?.name === "ASelect" && instance.subTree?.el?.contains(control)) instances.add(instance);
  });
  if (instances.size !== 1) return fail("无法读取当前下拉控件的真实选项 ID，请刷新业务页面后重试");
  const instance = [...instances][0]!;
  const selectedValue = instance.props.value == null ? "" : String(instance.props.value);
  if (!target) return {ok: true as const, selectedValue};
  const {value = "value", label = "label"} = instance.props.fieldNames || {};
  const fields = [{value: "id", label: "brandName"}, {value: "brandId", label: "brandName"},
    {value: "seriesId", label: "seriesName"}, {value: "modelId", label: "modelName"}];
  if (!fields.some(fields => fields.value === value && fields.label === label)) return fail("当前控件不是品牌、车系或车型选择框");
  const options = (instance.props.options || []).filter(option => String(option[value]) === target.id
    && String(option[label] ?? "").replace(/\s+/g, "") === target.label.replace(/\s+/g, ""));
  if (instance.props.disabled || options.length !== 1 || options[0]!.disabled) return fail("原页面当前下拉列表中没有对应的可用车型 ID，请核对页面范围与离线目录");
  const popupIds = `${control.getAttribute("aria-controls") || ""} ${control.getAttribute("aria-owns") || ""}`.trim().split(/\s+/);
  const popup = popupIds.map(id => document.getElementById(id)?.closest(".ant-select-dropdown")).find(Boolean);
  const lists = new Map<Instance, unknown>();
  walk([instance.subTree!], node => {
    const list = node.component;
    if (list?.props?.itemKey !== "key" || !list.subTree?.el || !popup?.contains(list.subTree.el)) return;
    const rows = list.props.data?.filter(row => String(row.value) === target.id
      && String(row.data[value]) === target.id && row.data[label] === options[0]![label]) || [];
    if (rows.length === 1) lists.set(list, rows[0]!.key);
  });
  if (lists.size > 1) return fail("当前车型存在多个选项列表，已停止回填");
  const list = [...lists.keys()][0];
  if (!list) return {ok: true as const, selectedValue}; // 弹层可能仍在挂载，调用方稍后重读。
  // 使用实时过滤后的扁平列表 key。自定义 fieldNames 时它可能是 rc-index-key-N，不能假设等于车型 ID。
  const key = lists.get(list);
  const matches = new Set<Element>();
  walk([list.subTree!], node => {
    const element = node.component?.subTree?.el;
    if (String(node.key) === String(key) && element?.matches?.(".ant-select-item-option")
      && element.isConnected && popup?.contains(element)) matches.add(element);
  });
  if (matches.size > 1) return fail("页面存在重复的车型 ID，已停止回填");
  const element = [...matches][0];
  if (!element && target.prepare) instance.exposed?.scrollTo?.({key});
  const optionToken = element ? `${token}-option` : undefined;
  popup?.querySelectorAll("[data-review-option-token]").forEach(el => el.removeAttribute("data-review-option-token"));
  if (element) element.setAttribute("data-review-option-token", optionToken!);
  return {ok: true as const, selectedValue, optionToken};
}
