from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from backend.app.db.models import AgentCheckpoint, AgentCheckpointWrite, AgentRun


def _parts(config):
    configurable = config.get("configurable", {})
    namespace = configurable.get("checkpoint_ns", "")
    parts = namespace.split("/")
    thread_id = configurable.get("thread_id")
    if len(parts) != 3 or not all(parts) or not thread_id:
        raise ValueError("checkpoint namespace requires household/session/run and thread_id")
    return thread_id, namespace, parts


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, session_factory):
        super().__init__(); self.session_factory = session_factory

    def _session(self): return self.session_factory()

    def _validate(self, session, parts):
        run = session.scalar(select(AgentRun).where(AgentRun.id == parts[2], AgentRun.household_id == parts[0],
                                                     AgentRun.session_id == parts[1]))
        if not run: raise ValueError("checkpoint namespace is not owned by run")

    def _dump(self, value):
        kind, blob = self.serde.dumps_typed(value); return kind.encode() + b"\0" + blob

    def _loads(self, blob):
        kind, payload = blob.split(b"\0", 1); return self.serde.loads_typed((kind.decode(), payload))

    def put(self, config, checkpoint, metadata, new_versions):
        thread_id, namespace, parts = _parts(config); checkpoint_id = checkpoint["id"]
        with self._session() as session:
            self._validate(session, parts)
            existing = session.scalar(select(AgentCheckpoint).where(AgentCheckpoint.thread_id == thread_id,
                AgentCheckpoint.checkpoint_ns == namespace, AgentCheckpoint.checkpoint_id == checkpoint_id))
            if existing: session.delete(existing); session.flush()
            session.add(AgentCheckpoint(household_id=parts[0], session_id=parts[1], run_id=parts[2], thread_id=thread_id,
                checkpoint_ns=namespace, checkpoint_id=checkpoint_id,
                parent_checkpoint_id=config.get("configurable", {}).get("checkpoint_id"),
                checkpoint_blob=self._dump(checkpoint), metadata_blob=self._dump(metadata)))
            session.commit()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id}}

    def put_writes(self, config, writes, task_id, task_path=""):
        thread_id, namespace, parts = _parts(config); checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._session() as session:
            self._validate(session, parts)
            for index, (channel, value) in enumerate(writes):
                session.execute(delete(AgentCheckpointWrite).where(AgentCheckpointWrite.thread_id == thread_id,
                    AgentCheckpointWrite.checkpoint_ns == namespace, AgentCheckpointWrite.checkpoint_id == checkpoint_id,
                    AgentCheckpointWrite.task_id == task_id, AgentCheckpointWrite.write_index == index))
                session.add(AgentCheckpointWrite(household_id=parts[0], session_id=parts[1], run_id=parts[2],
                    thread_id=thread_id, checkpoint_ns=namespace, checkpoint_id=checkpoint_id, task_id=task_id,
                    task_path=task_path, write_index=index, channel=channel, value_blob=self._dump(value)))
            session.commit()

    def _tuple(self, session, row):
        writes = session.scalars(select(AgentCheckpointWrite).where(AgentCheckpointWrite.household_id == row.household_id,
            AgentCheckpointWrite.thread_id == row.thread_id, AgentCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
            AgentCheckpointWrite.checkpoint_id == row.checkpoint_id).order_by(AgentCheckpointWrite.write_index)).all()
        config = {"configurable": {"thread_id": row.thread_id, "checkpoint_ns": row.checkpoint_ns,
                                    "checkpoint_id": row.checkpoint_id}}
        parent = None if not row.parent_checkpoint_id else {"configurable": {"thread_id": row.thread_id,
            "checkpoint_ns": row.checkpoint_ns, "checkpoint_id": row.parent_checkpoint_id}}
        return CheckpointTuple(config, self._loads(row.checkpoint_blob), self._loads(row.metadata_blob), parent,
                               [(item.task_id, item.channel, self._loads(item.value_blob)) for item in writes])

    def get_tuple(self, config):
        thread_id, namespace, parts = _parts(config); requested = config.get("configurable", {}).get("checkpoint_id")
        with self._session() as session:
            self._validate(session, parts)
            statement = select(AgentCheckpoint).where(AgentCheckpoint.household_id == parts[0],
                AgentCheckpoint.run_id == parts[2], AgentCheckpoint.thread_id == thread_id,
                AgentCheckpoint.checkpoint_ns == namespace)
            statement = statement.where(AgentCheckpoint.checkpoint_id == requested) if requested else statement.order_by(AgentCheckpoint.created_at.desc())
            row = session.scalar(statement); return self._tuple(session, row) if row else None

    def list(self, config, *, filter=None, before=None, limit=None):
        thread_id, namespace, parts = _parts(config)
        with self._session() as session:
            self._validate(session, parts)
            statement = select(AgentCheckpoint).where(AgentCheckpoint.household_id == parts[0],
                AgentCheckpoint.run_id == parts[2], AgentCheckpoint.thread_id == thread_id,
                AgentCheckpoint.checkpoint_ns == namespace).order_by(AgentCheckpoint.created_at.desc())
            if before:
                before_id = before.get("configurable", {}).get("checkpoint_id")
                before_row = session.scalar(select(AgentCheckpoint).where(AgentCheckpoint.checkpoint_ns == namespace,
                                                                           AgentCheckpoint.checkpoint_id == before_id))
                if before_row: statement = statement.where(AgentCheckpoint.created_at < before_row.created_at)
            rows = session.scalars(statement).all(); count = 0
            for row in rows:
                item = self._tuple(session, row)
                if filter and any(item.metadata.get(key) != value for key, value in filter.items()): continue
                yield item; count += 1
                if limit is not None and count >= limit: break

    def delete_thread(self, thread_id, config=None):
        if config is None: raise ValueError("tenant-scoped config is required")
        scoped_thread, namespace, parts = _parts(config)
        if scoped_thread != thread_id: raise ValueError("thread mismatch")
        with self._session() as session:
            self._validate(session, parts)
            predicates = (AgentCheckpointWrite.household_id == parts[0], AgentCheckpointWrite.run_id == parts[2],
                          AgentCheckpointWrite.thread_id == thread_id, AgentCheckpointWrite.checkpoint_ns == namespace)
            session.execute(delete(AgentCheckpointWrite).where(*predicates))
            session.execute(delete(AgentCheckpoint).where(AgentCheckpoint.household_id == parts[0],
                AgentCheckpoint.run_id == parts[2], AgentCheckpoint.thread_id == thread_id,
                AgentCheckpoint.checkpoint_ns == namespace)); session.commit()


def checkpoint_saver_for(engine, session_factory):
    return InMemorySaver() if engine.dialect.name == "sqlite" else SqlAlchemyCheckpointSaver(session_factory)
