/** Local technical diagnostics. Never pass page values, text, URLs or responses. */
const stringKeys = new Set(["collectionId", "job_id", "trace_id", "business", "region", "mode", "field", "code", "status", "errorType", "control"]);
const numberKeys = new Set(["fieldCount", "problemCount", "actionCount", "durationMs", "candidateCount", "matchedCount", "scannedControls", "matchedFields", "reviewFieldCount", "unmatchedLabelCount", "ambiguousFieldCount", "scanned", "eligible", "selectedCount", "unknownTypeCount", "imageCount", "imageFailureCount", "scannedImages", "selectedImages"]);
const booleanKeys = new Set(["ok", "failed", "catalogApplied", "imageOverflow"]);

export function logDiagnostic(event: string, details: Record<string, unknown> = {}) {
  const safe: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(details)) {
    if (numberKeys.has(key) && typeof value === "number" && Number.isFinite(value)) safe[key] = value;
    else if (booleanKeys.has(key) && typeof value === "boolean") safe[key] = value;
    else if (stringKeys.has(key) && typeof value === "string" && /^[A-Za-z0-9_.:-]{1,96}$/.test(value)) safe[key] = value;
  }
  console.info(`[ForjReview] ${event}`, safe);
}
