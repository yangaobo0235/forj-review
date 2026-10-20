"""Async configuration port backed by a deterministic in-memory snapshot."""


class AsyncSnapshotKernel:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def baseline(self):
        return self.snapshot.baseline()

    async def context(self, page_id=None):
        return self.snapshot.context(page_id)

    async def capabilities(self):
        return self.snapshot.capabilities()

    async def rollback_draft(self, page_id, version):
        return self.snapshot.rollback_draft(page_id, version)

    async def template_members(self, reference):
        return self.snapshot.template_members(reference)

    async def validate(self, record):
        return self.snapshot.validate(record)

    async def replay(self, record, case_set_id):
        return await self.snapshot.replay(record, case_set_id)

    async def verify_replay_receipt(self, result):
        return self.snapshot.verify_replay_receipt(result)

    async def commit(self, record, operation_id):
        return self.snapshot.commit(record, operation_id)

    async def commit_receipt(self, operation_id):
        return self.snapshot.commit_receipt(operation_id)

    async def activation(self, record):
        return await self.snapshot.activation(record)
