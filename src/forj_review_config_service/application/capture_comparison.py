"""Compare immutable directories by semantics; local IDs never cross captures."""

from collections import defaultdict


def compare_captures(previous, current):
    def suggestions(old_items, new_items, kind):
        def signature(item):
            if not item.label.strip():
                return None
            if kind == "field":
                return (
                    item.label.strip(),
                    item.section,
                    item.group_label,
                    item.control_kind,
                    item.locator_id,
                    item.locator_name,
                )
            return (
                item.label.strip(),
                item.section,
                item.group,
                tuple(item.group_path),
                tuple(sorted(item.locator.items())),
            )

        groups = defaultdict(list)
        for item in new_items:
            key = signature(item)
            if key is not None:
                groups[key].append(item)
        old_counts = defaultdict(int)
        for item in old_items:
            old_counts[signature(item)] += 1
        result = []
        for item in old_items:
            key = signature(item)
            matches = groups.get(key, []) if key is not None else []
            identity = "candidate_id" if kind == "field" else "sample_id"
            unique = len(matches) == 1 and old_counts[key] == 1
            result.append(
                {
                    "previous_id": getattr(item, identity),
                    "candidate_ids": [getattr(m, identity) for m in matches],
                    "status": "UNIQUE_SEMANTIC_MATCH" if unique else "NEEDS_INVESTIGATION",
                    "observation_reusable": False,
                    "reason": "标签、分区和定位线索唯一对应；需按新现场核对"
                    if unique
                    else "标签或定位不足，不能按序号认同一项",
                }
            )
        return result

    return {
        "previous_snapshot_id": previous.snapshot_id,
        "snapshot_id": current.snapshot_id,
        "fields": suggestions(previous.fields, current.fields, "field"),
        "materials": suggestions(previous.samples, current.samples, "material"),
        "notice": "仅为语义对应线索，不继承旧图片观察或浏览器证明；地址相同也不证明内容相同",
    }
