// Synthetic DOM fixture shaped like the observed Ant Design tab/panel relationship.
class Element {
  constructor(tag, attributes = {}, text = "") {
    this.tagName = tag.toUpperCase();
    this.attributes = new Map(Object.entries(attributes));
    this.ownText = text;
    this.children = [];
    this.parentElement = null;
    this.style = { display: "block", visibility: "visible" };
    this.hidden = false;
  }
  get textContent() {
    return [this.ownText, ...this.children.map((child) => child.textContent)]
      .filter(Boolean)
      .join(" ");
  }
  set textContent(value) {
    this.ownText = value;
  }
  get innerText() {
    return this.textContent;
  }
  set innerText(value) {
    this.ownText = value;
  }
  get childNodes() {
    return [
      ...(this.ownText ? [{ nodeType: 3, textContent: this.ownText }] : []),
      ...this.children,
    ];
  }
  get classList() {
    return { contains: (name) => (this.getAttribute("class") || "").split(/\s+/).includes(name) };
  }
  get id() {
    return this.getAttribute("id") || "";
  }
  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
  setAttribute(name, value) {
    this.attributes.set(name, value);
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this;
      node.ownerDocument = this.ownerDocument;
      this.children.push(node);
    }
  }
  contains(node) {
    return node === this || this.children.some((child) => child.contains(node));
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
  matches(selector) {
    return selector.split(",").some((part) => {
      part = part.trim();
      if (part === "*") return true;
      if (part === "[contenteditable]:not([contenteditable='false'])")
        return this.getAttribute("contenteditable") === "true";
      const tag = part.match(/^[a-z][a-z0-9-]*/i)?.[0];
      const classes = [...part.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
      const attrs = [...part.matchAll(/\[([^=\]]+)(?:=['"]([^'"]*)['"])?\]/g)];
      return (
        Boolean(tag || classes.length || attrs.length) &&
        (!tag || this.tagName === tag.toUpperCase()) &&
        classes.every((name) => this.classList.contains(name)) &&
        attrs.every(([, name, value]) =>
          value === undefined
            ? this.getAttribute(name) !== null
            : this.getAttribute(name) === value,
        )
      );
    });
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }
  querySelectorAll(selector) {
    return this.descendants().filter((node) =>
      selector.split(",").some((part) => {
        const parts = part.trim().split(/\s+/);
        return (
          node.matches(parts.at(-1)) &&
          (parts.length === 1 || node.parentElement?.closest(parts[0]))
        );
      }),
    );
  }
  descendants() {
    return this.children.flatMap((child) => [child, ...child.descendants()]);
  }
  getClientRects() {
    return this.hidden || this.style.display === "none" ? [] : [{}];
  }
}

export function reviewTabs(labels = ["初审", "复审"], selected = 0) {
  const observers = [];
  const document = {
    title: "Synthetic review tabs",
    images: [],
    defaultView: {
      getComputedStyle: (node) => node.style,
      MutationObserver: class {
        constructor(callback) {
          this.callback = callback;
          observers.push(this);
        }
        observe() {
          this.active = true;
        }
        disconnect() {
          this.active = false;
        }
      },
    },
    querySelectorAll(selector) {
      return this.body.querySelectorAll(selector);
    },
    getElementById(id) {
      return this.body.descendants().find((node) => node.id === id) ?? null;
    },
  };
  document.body = new Element("body");
  document.body.ownerDocument = document;
  document.body.innerText = "全部业务词可能同时出现：新车信息 审核过户凭证 行驶证信息";
  const add = (parent, tag, attrs = {}, text = "") => {
    const node = new Element(tag, attrs, text);
    parent.append(node);
    return node;
  };
  const nav = add(document.body, "div", { role: "tablist" });
  add(nav, "div", { role: "tab", "aria-selected": "true" }, "青岛一致性审核");
  const form = add(document.body, "form");
  const list = add(form, "div", { role: "tablist" });
  const tabs = labels.map((label, i) =>
    add(
      list,
      "div",
      {
        id: `synthetic-tab-${i}`,
        role: "tab",
        "aria-selected": String(i === selected),
        "aria-controls": `synthetic-panel-${i}`,
      },
      label,
    ),
  );
  const panels = labels.map((label, i) =>
    add(
      form,
      "div",
      {
        id: `synthetic-panel-${i}`,
        role: "tabpanel",
        "aria-labelledby": `synthetic-tab-${i}`,
        "aria-hidden": String(i !== selected),
      },
      `${label}表单`,
    ),
  );
  const select = (index) => {
    tabs.forEach((tab, i) => tab.setAttribute("aria-selected", String(i === index)));
    panels.forEach((panel, i) => panel.setAttribute("aria-hidden", String(i !== index)));
  };
  const flush = (records = []) =>
    observers
      .filter((observer) => observer.active)
      .forEach((observer) => observer.callback(records));
  return { document, add, form, list, tabs, panels, select, flush };
}
