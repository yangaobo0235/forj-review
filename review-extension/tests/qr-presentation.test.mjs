import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { qrCheckPresentation } from "../src/qrPresentation.ts";

test("matched qr check exposes only official status and extracted fields", () => {
  const presentation = qrCheckPresentation({
    status: "MATCH",
    raw_value: "http://secret.example/token",
    url: "https://secret.example/token",
    page_fields: { certificate_no: "CERT-1", vin: "VIN-1" },
    message: "二维码网页字段已提取",
  });

  assert.deepEqual(presentation, {
    title: "官网核验通过",
    visualStatus: "MATCH",
    fields: [
      { label: "回收证明编号", value: "CERT-1" },
      { label: "车架号", value: "VIN-1" },
    ],
    message: "二维码网页字段已提取",
  });
  assert.doesNotMatch(JSON.stringify(presentation), /secret\.example/);
});

test("invalid qr domain is presented as an explicit failure", () => {
  const presentation = qrCheckPresentation({
    status: "REVIEW_REQUIRED",
    raw_value: "https://evil.example/token",
    url: null,
    domain_valid: false,
    accessible: null,
    page_fields: {},
    message: "二维码网页无法完成核验",
  });

  assert.deepEqual(presentation, {
    title: "官网网址不正确",
    visualStatus: "CONFLICT",
    fields: [],
    message: "二维码网页无法完成核验",
  });
  assert.doesNotMatch(JSON.stringify(presentation), /evil\.example/);
});

test("valid qr domain requiring review keeps the warning presentation", () => {
  const presentation = qrCheckPresentation({
    status: "REVIEW_REQUIRED",
    domain_valid: true,
    accessible: false,
    page_fields: {},
    message: "二维码网页无法完成核验",
  });

  assert.deepEqual(presentation, {
    title: "官网核验待复核",
    visualStatus: "REVIEW_REQUIRED",
    fields: [],
    message: "二维码网页无法完成核验",
  });
});

test("qr field layout isolates itself from the global definition list grid", () => {
  const css = (readFileSync(new URL("../src/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/components/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  assert.match(css, /\.qr-fields\s*{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
  assert.match(css, /\.qr-fields dd\s*{[^}]*word-break:\s*normal/s);
  assert.match(
    css,
    /@media\s*\(max-width:\s*420px\)[\s\S]*?\.qr-fields\s*>\s*div\s*{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s,
  );
});

test("qr conflict explanation is rendered in the failure color", () => {
  const css = (readFileSync(new URL("../src/App.css", import.meta.url), "utf8") + readFileSync(new URL("../src/components/EvidenceReviewWorkbench.css", import.meta.url), "utf8"));

  assert.match(css, /\.status-conflict strong\s*{[^}]*color:\s*#cf1322/s);
  assert.match(css, /\.qr-check\.status-conflict small\s*{[^}]*color:\s*#cf1322/s);
  assert.match(css, /\.status-review_required strong\s*{[^}]*color:\s*#d48806/s);
});

test("qr card source does not render raw or canonical urls", () => {
  const source = readFileSync(new URL("../src/components/ReviewResults.tsx", import.meta.url), "utf8");
  const card = source.slice(source.indexOf('function QrResults'), source.indexOf("interface ResultGroupProps"));
  assert.doesNotMatch(card, /check\.(?:url|raw_value)/);
});

test("qr card uses the presentation status for its color", () => {
  const source = readFileSync(new URL("../src/components/ReviewResults.tsx", import.meta.url), "utf8");
  const card = source.slice(source.indexOf('function QrResults'), source.indexOf("interface ResultGroupProps"));

  assert.match(card, /status-\$\{presentation\.visualStatus\.toLowerCase\(\)\}/);
  assert.doesNotMatch(card, /status-\$\{check\.status\.toLowerCase\(\)\}/);
});
