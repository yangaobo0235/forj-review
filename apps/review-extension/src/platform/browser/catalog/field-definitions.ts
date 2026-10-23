/** 当前采集声明及标签索引。没有清单时不推导业务字段。 */
import type { FieldDefinition } from "../dom.ts";
import { manifestFieldDefinitions } from "./collect-manifest.ts";

export const normalizeText = (value: unknown) =>
  String(value || "")
    .replace(/[＊*]/g, "")
    .replace(/\s+/g, "")
    .replace(/[：:]$/, "");
const EMPTY_DEFINITIONS: Record<string, FieldDefinition> = {};

/** Fields are provided exclusively by the current backend manifest. */
export const fieldDefinitions = () => manifestFieldDefinitions() ?? EMPTY_DEFINITIONS;
let indexedDefinitions: Record<string, FieldDefinition> | null = null;
let labelIndex = new Map<string, Array<{ field: string; definition: FieldDefinition }>>();
export const definitionsByLabel = () => {
  const definitions = fieldDefinitions();
  if (definitions !== indexedDefinitions) {
    indexedDefinitions = definitions;
    labelIndex = new Map();
    for (const [field, definition] of Object.entries(definitions)) {
      for (const label of new Set(definition.aliases.map(normalizeText))) {
        const entries = labelIndex.get(label) || [];
        entries.push({ field, definition });
        labelIndex.set(label, entries);
      }
    }
  }
  return labelIndex;
};

export const definitionForLabel = (label: string) => {
  const normalized = normalizeText(label);
  const matches = definitionsByLabel().get(normalized) || [];
  if (matches.length !== 1) return null;
  const { field, definition } = matches[0];
  return { field, ...definition };
};

export const isKnownLabel = (label: string) => {
  const normalized = normalizeText(label);
  return definitionsByLabel().has(normalized);
};

export const isSectionRequired = (field: string) =>
  Boolean(fieldDefinitions()[field]?.sectionRequired);
