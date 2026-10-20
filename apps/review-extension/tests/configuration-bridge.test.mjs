import assert from "node:assert/strict";
import { test } from "vitest";
import {captureAndOpenWorkbench, handleCollectionTrial} from "../src/features/configuration-capture/bridge.ts";

const captureId = "a".repeat(32);
const binding = {tab_id: 42, snapshot_id: "snapshot", page_instance_id: "instance", page_url: "https://example.test/permit", structure_digest: "structure"};
const task = {...binding, id: "trial", kind: "browser", candidate_digest: "candidate", manifest: {fields: []}};
const message = {type: "CONFIGURATION_COLLECTION_TRIAL", capture_id: captureId, task};

test("capture uploads the snapshot and opens only a capture identifier in the independent web app", async () => {
  const oldChrome = globalThis.chrome, oldFetch = globalThis.fetch;
  const values = {}, opened = [];
  try {
    globalThis.chrome = {runtime: {id: "b".repeat(32)}, tabs: {
      query: async () => [{id: 42, url: binding.page_url}],
      sendMessage: async (id, request) => {assert.equal(id, 42); assert.equal(request.type, "DISCOVER_CONFIGURATION_PAGE"); return {...binding, fields: [], samples: []};},
      create: async tab => opened.push(tab.url),
    }, storage: {local: {set: async data => Object.assign(values, data)}}};
    globalThis.fetch = async (url, init) => {
      assert.equal(url, "http://127.0.0.1:26102/api/review-config/captures");
      assert.equal(JSON.parse(init.body).snapshot.snapshot_id, "snapshot");
      assert.deepEqual(init.headers, {"content-type": "application/json"});
      return Response.json({capture_id: captureId});
    };
    await captureAndOpenWorkbench();
    assert.deepEqual(opened, [`http://127.0.0.1:26198/?capture=${captureId}`]);
    assert.deepEqual(values[`configuration.capture.${captureId}`], binding);
  } finally {globalThis.chrome = oldChrome; globalThis.fetch = oldFetch;}
});

test("workbench trial uses the captured source tab even when a different tab is active", async () => {
  const previous = globalThis.chrome;
  try {
    globalThis.chrome = {storage: {local: {get: async key => ({[key]: binding})}}, tabs: {
      get: async id => {assert.equal(id, 42); return {url: binding.page_url};},
      query: async () => {throw new Error("must not use the active web tab");},
      sendMessage: async (id, request) => {assert.equal(id, 42); assert.equal(request.type, "TRIAL_CONFIGURATION_PAGE"); return {fields: {vin: 1}};},
    }};
    assert.deepEqual(await handleCollectionTrial(message, "http://127.0.0.1:26198/?session=x"), {fields: {vin: 1}});
  } finally {globalThis.chrome = previous;}
});

test("bridge rejects other origins, changed snapshots and navigated source records", async () => {
  const previous = globalThis.chrome;
  try {
    globalThis.chrome = {storage: {local: {get: async key => ({[key]: binding})}}, tabs: {
      get: async () => ({url: binding.page_url + "?record=other"}),
      sendMessage: async () => {throw new Error("must not reach collection");},
    }};
    await assert.rejects(handleCollectionTrial(message, "https://example.test"), /仅配置工作台/);
    await assert.rejects(handleCollectionTrial(message, "http://localhost.evil.test:26198"), /仅配置工作台/);
    await assert.rejects(handleCollectionTrial({...message, task: {...task, snapshot_id: "other"}}, "http://localhost:26198"), /不一致/);
    await assert.rejects(handleCollectionTrial(message, "http://localhost:26198"), /切换记录/);
  } finally {globalThis.chrome = previous;}
});

test("closed source tabs have a recoverable recapture instruction", async () => {
  const previous = globalThis.chrome;
  try {
    globalThis.chrome = {storage: {local: {get: async key => ({[key]: binding})}}, tabs: {get: async () => {throw new Error("closed");}}};
    await assert.rejects(handleCollectionTrial(message, "http://127.0.0.1:26198"), /已关闭.*重新/);
  } finally {globalThis.chrome = previous;}
});
