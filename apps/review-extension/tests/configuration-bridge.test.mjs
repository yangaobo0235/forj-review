import assert from "node:assert/strict";
import { test } from "vitest";
import {
  captureAndOpenWorkbench,
  handleConfigurationRequest,
} from "../src/features/configuration-capture/bridge.ts";

const captureId = "a".repeat(32);
const binding = {
  tab_id: 42,
  snapshot_id: "snapshot",
  page_instance_id: "instance",
  page_url: "https://example.test/permit",
  structure_digest: "structure",
};
const task = {
  ...binding,
  id: "trial",
  kind: "browser",
  candidate_digest: "candidate",
  manifest: { fields: [] },
};
const message = { type: "CONFIGURATION_COLLECTION_TRIAL", capture_id: captureId, task };

test("removed binary upload command is rejected without downloading or posting images", async () => {
  const oldFetch = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => {
      calls++;
      throw new Error("must not download");
    };
    await assert.rejects(
      handleConfigurationRequest(
        { type: "CONFIGURATION_CAPTURE_IMAGE", capture_id: captureId, sample_id: "sample_0" },
        "http://localhost:26198",
      ),
      /只读采集请求无效/,
    );
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test("capture uploads the snapshot and opens only a capture identifier in the independent web app", async () => {
  const oldChrome = globalThis.chrome,
    oldFetch = globalThis.fetch;
  const values = {},
    opened = [];
  try {
    globalThis.chrome = {
      runtime: { id: "b".repeat(32) },
      tabs: {
        query: async () => [{ id: 42, url: binding.page_url }],
        sendMessage: async (id, request) => {
          assert.equal(id, 42);
          assert.equal(request.type, "DISCOVER_CONFIGURATION_PAGE");
          return { ...binding, fields: [], samples: [] };
        },
        create: async (tab) => opened.push(tab.url),
      },
      storage: {
        local: {
          get: async () => ({}),
          remove: async () => {},
          set: async (data) => Object.assign(values, data),
        },
      },
    };
    globalThis.fetch = async (url, init) => {
      assert.equal(url, "http://127.0.0.1:26102/api/review-config/captures");
      assert.equal(JSON.parse(init.body).snapshot.snapshot_id, "snapshot");
      assert.deepEqual(init.headers, { "content-type": "application/json" });
      return Response.json({ capture_id: captureId });
    };
    await captureAndOpenWorkbench();
    assert.deepEqual(opened, [`http://127.0.0.1:26198/?capture=${captureId}`]);
    assert.deepEqual(values[`configuration.url-capture.${captureId}`], binding);
  } finally {
    globalThis.chrome = oldChrome;
    globalThis.fetch = oldFetch;
  }
});

test("workbench trial uses the captured source tab even when a different tab is active", async () => {
  const previous = globalThis.chrome;
  try {
    globalThis.chrome = {
      storage: { local: { get: async (key) => ({ [key]: binding }) } },
      tabs: {
        get: async (id) => {
          assert.equal(id, 42);
          return { url: binding.page_url };
        },
        query: async () => {
          throw new Error("must not use the active web tab");
        },
        sendMessage: async (id, request) => {
          assert.equal(id, 42);
          assert.equal(request.type, "TRIAL_CONFIGURATION_PAGE");
          return { fields: { vin: 1 } };
        },
      },
    };
    assert.deepEqual(await handleConfigurationRequest(message, "http://127.0.0.1:26198/?task=x"), {
      fields: { vin: 1 },
    });
  } finally {
    globalThis.chrome = previous;
  }
});

test("bridge rejects other origins, changed snapshots and navigated source records", async () => {
  const previous = globalThis.chrome;
  try {
    globalThis.chrome = {
      storage: { local: { get: async (key) => ({ [key]: binding }) } },
      tabs: {
        get: async () => ({ url: binding.page_url + "?record=other" }),
        sendMessage: async () => {
          throw new Error("must not reach collection");
        },
      },
    };
    await assert.rejects(
      handleConfigurationRequest(message, "https://example.test"),
      /仅配置工作台/,
    );
    await assert.rejects(
      handleConfigurationRequest(message, "http://localhost.evil.test:26198"),
      /仅配置工作台/,
    );
    await assert.rejects(
      handleConfigurationRequest(
        { ...message, task: { ...task, snapshot_id: "other" } },
        "http://localhost:26198",
      ),
      /不一致/,
    );
    await assert.rejects(handleConfigurationRequest(message, "http://localhost:26198"), /切换记录/);
  } finally {
    globalThis.chrome = previous;
  }
});

test("closed source tabs have a recoverable recapture instruction", async () => {
  const previous = globalThis.chrome;
  try {
    globalThis.chrome = {
      storage: { local: { get: async (key) => ({ [key]: binding }) } },
      tabs: {
        get: async () => {
          throw new Error("closed");
        },
      },
    };
    await assert.rejects(
      handleConfigurationRequest(message, "http://127.0.0.1:26198"),
      /已关闭.*重新/,
    );
  } finally {
    globalThis.chrome = previous;
  }
});

const catalog = {
  version: "1",
  identities: [
    {
      page_id: "permit",
      business_type: "permit_review",
      region: "default",
      profile_version: "2",
      paths: ["/permit"],
      anchors: [],
    },
  ],
};
const manifest = {
  profile_version: "2",
  effective_digest: "released",
  scopes: [],
  fields: [{ key: "vin" }],
  materials: [],
  page_groups: [],
};
const adoption = {
  type: "CONFIGURATION_ADOPTION_CHECK",
  capture_id: captureId,
  task: {
    id: "adopt",
    kind: "adoption",
    snapshot_id: binding.snapshot_id,
    page_instance_id: binding.page_instance_id,
    page_url: binding.page_url,
    page_id: "permit",
    version: "2",
    effective_digest: "released",
  },
};

test("adoption independently fetches the released manifest and uses the bound business tab", async () => {
  const oldChrome = globalThis.chrome,
    oldFetch = globalThis.fetch;
  const reads = [];
  try {
    globalThis.fetch = async (url) => {
      reads.push(String(url));
      return Response.json(String(url).includes("page-catalog") ? catalog : manifest);
    };
    globalThis.chrome = {
      storage: { local: { get: async (key) => ({ [key]: binding }) } },
      tabs: {
        get: async () => ({ url: binding.page_url }),
        sendMessage: async (id, request) => {
          assert.equal(id, 42);
          assert.equal(request.task.manifest.effective_digest, "released");
          return {
            page_instance_id: binding.page_instance_id,
            page_url: binding.page_url,
            matched_page_id: "permit",
            fields: { vin: 1 },
            diagnostics: [],
          };
        },
      },
    };
    const result = await handleConfigurationRequest(adoption, "http://127.0.0.1:26198");
    assert.equal(reads.length, 2);
    assert.equal(result.version, "2");
    assert.equal(result.effective_digest, "released");
    assert.equal(result.page_id, "permit");
    globalThis.fetch = async (url) =>
      Response.json(
        String(url).includes("page-catalog")
          ? catalog
          : { ...manifest, effective_digest: "changed" },
      );
    await assert.rejects(
      handleConfigurationRequest(adoption, "http://127.0.0.1:26198"),
      /版本或摘要不一致/,
    );
  } finally {
    globalThis.chrome = oldChrome;
    globalThis.fetch = oldFetch;
  }
});
