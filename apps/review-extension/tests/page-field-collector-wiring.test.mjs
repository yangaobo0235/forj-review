import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test, vi } from "vitest";
import { reviewTabs } from "./helpers/review-tabs.mjs";
import { domReviewTabs } from "./helpers/dom-review-tabs.mjs";

const manifest = JSON.parse(
  readFileSync(new URL("../public/manifest.json", import.meta.url), "utf8"),
);
const contentSource = readFileSync(
  new URL("../src/entrypoints/content.ts", import.meta.url),
  "utf8",
);

async function runContent(context) {
  vi.resetModules();
  const overrides = context.globalThis ?? {};
  const dependencies = {
    "../src/platform/browser/identity/business-detector.ts": "ReviewBusinessDetector",
    "../src/platform/browser/collection/business-scope.ts": "ReviewBusinessScope",
    "../src/platform/browser/collection/image-candidates.ts": "ReviewImageCandidates",
    "../src/platform/browser/actions/image-focus.ts": "ReviewImageFocus",
    "../src/platform/browser/collection/page-field-collector.ts": "ReviewPageFieldCollector",
    "../src/platform/browser/actions/page-field-writer.ts": "ReviewPageFieldWriter",
  };
  for (const [file, name] of Object.entries(dependencies)) {
    vi.doUnmock(file);
    if (overrides[name]) vi.doMock(file, () => ({ [name]: overrides[name] }));
  }
  if (overrides.ReviewPageFieldCollector && !overrides.ReviewPageFieldCollector.collectIdentity) {
    overrides.ReviewPageFieldCollector.collectIdentity = (root) =>
      overrides.ReviewPageFieldCollector.collect(root);
  }
  for (const [name, value] of Object.entries({
    Node: { TEXT_NODE: 3 },
    HTMLImageElement: class {},
    ...context,
    ...overrides,
  })) {
    if (name !== "globalThis" && name !== "console") vi.stubGlobal(name, value);
  }
  const register = context.chrome.runtime.onMessage.addListener;
  const testManifest = JSON.parse(
    readFileSync(new URL("./fixtures/collect-manifest.json", import.meta.url), "utf8"),
  );
  const testCatalog = JSON.parse(
    readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"),
  );
  context.chrome.runtime.onMessage.addListener = (handler) =>
    register((message, ...args) =>
      handler(
        message.type?.startsWith("COLLECT_PAGE")
          ? { collectManifests: [testManifest], pageCatalog: testCatalog, ...message }
          : message,
        ...args,
      ),
    );
  await import("../src/entrypoints/content.ts");
}

test("loads the page field collector before the content script", () => {
  const scripts = manifest.content_scripts[0].js;

  assert.deepEqual(scripts, ["content.js"]);
});

test("does not load the retired host-page review marker", () => {
  const scripts = manifest.content_scripts[0].js;
  assert.equal(scripts.includes("page-review-marker.js"), false);
  assert.deepEqual(scripts, ["content.js"]);
});

test("content script delegates page field collection without requiring a detected business", () => {
  assert.match(
    contentSource,
    /ReviewPageFieldCollector\.collect\(\s*activeReviewRoot \?\? document\s*\)/,
  );
  assert.doesNotMatch(contentSource, /business\.businessType/);
});

test("content script names its collection and focus message contract", () => {
  assert.match(contentSource, /const MESSAGE_TYPES = Object\.freeze/);
  assert.match(contentSource, /focusReviewImage/);
  assert.match(contentSource, /isCollectionMessage/);
  assert.match(contentSource, /collectPageData:\s*"COLLECT_PAGE_DATA"/);
  assert.match(contentSource, /focusReviewImage:\s*"FOCUS_REVIEW_IMAGE"/);
});

test("source review collects actual MyForm sibling fields through the full content message path", async () => {
  // Mirrors my-form/index.vue: empty tab panes, followed by one row of title/field columns.
  const f = domReviewTabs(["车源审核", "过户审核"]);
  f.form.setAttribute("class", "largeForm myForm");
  f.panels.forEach((panel) => {
    panel.textContent = "";
  });
  const row = f.add(f.form, "div", { class: "ant-row" });
  const title = (text) =>
    f.add(f.add(row, "div", { class: "ant-col" }), "div", { class: "formItemTitle" }, text);
  const inputs = new Map();
  const addField = (label, value, type = "text") => {
    const col = f.add(row, "div", { class: "ant-col" });
    const item = f.add(col, "div", { class: "ant-form-item" });
    const id = `source-field-${inputs.size}`;
    f.add(f.add(item, "div", { class: "ant-form-item-label" }), "label", { for: id }, label);
    const input = f.add(f.add(item, "div", { class: "ant-form-item-control" }), "input", {
      id,
      type,
    });
    input.value = value;
    inputs.set(label, input);
  };
  addField("车辆类型", "牵引车");
  title("行驶证信息");
  const license = {
    车牌号码: "鲁A12345",
    VIN: "LFV00000000000001",
    发动机号: "12345678",
    行驶证车辆类型: "重型半挂牵引车",
    品牌型号: "解放牌MODEL100A",
    使用性质: "营运",
    注册日期: "2023-03-22",
    发证日期: "2026-02-11",
    所有人: "合成示例公司",
    地址: "无需核对",
  };
  for (const [label, value] of Object.entries(license))
    addField(label, value, label.includes("日期") ? "date" : "text");
  title("规格参数");
  const specs = {
    品牌: "示例品牌",
    车系: "示例车系",
    车型: "示例完整销售车型",
    驱动形式: "6X4",
    燃料种类: "柴油",
    排放标准: "国六",
    最大马力: "420",
    发动机品牌: "示例发动机",
    发动机型号: "ENGINE42-E6",
    货箱长度: "",
  };
  for (const [label, value] of Object.entries(specs)) addField(label, value);
  // 规格中的三项身份控件要保留真实 option ID，不能用文本输入框冒充选择框。
  for (const [label, id, options] of [
    ["品牌", "2", [["2", specs["品牌"]]]],
    ["车系", "3", [["3", specs["车系"]]]],
    [
      "车型",
      "9",
      [
        ["9", specs["车型"]],
        ["10", "合成完整车型 420马力 6X4"],
      ],
    ],
  ]) {
    const previous = inputs.get(label);
    const select = f.document.createElement("select");
    select.id = previous.id;
    previous.replaceWith(select);
    for (const [value, text] of options) {
      const option = f.document.createElement("option");
      option.value = value;
      option.textContent = text;
      select.append(option);
    }
    select.value = id;
    inputs.set(label, select);
  }
  const old = f.add(f.panels[1], "input", { "aria-label": "车牌号码" });
  old.value = "OLD-PLATE";
  const filter = f.add(f.document.body, "input", { "aria-label": "车牌号码" });
  filter.value = "FILTER-PLATE";
  let handler;
  const modelInput = {
    announcementModel: "MODEL100A80",
    engineModel: "ENGINE42-E6",
    pageScope: {
      brandId: "2",
      brandName: "示例品牌",
      seriesId: "3",
      seriesName: "示例车系",
      typeLevel: "牵引车",
    },
  };
  const catalogModel = { modelId: "10", modelName: "合成完整车型 420马力 6X4" };
  let sessionResult = { ok: false, message: "车型查询已失效，请重新搜索并选择" };
  let catalogResult = catalogModel;
  const modelMessages = [];
  await runContent({
    document: f.document,
    URL,
    window: {
      location: { href: "https://admin.forjtruck.com/consistency-changchun?showPageModel=1" },
    },
    chrome: {
      runtime: {
        id: "extension",
        onMessage: {
          addListener(fn) {
            handler = fn;
          },
        },
        sendMessage: async (request) => {
          modelMessages.push(request);
          return request.type === "RESOLVE_REVIEW_MODEL_SEARCH"
            ? sessionResult
            : { ok: true, candidates: [catalogResult] };
        },
      },
    },
    console: { info() {} },
    globalThis: { crypto: { randomUUID: () => "source-form-test" } },
  });
  const message = (body) => new Promise((resolve) => handler(body, { id: "extension" }, resolve));
  const manifest = JSON.parse(
    readFileSync(
      new URL("./fixtures/collect-manifest-vehicle-source.json", import.meta.url),
      "utf8",
    ),
  );
  const catalog = JSON.parse(
    readFileSync(new URL("./fixtures/page-catalog.json", import.meta.url), "utf8"),
  );
  const result = await message({
    type: "COLLECT_PAGE_MANIFEST",
    pageCatalog: catalog,
    collectManifests: [manifest],
  });
  assert.equal(result.businessType, "vehicle_source");
  assert.deepEqual(result.pageSelection, {
    brand: { id: "2", label: "示例品牌" },
    series: { id: "3", label: "示例车系" },
    model: { id: "9", label: "示例完整销售车型" },
    vehicleType: "牵引车",
  });
  assert.equal(result.pageFields["vehicle.plate_no"], license["车牌号码"]);
  assert.equal(result.pageFields["vehicle.vin"], license.VIN);
  assert.equal(result.pageFields["vehicle.owner"], license["所有人"]);
  assert.equal(result.pageFields["vehicle.brand_model"], license["品牌型号"]);
  assert.equal(result.pageFields["vehicle.model"], specs["车型"]);
  assert.equal(result.pageFields["vehicle.type"], "牵引车");
  assert.equal(
    Object.keys(result.pageFields).length,
    19,
    "vehicle type, eight license and ten specification controls are collected",
  );
  assert.equal(
    result.reviewFields.filter((item) => item.field).length,
    9,
    "only eight license fields and model are reviewable",
  );
  assert.equal(
    result.reviewFields.find((item) => item.field === "vehicle.plate_no").section,
    "license",
  );
  assert.equal(result.reviewFields.find((item) => item.field === "vehicle.model").section, "spec");
  assert.equal(result.fieldTargets.length, 19);
  assert.equal(result.collectionDiagnostics.ambiguousFields.length, 0);
  assert.ok(result.pageFingerprint, "live VIN is available to the write guard");
  const target = {
    expectedPageUrl: result.pageUrl,
    expectedPageInstanceId: result.pageInstanceId,
    expectedCollectionId: result.collectionId,
    expectedPageFingerprint: result.pageFingerprint,
  };
  const queryScope = () => message({ ...target, type: "READ_REVIEW_MODEL_SCOPE" });
  assert.deepEqual((await queryScope()).pageScope, modelInput.pageScope);
  const filled = await message({
    ...target,
    type: "APPLY_PAGE_FIELD_VALUE",
    action: {
      field: "vehicle.brand_model",
      value: "解放牌MODEL100A80",
      expectedValue: license["品牌型号"],
    },
  });
  assert.equal(filled.ok, true, filled.message);
  assert.equal(inputs.get("品牌型号").value, "解放牌MODEL100A80");
  assert.equal(inputs.get("车牌号码").value, license["车牌号码"]);
  const applyModel = () =>
    message({
      ...target,
      type: "APPLY_VEHICLE_MODEL",
      searchId: "trusted-search",
      modelId: catalogModel.modelId,
      candidate: { ...catalogModel, modelName: "不得使用消息内伪造的配置" },
    });
  const rejected = await applyModel();
  assert.equal(rejected.ok, false);
  assert.match(rejected.message, /查询已失效/);
  assert.equal(inputs.get("车型").value, "9");
  sessionResult = { ok: true, candidate: catalogModel, input: modelInput };
  catalogResult = { ...catalogModel, modelName: "目录已更名" };
  const changed = await applyModel();
  assert.equal(changed.ok, false);
  assert.match(changed.message, /候选车型已更新/);
  assert.equal(inputs.get("车型").value, "9");
  catalogResult = catalogModel;
  inputs.get("车辆类型").value = "载货车";
  assert.equal((await queryScope()).code, "MODEL_PAGE_SCOPE_CHANGED");
  const scopeChanged = await applyModel();
  assert.equal(scopeChanged.ok, false);
  // 页面业务范围改变后，旧的车型选择不能继续回填。
  assert.equal(inputs.get("车型").value, "9");
  assert.equal(
    modelMessages.filter((item) => item.type === "QUERY_VEHICLE_MODEL_CATALOG").length,
    1,
  );
  inputs.get("车辆类型").value = "牵引车";
  // Chrome storage 属性顺序不影响候选身份；规格手动改动也不阻止只选车型。
  sessionResult.candidate = Object.fromEntries(Object.entries(catalogModel).reverse());
  inputs.get("最大马力").value = "500";
  const modelFilled = await applyModel();
  assert.equal(modelFilled.ok, true, modelFilled.message);
  assert.equal(inputs.get("车型").value, "10");
  assert.equal(modelFilled.actions.length, 1);
  assert.equal(inputs.get("最大马力").value, "500");
  assert.ok(modelFilled.actions.every((item) => item.field !== "spec.brand"));
  assert.equal(inputs.get("品牌").value, "2");
  assert.equal(inputs.get("车系").value, "3");
  assert.equal(modelMessages[0].type, "RESOLVE_REVIEW_MODEL_SEARCH");
  assert.equal(
    modelMessages.filter((item) => item.type === "QUERY_VEHICLE_MODEL_CATALOG").length,
    2,
  );
  // Neither a forged query scope nor a manual same-label series edit can change
  // fixed parents through the content entry, even after a verified model write.
  assert.equal(
    (await applyModel()).ok,
    true,
    "verified baseline permits repeated controlled writes",
  );
  f.add(inputs.get("车系"), "option", { value: "4" }, "示例车系");
  f.add(inputs.get("车型"), "option", { value: "11" }, catalogModel.modelName);
  catalogModel.modelId = "11";
  sessionResult = {
    ok: true,
    candidate: structuredClone(catalogModel),
    input: { ...modelInput, pageScope: { ...modelInput.pageScope, seriesId: "4" } },
  };
  const switched = await applyModel();
  assert.equal(switched.code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(inputs.get("车系").value, "3");
  assert.equal(inputs.get("车型").value, "10");
  assert.equal(inputs.get("品牌").value, "2");
  sessionResult.input = modelInput;
  inputs.get("车系").value = "4";
  assert.equal((await queryScope()).code, "MODEL_PAGE_SCOPE_CHANGED");
  assert.equal(
    (await applyModel()).code,
    "MODEL_PAGE_SCOPE_CHANGED",
    "manual same-label ID change remains blocked",
  );
  inputs.get("车系").value = "3";
  f.select(1);
  const messagesBefore = modelMessages.length;
  const staleModel = await applyModel();
  assert.equal(staleModel.ok, false);
  assert.equal(staleModel.code, "PAGE_IDENTITY_CHANGED");
  assert.equal(
    modelMessages.length,
    messagesBefore,
    "changed steps are rejected before resolving a model session",
  );
  const stale = await message({
    ...target,
    type: "APPLY_PAGE_FIELD_VALUE",
    action: {
      field: "vehicle.brand_model",
      value: "SHOULD-NOT-WRITE",
      expectedValue: "解放牌MODEL100A80",
    },
  });
  assert.equal(
    stale.ok,
    false,
    "step changes still block old source writes with form-level collection",
  );
  assert.equal(inputs.get("品牌型号").value, "解放牌MODEL100A80");
}, 60000);

test("same-URL tab changes expire pending collections, fills, image focus and invoice verification", async () => {
  for (const mode of ["observed", "live", "pending"]) {
    const observed = mode !== "live";
    const fixture = reviewTabs();
    if (!observed) fixture.document.defaultView.MutationObserver = undefined;
    let handler,
      collectedRoot,
      writes = 0,
      identityReads = 0;
    const element = { value: "VIN-A", isConnected: true };
    const fields = { "vehicle.vin": "VIN-A", "vehicle.plate_no": "PLATE-A" };
    const catalog = JSON.parse(
      readFileSync(new URL("./fixtures/page-catalog-synthetic.json", import.meta.url), "utf8"),
    );
    const manifest = {
      business_type: "permit_initial",
      region: "default",
      scopes: [],
      fields: [],
      materials: [],
      page_groups: [],
      identity_anchors: ["vehicle.vin", "vehicle.plate_no"],
      record_identity_fields: ["vehicle.vin"],
      writable_fields: ["vehicle.vin", "vehicle.plate_no"],
    };
    const message = (body) => new Promise((resolve) => handler(body, null, resolve));
    await runContent({
      URL,
      document: fixture.document,
      window: {
        location: { href: "https://admin.forjtruck.com/sample-review-qingdao?showPageModel=1" },
      },
      chrome: {
        runtime: {
          onMessage: {
            addListener(fn) {
              handler = fn;
            },
          },
        },
      },
      console: { info() {} },
      globalThis: {
        crypto: { randomUUID: () => "synthetic-instance" },
        ReviewPageFieldCollector: {
          collect: (root) => {
            collectedRoot = root;
            return {
              pageFields: fields,
              fieldTargets: [{ field: "vehicle.vin", element }],
              writableTargets: [],
              unmatchedLabels: [],
              scannedControls: 1,
              candidateCount: 1,
              ambiguousFields: [],
            };
          },
          collectIdentity: (root) => {
            identityReads++;
            assert.equal(root, fixture.form);
            return { pageFields: fields, fieldTargets: [] };
          },
        },
        ReviewPageFieldWriter: {
          captureValue: () => ({ control: element, value: element.value }),
          executeValue: async () => {
            writes++;
            return { ok: true };
          },
          executeValueGroup: async () => {
            writes++;
            return { ok: true };
          },
          triggerInvoiceVerification: () => {
            writes++;
            return { ok: true };
          },
        },
        ReviewBusinessScope: {
          documentTypeFor: (_scope, _order, category) => category || "unknown",
          scopeForLabel: () => null,
          assign: () => [],
        },
        ReviewImageCandidates: {
          select: () => ({ selected: [], scannedCount: 0, overflow: false }),
        },
      },
    });
    const collecting = message({
      type: "COLLECT_PAGE_MANIFEST",
      pageCatalog: catalog,
      collectManifests: [manifest],
    });
    if (mode === "pending") {
      fixture.select(1);
      fixture.flush([]);
      fixture.select(0);
    }
    const collected = await collecting;
    if (mode === "pending")
      assert.equal(
        collected.staleCollection,
        true,
        "a late response cannot restore an expired step",
      );
    assert.equal(collected.businessType, "permit_initial", collected.businessDetectionError);
    assert.equal(
      collectedRoot === fixture.form,
      true,
      "empty tab panels collect from their owning review form",
    );
    assert.ok(collected.pageFingerprint);
    const target = {
      expectedPageUrl: collected.pageUrl,
      expectedPageInstanceId: collected.pageInstanceId,
      expectedCollectionId: collected.collectionId,
      expectedPageFingerprint: collected.pageFingerprint,
    };
    fixture.select(1);
    if (observed) {
      fixture.flush([]);
      fixture.select(0);
    }
    for (const request of [
      {
        type: "APPLY_PAGE_FIELD_VALUE",
        action: { field: "vehicle.vin", value: "NEW", expectedValue: "VIN-A" },
      },
      {
        type: "APPLY_PAGE_FIELD_GROUP_VALUE",
        action: { fields: ["vehicle.vin", "vehicle.plate_no"], value: "NEW" },
      },
      { type: "VERIFY_INVOICE", action: { field: "vehicle.vin", expectedValue: "VIN-A" } },
      { type: "FOCUS_REVIEW_IMAGE", imageId: "synthetic-image" },
    ])
      assert.equal((await message({ ...target, ...request })).ok, false);
    assert.equal(writes, 0);
    assert.equal(identityReads, 0, "a changed step is rejected before reading fields or writing");
  }
});

test("content script reports candidate and ambiguity diagnostics", () => {
  assert.match(contentSource, /candidateCount: fieldCollection\.candidateCount/);
  assert.match(contentSource, /ambiguousFields: fieldCollection\.ambiguousFields/);
});

test("content script sends writable target snapshots", () => {
  assert.match(contentSource, /writableTargets/);
  assert.match(contentSource, /pageInstanceId/);
  assert.match(contentSource, /expectedPageInstanceId/);
  assert.match(contentSource, /APPLY_PAGE_FIELD_VALUE/);
});

test("content script keeps field elements in a map replaced only by the active collection", () => {
  assert.match(contentSource, /const reviewFieldElements = new Map\(\);/);
  assert.match(contentSource, /reviewFieldElements\.set\(field,\s*\{\s*element,\s*collectionId/);
  const expiredGuard = contentSource.indexOf("latestCollectionId !== collectionId");
  const fieldMapReplace = contentSource.indexOf("reviewFieldElements.clear()");
  assert.ok(expiredGuard >= 0);
  assert.ok(fieldMapReplace > expiredGuard);
});

test("content script sends field target snapshots without DOM elements", async () => {
  let handler;
  const element = { isConnected: true };
  const fields = { "application.id": "case-a", "old_vehicle.vin": "OLD-A" };
  const reviewFields = [
    {
      field: "old_vehicle.vin",
      label: "报废车辆车架号",
      value: "OLD-A",
      controlType: "text",
      editable: true,
      order: 1,
      section: "old_vehicle",
    },
  ];
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
        sendMessage: async () => ({ ok: true }),
      },
    },
    document: {
      title: "审核页",
      images: [],
      body: {
        innerText: "",
        querySelectorAll() {
          return [];
        },
      },
    },
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
      getComputedStyle() {
        return { display: "block", visibility: "visible" };
      },
    },
    console: { info() {} },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewBusinessDetector: {
        resolve() {
          return { business: { businessType: "scrap_replacement" } };
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return {
            pageFields: fields,
            reviewFields,
            fieldTargets: [{ field: "old_vehicle.vin", element }],
            writableTargets: [],
            unmatchedLabels: [],
            scannedControls: 1,
            candidateCount: 0,
            ambiguousFields: [],
          };
        },
      },
      ReviewBusinessScope: {
        documentTypeFor: (_scope, _order, category) => category || "unknown",
        scopeForLabel() {
          return null;
        },
        assign() {
          return [];
        },
      },
      ReviewImageCandidates: {
        select() {
          return { selected: [], scannedCount: 0, overflow: false };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];

  handler({ type: "COLLECT_PAGE_DATA" }, null, (response) => responses.push(response));
  await new Promise((resolve) => setTimeout(resolve, 0));

  // The response crosses a vm realm boundary, so compare JSON instead of prototypes.
  assert.equal(
    JSON.stringify(responses[0]?.fieldTargets),
    JSON.stringify([{ field: "old_vehicle.vin", present: true }]),
  );
  assert.equal(Object.hasOwn(responses[0].fieldTargets[0], "element"), false);
  assert.equal(JSON.stringify(responses[0].reviewFields), JSON.stringify(reviewFields));
  assert.equal(responses[0].collectionDiagnostics.reviewFieldCount, 1);
});

test("content script rejects a mismatched page identity before calling the writer", async () => {
  let handler;
  let writes = 0;
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
      },
    },
    document: {},
    window: { location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?case=next" } },
    globalThis: {
      ReviewPageFieldWriter: {
        executeValue() {
          writes += 1;
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return { pageFields: {} };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];

  const asynchronous = handler(
    {
      type: "APPLY_PAGE_FIELD_VALUE",
      action: { field: "old_vehicle.vin", value: "NEW-VIN", expectedValue: "OLD-A" },
      expectedPageUrl: "https://admin.forjtruck.com/scrap-replace-qingdao?case=old",
      expectedPageInstanceId: "old-instance",
    },
    null,
    (response) => responses.push(response),
  );

  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(asynchronous, true);
  assert.equal(writes, 0);
  assert.equal(responses[0]?.ok, false);
  assert.match(responses[0]?.message || "", /页面已变化|重新审核/);
});

test("content script rejects a same-URL SPA record change by page fingerprint", async () => {
  let handler;
  let writes = 0;
  const currentFields = { "application.id": "case-b", "old_vehicle.vin": "OLD-B" };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
      },
    },
    document: {},
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
    },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewPageFieldWriter: {
        executeValue() {
          writes += 1;
          return Promise.resolve({ ok: true });
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return { pageFields: currentFields };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];

  handler(
    {
      type: "APPLY_PAGE_FIELD_VALUE",
      action: { field: "old_vehicle.vin", value: "NEW-VIN", expectedValue: "OLD-A" },
      expectedPageUrl: context.window.location.href,
      expectedPageInstanceId: "page-instance",
      expectedPageFingerprint: '[["application.id","case-a"],["old_vehicle.vin","OLD-A"]]',
    },
    null,
    (response) => responses.push(response),
  );

  assert.equal(writes, 0);
  assert.equal(responses[0]?.ok, false);
  assert.match(responses[0]?.message || "", /页面已变化|重新审核/);
});

test("content script rejects an owner-only page fingerprint before writer execution", async () => {
  let handler;
  let writes = 0;
  const fields = { "old_vehicle.owner": "张三" };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
      },
    },
    document: {},
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
    },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewPageFieldWriter: {
        executeValue() {
          writes += 1;
          return Promise.resolve({ ok: true });
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return { pageFields: fields };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];

  handler(
    {
      type: "APPLY_PAGE_FIELD_VALUE",
      action: { field: "old_vehicle.vin", value: "NEW-VIN", expectedValue: "OLD-A" },
      expectedPageUrl: context.window.location.href,
      expectedPageInstanceId: "page-instance",
      expectedPageFingerprint: '[["old_vehicle.owner","张三"]]',
    },
    null,
    (response) => responses.push(response),
  );

  assert.equal(writes, 0);
  assert.equal(responses[0]?.ok, false);
  assert.match(responses[0]?.message || "", /页面已变化|重新审核/);
});

test("content script rejects image focus when the same URL now represents another collected record", async () => {
  let handler;
  let focusCalls = 0;
  const fields = { "application.id": "case-b", "old_vehicle.vin": "OLD-B" };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
      },
    },
    document: {},
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
    },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewPageFieldCollector: {
        collect() {
          return { pageFields: fields };
        },
      },
      ReviewImageFocus: {
        focus() {
          focusCalls += 1;
          return { ok: true };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];

  handler(
    {
      type: "FOCUS_REVIEW_IMAGE",
      imageId: "unknown-01",
      expectedPageUrl: context.window.location.href,
      expectedPageInstanceId: "page-instance",
      expectedPageFingerprint: '[["application.id","case-a"],["old_vehicle.vin","OLD-A"]]',
    },
    null,
    (response) => responses.push(response),
  );

  assert.equal(focusCalls, 0);
  assert.equal(responses[0]?.ok, false);
  assert.match(responses[0]?.error || "", /页面已变化|重新采集/);
});

test("content script rejects image focus without the active collection token", async () => {
  let handler;
  let focusCalls = 0;
  const fields = { "application.id": "case-a", "old_vehicle.vin": "OLD-A" };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
      },
    },
    document: {},
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
    },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewPageFieldCollector: {
        collect() {
          return { pageFields: fields };
        },
      },
      ReviewImageFocus: {
        focus() {
          focusCalls += 1;
          return { ok: true };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];

  handler(
    {
      type: "FOCUS_REVIEW_IMAGE",
      imageId: "unknown-01",
      expectedPageUrl: context.window.location.href,
      expectedPageInstanceId: "page-instance",
      expectedPageFingerprint: '[["application.id","case-a"],["old_vehicle.vin","OLD-A"]]',
      expectedCollectionId: "collection-a",
    },
    null,
    (response) => responses.push(response),
  );

  assert.equal(focusCalls, 0);
  assert.equal(responses[0]?.ok, false);
  assert.match(responses[0]?.error || "", /页面已变化|重新采集/);
});

test("content keeps image focus bound to the latest completed collection token", async () => {
  let handler;
  let focusCalls = 0;
  const fields = { "application.id": "case-a", "old_vehicle.vin": "OLD-A" };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
        sendMessage: async () => ({ ok: true }),
      },
    },
    document: {
      title: "审核页",
      images: [],
      body: {
        innerText: "",
        querySelectorAll() {
          return [];
        },
      },
    },
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
      getComputedStyle() {
        return { display: "block", visibility: "visible" };
      },
    },
    console: { info() {} },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewBusinessDetector: {
        resolve() {
          return { business: { businessType: "scrap_replacement" } };
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return {
            pageFields: fields,
            fieldTargets: [],
            writableTargets: [],
            unmatchedLabels: [],
            scannedControls: 0,
            candidateCount: 0,
            ambiguousFields: [],
          };
        },
      },
      ReviewBusinessScope: {
        documentTypeFor: (_scope, _order, category) => category || "unknown",
        scopeForLabel() {
          return null;
        },
        assign() {
          return [];
        },
      },
      ReviewImageCandidates: {
        select() {
          return { selected: [], scannedCount: 0, overflow: false };
        },
      },
      ReviewImageFocus: {
        focus() {
          focusCalls += 1;
          return { ok: true };
        },
      },
    },
  };
  await runContent(context);
  const responses = [];
  const collect = () =>
    handler({ type: "COLLECT_PAGE_DATA" }, null, (response) => responses.push(response));

  collect();
  collect();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const expired = responses.find((response) => response.collectionIssues?.length);
  const active = responses.find((response) => !response.collectionIssues?.length);
  assert.ok(expired?.collectionId);
  assert.ok(active?.collectionId);
  assert.notEqual(expired.collectionId, active.collectionId);
  // 被取代的采集必须带回真实原因和已识别的业务，面板不得退化为“无法识别业务”。
  assert.equal(expired.staleCollection, true);
  assert.equal(expired.collectionIssues.length, 1);
  assert.equal(expired.collectionIssues[0], "页面采集已过期，请重新采集");
  assert.equal(expired.businessType, "scrap_replacement");
  assert.equal(active.staleCollection, undefined);

  const identity = {
    type: "FOCUS_REVIEW_IMAGE",
    imageId: "unknown-01",
    expectedPageUrl: context.window.location.href,
    expectedPageInstanceId: "page-instance",
    expectedPageFingerprint: '[["application.id","case-a"],["old_vehicle.vin","OLD-A"]]',
  };
  const oldResponses = [];
  handler({ ...identity, expectedCollectionId: expired.collectionId }, null, (response) =>
    oldResponses.push(response),
  );
  assert.equal(focusCalls, 0);
  assert.equal(oldResponses[0]?.ok, false);

  const activeResponses = [];
  handler({ ...identity, expectedCollectionId: active.collectionId }, null, (response) =>
    activeResponses.push(response),
  );
  assert.equal(focusCalls, 0);
  assert.equal(activeResponses[0]?.ok, false);
});

test("content rejects a mapped image when its source changes after collection", async () => {
  let handler;
  let clicks = 0;
  const fields = { "application.id": "case-a", "old_vehicle.vin": "OLD-A" };
  const image = {
    isConnected: true,
    src: "https://images.test/original",
    currentSrc: "https://images.test/original",
    alt: "原始资料",
    className: "",
    naturalWidth: 100,
    naturalHeight: 80,
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 100, height: 80 };
    },
    closest() {
      return null;
    },
    getAttribute() {
      return null;
    },
    click() {
      clicks += 1;
    },
  };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
        sendMessage: async () => {
          throw new Error("Image bytes must never be requested");
        },
      },
    },
    document: {
      title: "审核页",
      images: [image],
      body: {
        innerText: "",
        querySelectorAll() {
          return [];
        },
      },
    },
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
      getComputedStyle() {
        return { display: "block", visibility: "visible" };
      },
    },
    console: { info() {} },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewBusinessDetector: {
        resolve() {
          return { business: { businessType: "scrap_replacement" } };
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return {
            pageFields: fields,
            fieldTargets: [],
            writableTargets: [],
            unmatchedLabels: [],
            scannedControls: 0,
            candidateCount: 0,
            ambiguousFields: [],
          };
        },
      },
      ReviewBusinessScope: {
        documentTypeFor: (_scope, _order, category) => category || "unknown",
        scopeForLabel() {
          return null;
        },
        assign() {
          return [];
        },
      },
      ReviewImageCandidates: {
        select(candidates) {
          return { selected: candidates, scannedCount: candidates.length, overflow: false };
        },
      },
      ReviewImageFocus: {
        focus(node) {
          node.click();
          return { ok: true };
        },
      },
    },
  };
  await runContent(context);
  const collected = [];
  handler({ type: "COLLECT_PAGE_DATA" }, null, (response) => collected.push(response));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const identity = {
    type: "FOCUS_REVIEW_IMAGE",
    imageId: "unknown-01",
    expectedPageUrl: context.window.location.href,
    expectedPageInstanceId: "page-instance",
    expectedPageFingerprint: '[["application.id","case-a"],["old_vehicle.vin","OLD-A"]]',
    expectedCollectionId: collected[0].collectionId,
  };

  const unchanged = [];
  handler(identity, null, (response) => unchanged.push(response));
  assert.equal(unchanged[0]?.ok, true);
  assert.equal(clicks, 1);

  image.currentSrc = "https://images.test/replaced";
  const changed = [];
  handler(identity, null, (response) => changed.push(response));
  assert.equal(changed[0]?.ok, false);
  assert.equal(clicks, 1);
  assert.match(changed[0]?.error || "", /原图已变化|重新采集/);
});

test("framework re-render 换掉 <img> 节点后按图片地址重新定位", async () => {
  let handler;
  let clicks = 0;
  const fields = { "application.id": "case-a", "old_vehicle.vin": "OLD-A" };
  const collected = {
    isConnected: true,
    src: "https://images.test/original",
    currentSrc: "https://images.test/original",
    alt: "原始资料",
    className: "",
    naturalWidth: 100,
    naturalHeight: 80,
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 100, height: 80 };
    },
    closest() {
      return null;
    },
    getAttribute() {
      return null;
    },
    click() {
      clicks += 1;
    },
  };
  // 上传组件重新挂载缩略图：节点换了，地址没变。
  const rerendered = {
    ...collected,
    click() {
      clicks += 1;
    },
  };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            handler = listener;
          },
        },
        sendMessage: async () => {
          throw new Error("Image bytes must never be requested");
        },
      },
    },
    document: {
      title: "审核页",
      images: [collected],
      body: {
        innerText: "",
        querySelectorAll() {
          return [];
        },
      },
    },
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
      getComputedStyle() {
        return { display: "block", visibility: "visible" };
      },
    },
    console: { info() {} },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewBusinessDetector: {
        resolve() {
          return { business: { businessType: "scrap_replacement" } };
        },
      },
      ReviewPageFieldCollector: {
        collect() {
          return {
            pageFields: fields,
            fieldTargets: [],
            writableTargets: [],
            unmatchedLabels: [],
            scannedControls: 0,
            candidateCount: 0,
            ambiguousFields: [],
          };
        },
      },
      ReviewBusinessScope: {
        documentTypeFor: (_scope, _order, category) => category || "unknown",
        scopeForLabel() {
          return null;
        },
        assign() {
          return [];
        },
      },
      ReviewImageCandidates: {
        select(candidates) {
          return { selected: candidates, scannedCount: candidates.length, overflow: false };
        },
      },
      ReviewImageFocus: {
        focus(node) {
          node.click();
          return { ok: true };
        },
      },
    },
  };
  await runContent(context);
  const collectedResponses = [];
  handler({ type: "COLLECT_PAGE_DATA" }, null, (response) => collectedResponses.push(response));
  await new Promise((resolve) => setTimeout(resolve, 0));

  collected.isConnected = false;
  context.document.images = [rerendered];

  const responses = [];
  handler(
    {
      type: "FOCUS_REVIEW_IMAGE",
      imageId: "unknown-01",
      expectedPageUrl: context.window.location.href,
      expectedPageInstanceId: "page-instance",
      expectedPageFingerprint: '[["application.id","case-a"],["old_vehicle.vin","OLD-A"]]',
      expectedCollectionId: collectedResponses[0].collectionId,
    },
    null,
    (response) => responses.push(response),
  );

  assert.equal(responses[0]?.ok, true);
  assert.equal(clicks, 1);
});

async function repeatedVinFillFixture(initialVin = "LFWSRXDN0T1F1") {
  let handler,
    focusCalls = 0;
  const controls = Object.fromEntries(
    ["new_vehicle.vin", "page_ocr.new_vehicle_vin"].map((field) => [
      field,
      { tagName: "INPUT", value: initialVin, isConnected: true },
    ]),
  );
  const fields = { "application.id": "CASE-A" };
  const pageFields = () => ({
    ...fields,
    ...Object.fromEntries(Object.entries(controls).map(([field, node]) => [field, node.value])),
  });
  const image = {
    isConnected: true,
    src: "https://images.test/vin-proof",
    currentSrc: "https://images.test/vin-proof",
    alt: "新车登记证书",
    className: "",
    naturalWidth: 100,
    naturalHeight: 80,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 80 }),
    closest: () => null,
    getAttribute: () => null,
  };
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(fn) {
            handler = fn;
          },
        },
      },
    },
    document: {
      title: "审核页",
      images: [image],
      body: { innerText: "", querySelectorAll: () => [] },
    },
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
      getComputedStyle: () => ({ display: "block", visibility: "visible" }),
    },
    console: { info() {} },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewBusinessDetector: {
        resolve: () => ({ business: { businessType: "scrap_replacement" } }),
      },
      ReviewPageFieldCollector: {
        collect: () => ({
          pageFields: pageFields(),
          fieldTargets: Object.entries(controls).map(([field, element]) => ({ field, element })),
          writableTargets: [],
          unmatchedLabels: [],
          scannedControls: 2,
          candidateCount: 2,
          ambiguousFields: [],
        }),
        collectIdentity: () => ({
          pageFields: pageFields(),
          fieldTargets: [{ field: "new_vehicle.vin", element: controls["new_vehicle.vin"] }],
        }),
      },
      ReviewPageFieldWriter: {
        defaultWritableFields: new Set(Object.keys(controls)),
        captureValue: (node) => ({ control: node, value: node.value }),
        executeValue: async (_root, node, action, guard) => {
          assert.equal(node.value, action.expectedValue);
          assert.equal(guard(), true);
          node.value = action.value;
          assert.equal(guard(), true);
          return { ok: true, actions: [{ field: action.field, value: node.value }] };
        },
        executeValueGroup: async (_root, entries, action, guard) => {
          for (const entry of entries) {
            assert.equal(entry.element.value, entry.expectedValue);
            assert.equal(guard(), true);
            entry.element.value = action.value;
          }
          assert.equal(guard(), true);
          return {
            ok: true,
            actions: entries.map((entry) => ({ field: entry.field, value: entry.element.value })),
          };
        },
      },
      ReviewBusinessScope: {
        documentTypeFor: (_scope, _order, category) => category || "unknown",
        scopeForLabel: () => null,
        assign: () => [],
      },
      ReviewImageCandidates: {
        select: (candidates) => ({
          selected: candidates,
          scannedCount: candidates.length,
          overflow: false,
        }),
      },
      ReviewImageFocus: {
        focus: () => {
          focusCalls++;
          return { ok: true };
        },
      },
    },
  };
  await runContent(context);
  const send = (message) => new Promise((resolve) => handler(message, null, resolve));
  const collect = () => send({ type: "COLLECT_PAGE_DATA" });
  const collected = await collect();
  const identity = (snapshot) => ({
    expectedPageUrl: snapshot.pageUrl,
    expectedPageInstanceId: snapshot.pageInstanceId,
    expectedCollectionId: snapshot.collectionId,
    expectedPageFingerprint: snapshot.pageFingerprint,
  });
  return {
    controls,
    fields,
    collect,
    get focusCalls() {
      return focusCalls;
    },
    fill: (value, kind = "group", snapshot = collected) =>
      send({
        ...identity(snapshot),
        type: kind === "group" ? "APPLY_PAGE_FIELD_GROUP_VALUE" : "APPLY_PAGE_FIELD_VALUE",
        action:
          kind === "group"
            ? {
                fields: Object.keys(controls),
                value,
                expectedValues: Object.fromEntries(
                  Object.entries(controls).map(([field, node]) => [field, node.value]),
                ),
              }
            : { field: "new_vehicle.vin", value, expectedValue: controls["new_vehicle.vin"].value },
      }),
    focus: (snapshot = collected) =>
      send({
        ...identity(snapshot),
        type: "FOCUS_REVIEW_IMAGE",
        imageId: snapshot.images[0].imageId,
      }),
  };
}

test("repeated VIN group fills keep image focus bound to the original collection", async () => {
  for (const values of [
    ["VIN-A", "VIN-A"],
    ["VIN-A", "VIN-B", "LFWSRXDN0T1F1"],
  ]) {
    const fixture = await repeatedVinFillFixture();
    for (const value of values) {
      assert.equal((await fixture.fill(value)).ok, true);
      assert.equal(
        (await fixture.focus()).ok,
        true,
        "successful writes must preserve the collection identity",
      );
    }
    assert.equal(fixture.focusCalls, values.length);
  }
});

test("mixed single and group VIN fills and an initially empty VIN preserve the first baseline", async () => {
  for (const initial of ["LFWSRXDN0T1F1", ""]) {
    const fixture = await repeatedVinFillFixture(initial);
    for (const [kind, value] of [
      ["group", "VIN-A"],
      ["single", "VIN-B"],
      ["group", "VIN-C"],
    ]) {
      assert.equal((await fixture.fill(value, kind)).ok, true);
      assert.equal((await fixture.focus()).ok, true);
    }
  }
});

test("repeated fills still reject image focus after an unverified VIN or record change", async () => {
  for (const changedField of ["new_vehicle.vin", "application.id"]) {
    const fixture = await repeatedVinFillFixture();
    assert.equal((await fixture.fill("VIN-A")).ok, true);
    assert.equal((await fixture.fill("VIN-B")).ok, true);
    if (changedField === "new_vehicle.vin")
      fixture.controls[changedField].value = "MANUALLY-CHANGED";
    else fixture.fields[changedField] = "CASE-B";
    assert.equal((await fixture.focus()).ok, false);
    assert.equal(fixture.focusCalls, 0);
    if (changedField === "application.id")
      assert.equal((await fixture.fill("VIN-C")).code, "PAGE_IDENTITY_CHANGED");
  }
});

test("a fresh collection replaces the VIN baseline and expires old image focus", async () => {
  const fixture = await repeatedVinFillFixture();
  assert.equal((await fixture.fill("VIN-A")).ok, true);
  const fresh = await fixture.collect();
  assert.equal((await fixture.focus()).ok, false);
  for (const value of ["VIN-B", "VIN-C"]) {
    assert.equal((await fixture.fill(value, "group", fresh)).ok, true);
    assert.equal((await fixture.focus(fresh)).ok, true);
  }
});

test("changed write targets require a surviving independent strong identity anchor", async () => {
  for (const [initial, current, expectedOk] of [
    [
      { "application.id": "CASE-A", "old_vehicle.vin": "OLD" },
      { "old_vehicle.vin": "CHANGED" },
      false,
    ],
    [{ "old_vehicle.vin": "OLD" }, { "old_vehicle.vin": "CHANGED" }, false],
    [
      { "old_vehicle.vin": "OLD", "old_vehicle.owner": "OWNER" },
      { "old_vehicle.vin": "CHANGED", "old_vehicle.owner": "OWNER" },
      false,
    ],
    [
      { "application.id": "CASE-A", "old_vehicle.vin": "OLD" },
      { "application.id": "CASE-A", "old_vehicle.vin": "CHANGED" },
      true,
    ],
  ]) {
    let handler,
      writes = 0;
    let fields = initial;
    const element = { tagName: "INPUT", value: "OLD", isConnected: true };
    const context = {
      chrome: {
        runtime: {
          onMessage: {
            addListener(fn) {
              handler = fn;
            },
          },
        },
      },
      document: {
        title: "审核页",
        images: [],
        body: { innerText: "", querySelectorAll: () => [] },
      },
      window: {
        location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
      },
      console: { info() {} },
      globalThis: {
        crypto: { randomUUID: () => "page-instance" },
        ReviewBusinessDetector: {
          resolve: () => ({ business: { businessType: "scrap_replacement" } }),
        },
        ReviewPageFieldCollector: {
          collect: () => ({
            pageFields: fields,
            fieldTargets: [{ field: "old_vehicle.vin", element }],
            writableTargets: [],
            unmatchedLabels: [],
            scannedControls: 1,
            candidateCount: 1,
            ambiguousFields: [],
          }),
        },
        ReviewPageFieldWriter: {
          defaultWritableFields: new Set(["old_vehicle.vin"]),
          captureValue: () => ({ control: element, value: element.value }),
          executeValue: async () => {
            writes++;
            return { ok: true };
          },
        },
        ReviewBusinessScope: {
          documentTypeFor: (_scope, _order, category) => category || "unknown",
          scopeForLabel: () => null,
          assign: () => [],
        },
        ReviewImageCandidates: {
          select: () => ({ selected: [], scannedCount: 0, overflow: false }),
        },
      },
    };
    await runContent(context);
    const collected = await new Promise((resolve) =>
      handler({ type: "COLLECT_PAGE_DATA" }, null, resolve),
    );
    fields = current;
    element.value = String(current["old_vehicle.vin"] ?? "");
    const result = await new Promise((resolve) =>
      handler(
        {
          type: "APPLY_PAGE_FIELD_VALUE",
          action: { field: "old_vehicle.vin", value: "NEW", expectedValue: "OLD" },
          expectedPageUrl: collected.pageUrl,
          expectedPageInstanceId: collected.pageInstanceId,
          expectedCollectionId: collected.collectionId,
          expectedPageFingerprint: collected.pageFingerprint,
        },
        null,
        resolve,
      ),
    );
    assert.equal(result.ok, expectedOk);
    assert.equal(writes, expectedOk ? 1 : 0);
  }
});

test("field writes use one live identity sample per guard without rebuilding the whole form", async () => {
  let handler,
    fullReads = 0,
    identityReads = 0;
  const element = { tagName: "INPUT", value: "OLD", isConnected: true };
  const pageFields = () => ({ "application.id": "CASE-A", "old_vehicle.vin": element.value });
  const context = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(fn) {
            handler = fn;
          },
        },
      },
    },
    document: { title: "审核页", images: [], body: { innerText: "", querySelectorAll: () => [] } },
    window: {
      location: { href: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1" },
    },
    console: { info() {} },
    globalThis: {
      crypto: { randomUUID: () => "page-instance" },
      ReviewBusinessDetector: {
        resolve: () => ({ business: { businessType: "scrap_replacement" } }),
      },
      ReviewPageFieldCollector: {
        collect: () => {
          fullReads++;
          return {
            pageFields: pageFields(),
            fieldTargets: [{ field: "old_vehicle.vin", element }],
            writableTargets: [],
            unmatchedLabels: [],
            scannedControls: 1,
            candidateCount: 1,
            ambiguousFields: [],
          };
        },
        collectIdentity: (_root, anchors) => {
          identityReads++;
          assert.ok(anchors.includes("application.id"));
          assert.ok(anchors.includes("old_vehicle.vin"));
          return {
            pageFields: pageFields(),
            fieldTargets: [{ field: "old_vehicle.vin", element }],
          };
        },
      },
      ReviewPageFieldWriter: {
        defaultWritableFields: new Set(["old_vehicle.vin"]),
        captureValue: () => ({ control: element, value: element.value }),
        executeValue: async (_root, _target, action, guard) => {
          assert.equal(guard(), true);
          element.value = action.value;
          assert.equal(guard(), true);
          return { ok: true, actions: [{ field: action.field, value: action.value }] };
        },
      },
      ReviewBusinessScope: {
        documentTypeFor: (_scope, _order, category) => category || "unknown",
        scopeForLabel: () => null,
        assign: () => [],
      },
      ReviewImageCandidates: { select: () => ({ selected: [], scannedCount: 0, overflow: false }) },
    },
  };
  await runContent(context);
  const collected = await new Promise((resolve) =>
    handler({ type: "COLLECT_PAGE_DATA" }, null, resolve),
  );
  const result = await new Promise((resolve) =>
    handler(
      {
        type: "APPLY_PAGE_FIELD_VALUE",
        action: { field: "old_vehicle.vin", value: "NEW", expectedValue: "OLD" },
        expectedPageUrl: collected.pageUrl,
        expectedPageInstanceId: collected.pageInstanceId,
        expectedCollectionId: collected.collectionId,
        expectedPageFingerprint: collected.pageFingerprint,
      },
      null,
      resolve,
    ),
  );
  assert.equal(result.ok, true);
  assert.equal(fullReads, 1, "only initial review collection builds the whole form");
  assert.equal(identityReads, 3, "preflight, prewrite and postwrite each take one sample");
});

test("manifest grants access to the production review host", () => {
  assert.ok(manifest.host_permissions.includes("https://admin.forjtruck.com/*"));
  assert.ok(manifest.host_permissions.includes("http://175.178.6.214:26101/*"));
});

test("collector scans every valid contenteditable form", () => {
  const collectorSource = readFileSync(
    new URL("../src/platform/browser/collection/page-field-collector.ts", import.meta.url),
    "utf8",
  );

  assert.match(collectorSource, /\[contenteditable\]:not\(\[contenteditable='false'\]\)/);
});

test("content script has no host-page review marker protocol", () => {
  assert.doesNotMatch(
    contentSource,
    /SHOW_REVIEW_FIELD_STEP|COMPLETE_REVIEW_FIELD_STEP|CLEAR_REVIEW_FIELD_MARKERS|REVIEW_FIELD_DECISION/,
  );
  assert.doesNotMatch(contentSource, /ReviewPageMarker/);
});

test("组合回填失败时说明是哪个字段、哪一条不满足", () => {
  // 组合回填有四种失败原因（控件找不到 / 不属于本次采集 / 不在白名单 / 写后回读
  // 失败），表现形式都是"点了没反应"。审核员看的是面板提示行，所以原因必须写在
  // 那里，而不是只丢进 Console——为了排查让审核员开开发者工具是不现实的。
  assert.match(contentSource, /页面上没找到这个控件/);
  assert.match(contentSource, /不属于本次采集/);
  assert.match(contentSource, /不在允许回填的字段清单里/);
  assert.match(contentSource, /组合字段无法回填：/);
});
