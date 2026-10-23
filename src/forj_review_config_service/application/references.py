"""References describe evidence used by a task, never its desired configuration."""

from forj_review_config_service.contracts.models import ConfigurationError


def add_references(record, fields, samples):
    if record.snapshot is None:
        raise ConfigurationError("CAPTURE_REQUIRED", "请先关联业务页面现场")
    if set(fields) - {f.candidate_id for f in record.snapshot.fields} or set(samples) - {
        s.sample_id for s in record.snapshot.samples
    }:
        raise ConfigurationError("INVALID_REFERENCE", "引用必须来自当前页面现场", 422)
    record.reference_fields = sorted(set(record.reference_fields) | set(fields))
    record.reference_samples = sorted(set(record.reference_samples) | set(samples))
    # The old proof binding included reference IDs. Keep that binding conservative.
    record.report = None
    return {"fields": record.reference_fields, "samples": record.reference_samples}
