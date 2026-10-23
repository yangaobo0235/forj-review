import assert from "node:assert/strict";
import { test } from "vitest";
import {
  guardActiveReviewTab,
  readActiveReviewTab,
  reviewTabCollectionRoot,
} from "../src/platform/browser/identity/active-review-tab.ts";
import { reviewTabs } from "./helpers/review-tabs.mjs";

const labels = ["初审", "复审", "一致性审核"];

test("empty MyForm tab panels collect sibling fields from their owning form", () => {
  const f = reviewTabs();
  const state = readActiveReviewTab(f.document, labels);
  assert.equal(reviewTabCollectionRoot(state), f.form);
  f.tabs[0].setAttribute("aria-controls", "missing");
  assert.equal(reviewTabCollectionRoot(readActiveReviewTab(f.document, labels)), null);
});

test("a panel with visible fields keeps collection inside that panel", () => {
  const f = reviewTabs();
  const input = f.add(f.panels[0], "input");
  f.panels[0].querySelectorAll = () => [input];
  assert.equal(reviewTabCollectionRoot(readActiveReviewTab(f.document, labels)), f.panels[0]);
});

test("selected review tabs exclude navigation and hidden forms, and resolve their visible panel", () => {
  const fixture = reviewTabs();
  const hiddenForm = fixture.add(fixture.document.body, "form", { "aria-hidden": "true" });
  const hiddenList = fixture.add(hiddenForm, "div", { role: "tablist" });
  fixture.add(hiddenList, "div", { role: "tab", "aria-selected": "true" }, "初审");
  const initial = readActiveReviewTab(fixture.document, labels);
  assert.equal(initial.label, "初审");
  assert.equal(initial.panelReady, true);
  assert.equal(initial.panel, fixture.panels[0]);
  fixture.select(1);
  assert.equal(readActiveReviewTab(fixture.document, labels).label, "复审");
  assert.equal(readActiveReviewTab(fixture.document, labels).panel, fixture.panels[1]);
  fixture.tabs[0].textContent = "一致性审核";
  fixture.tabs[1].textContent = "过户详情";
  assert.equal(
    readActiveReviewTab(fixture.document, labels).label,
    "过户详情",
    "same index can refer to a reference tab in another flow",
  );
  fixture.select(0);
  assert.equal(readActiveReviewTab(fixture.document, labels).label, "一致性审核");
});

test("a missing, hidden, unrelated or incorrectly linked panel is never ready", () => {
  for (const mutate of [
    (f) => f.tabs[0].setAttribute("aria-controls", "missing"),
    (f) => f.panels[0].setAttribute("aria-hidden", "true"),
    (f) => f.panels[0].setAttribute("aria-labelledby", "other-tab"),
    (f) => f.panels[0].setAttribute("role", "other"),
    (f) => {
      f.panels[0].style.display = "none";
    },
    (f) => {
      f.panels[0].parentElement = f.document.body;
      f.form.children = f.form.children.filter((node) => node !== f.panels[0]);
      f.document.body.children.push(f.panels[0]);
    },
  ]) {
    const f = reviewTabs();
    mutate(f);
    assert.equal(readActiveReviewTab(f.document, labels).panelReady, false);
  }
});

test("multiple selected tabs or competing visible review forms remain ambiguous", () => {
  const f = reviewTabs();
  f.tabs[1].setAttribute("aria-selected", "true");
  assert.equal(readActiveReviewTab(f.document, labels).tab, null);
  f.select(0);
  const other = f.add(f.document.body, "form");
  f.add(
    f.add(other, "div", { role: "tablist" }),
    "div",
    { role: "tab", "aria-selected": "true" },
    "一致性审核",
  );
  assert.equal(readActiveReviewTab(f.document, labels).tab, null);
  assert.equal(readActiveReviewTab(f.document, []).panelReady, false);
});

test("leaving a step invalidates its guard permanently, including a same-batch round trip", () => {
  for (const roundTrip of [false, true]) {
    const f = reviewTabs();
    let invalidations = 0;
    const guard = guardActiveReviewTab(
      f.document,
      labels,
      readActiveReviewTab(f.document, labels),
      () => invalidations++,
    );
    assert.equal(guard.isCurrent(), true);
    f.select(1);
    if (roundTrip) f.select(0);
    f.flush(
      roundTrip
        ? [
            {
              type: "attributes",
              target: f.tabs[0],
              attributeName: "aria-selected",
              oldValue: "false",
            },
          ]
        : [],
    );
    assert.equal(guard.isCurrent(), false);
    f.select(0);
    assert.equal(guard.isCurrent(), false);
    assert.equal(invalidations, 1);
    guard.disconnect();
  }
});

test("ordinary field changes preserve the guard; replacing or removing its panel invalidates it", () => {
  const f = reviewTabs();
  let invalidations = 0;
  const guard = guardActiveReviewTab(
    f.document,
    labels,
    readActiveReviewTab(f.document, labels),
    () => invalidations++,
  );
  f.flush([
    { type: "attributes", target: f.tabs[0], attributeName: "aria-selected", oldValue: "true" },
  ]);
  assert.equal(guard.isCurrent(), true, "redundant framework state assignments are harmless");
  f.flush([{ type: "childList", removedNodes: [], target: f.panels[0] }]);
  assert.equal(guard.isCurrent(), true);
  f.flush([{ type: "childList", removedNodes: [f.panels[0]], target: f.form }]);
  assert.equal(guard.isCurrent(), false);
  assert.equal(invalidations, 1);
});

test("replacing a panel with the same ID cannot reuse the old collection", () => {
  const f = reviewTabs();
  const snapshot = readActiveReviewTab(f.document, labels);
  const guard = guardActiveReviewTab(f.document, labels, snapshot, () => {});
  f.form.children = f.form.children.filter((node) => node !== snapshot.panel);
  f.add(f.form, "div", {
    id: snapshot.panel.id,
    role: "tabpanel",
    "aria-labelledby": snapshot.tab.id,
    "aria-hidden": "false",
  });
  assert.equal(readActiveReviewTab(f.document, labels).panelReady, true);
  assert.equal(guard.isCurrent(), false, "a valid new panel still needs its own collection");
  guard.disconnect();
});
