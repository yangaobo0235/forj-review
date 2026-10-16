export interface ManifestField {
  key: string;
  label: string;
  aliases: string[];
  section: string;
  section_required: boolean;
  reviewable: boolean;
  locator_name?: string;
  locator_id?: string;
  occurrence?: number;
}

export interface ManifestPageGroup {
  occurrence?: number;
  label: string;
  scope: string;
  title: string;
}

export interface ManifestMaterial {
  document_type: string;
  label: string;
  hints: string[];
  /** 上传槽位 [业务分区, 组内序号]，用于类型不确定时兜底。 */
  slots: [string, number][];
}

export interface CollectManifest {
  page_id?: string;
  business_type: string;
  region?: string;
  profile_version?: string;
  /** 该业务认定的材料分区，如旧车 / 新车。 */
  scopes: string[];
  /** 页面控件区域，与材料业务分区独立；旧清单缺省时沿用历史识别。 */
  page_sections?: { key: string; title: string }[];
  page_groups: ManifestPageGroup[];
  fields: ManifestField[];
  materials: ManifestMaterial[];
  /** 允许写回的字段键；缺省视为不允许任何写回。 */
  writable_fields?: string[];
  /** 允许写回的控件类型；缺省或空数组表示不限制（历史业务口径）。 */
  writable_control_kinds?: string[];
  /** 页面指纹锚点：能唯一标识这条审核记录的字段。缺省时退回内置表。 */
  identity_anchors?: string[];
  /** Explicit record identifiers; an empty list intentionally disables identity-dependent actions. */
  record_identity_fields?: string[];
}

