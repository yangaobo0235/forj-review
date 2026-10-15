/** Native and framework controls share DOM identity; control-specific properties are optional. */
export interface DomElement extends HTMLElement {
  value?: string;
  disabled?: boolean;
  readOnly?: boolean;
  checked?: boolean;
  selected?: boolean;
  options?: HTMLOptionsCollection;
  selectedOptions?: HTMLCollectionOf<HTMLOptionElement>;
}

export type DomRoot = ParentNode & {
  defaultView?: (Window & typeof globalThis) | null;
  getElementById?: (id: string) => HTMLElement | null;
};

export interface FieldDefinition {
  aliases: string[];
  section: string;
  reviewable?: boolean;
  sectionRequired?: boolean;
  locatorName?: string;
  locatorId?: string;
  occurrence?: number;
}

export interface FieldCandidate {
  label: string;
  value: string;
  section: string;
  source: string;
  element?: DomElement | null;
  proximity?: number;
}
