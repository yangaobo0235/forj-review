"""Load a validated in-memory page snapshot from PostgreSQL at startup."""

from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.integrations.database import AsyncDatabase
from forj_review_service.integrations.storage.configuration import read_state_async
from forj_review_service.runtime.configuration.service import validated_snapshot


async def load_registry(database: AsyncDatabase) -> BusinessRegistry:
    async with database.session(readonly=True, repeatable=True) as session:
        state = await read_state_async(session, active_only=True)
    _, registry = validated_snapshot(state)
    disabled = {d["page_id"] for d in state.definitions if d.get("enabled") is False}
    if disabled:
        registry = BusinessRegistry(
            tuple(p for p in registry.profiles if p.page_id not in disabled),
            active_profiles=tuple(p for p in registry.active_profiles if p.page_id not in disabled),
        )
    registry.release_id = state.release_id
    return registry
