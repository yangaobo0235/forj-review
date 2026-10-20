import assert from "node:assert/strict";
import { test } from "vitest";

test("focuses an image only in the collected tab with the collected page identity", async () => {
  let focusReviewImage;
  try {
    ({ focusReviewImage } = await import("../src/platform/extension/image-focus-client.ts"));
  } catch {
    assert.fail("the image focus client is required");
  }
  const messages = [];
  const chromeApi = {
    tabs: {
      sendMessage: async (tabId, message) => {
        messages.push({ tabId, message });
        return { ok: true };
      },
    },
  };
  const target = {
    sourceTabId: 42,
    pageUrl: "https://admin.forjtruck.com/scrap-replace-qingdao?showPageModel=1",
    pageInstanceId: "page-a",
    pageFingerprint: '[["application.id","case-a"]]',
    collectionId: "collection-a",
  };

  const result = await focusReviewImage("unknown-01", target, chromeApi);

  assert.equal(result.ok, true);
  assert.deepEqual(messages, [{
    tabId: 42,
    message: {
      type: "FOCUS_REVIEW_IMAGE",
      imageId: "unknown-01",
      expectedPageUrl: target.pageUrl,
      expectedPageInstanceId: target.pageInstanceId,
      expectedPageFingerprint: target.pageFingerprint,
      expectedCollectionId: target.collectionId,
    },
  }]);
});
