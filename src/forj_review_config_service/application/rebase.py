"""Three-way merge of configuration data; conflicts require explicit user choices."""

from copy import deepcopy

MISSING = object()
METADATA = {"version", "active"}


def merge_candidate(base, candidate, current, resolutions=None):
    conflicts = []
    resolutions = resolutions or {}

    def merge(old, ours, theirs, path):
        if ours == old or ours == theirs:
            return theirs
        if theirs == old:
            return ours
        if all(isinstance(v, dict) for v in (old, ours, theirs)):
            result = {}
            for key in sorted(old.keys() | ours.keys() | theirs.keys()):
                value = merge(
                    old.get(key, MISSING),
                    ours.get(key, MISSING),
                    theirs.get(key, MISSING),
                    f"{path}/{key}",
                )
                if value is not MISSING:
                    result[key] = value
            return result
        # Audit fields have stable keys; merging by list position would corrupt deletions.
        if path == "/fields" and all(isinstance(v, list) for v in (old, ours, theirs)):
            mapped = [{f["key"]: f for f in values} for values in (old, ours, theirs)]
            merged = merge(*mapped, path)
            order = list(dict.fromkeys([f["key"] for f in theirs + ours]))
            return [merged[key] for key in order if key in merged]
        conflicts.append(
            {
                "path": path,
                "base": None if old is MISSING else old,
                "candidate": None if ours is MISSING else ours,
                "current": None if theirs is MISSING else theirs,
                "candidate_missing": ours is MISSING,
                "current_missing": theirs is MISSING,
            }
        )
        return ours if resolutions.get(path) == "candidate" else theirs

    old, ours, theirs = [
        {k: deepcopy(v) for k, v in data.items() if k not in METADATA}
        for data in (base, candidate, current)
    ]
    result = merge(old, ours, theirs, "")
    result.update({k: current[k] for k in METADATA if k in current})
    return result, conflicts
