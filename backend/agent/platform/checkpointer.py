from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from backend.app.db.models import AgentCheckpoint, AgentCheckpointWrite


def _parts(config):
    configurable = config.get("configurable", {})
    namespace = configurable.get("checkpoint_ns", "")
    parts = namespace.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError("checkpoint_ns must be household/session/run")
    return configurable.get("thread_id"), namespace, parts


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, session_factory):
        super().__init__()
        self.session_factory = session_factory

    def _session(self):
        return self.session_factory()

    def put(self, config, checkpoint, metadata, new_versions):
        thread_id, namespace, parts = _parts(config)
        checkpoint_id = checkpoint["id"]
        parent_id = config.get("configurable", {}).get("checkpoint_id")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(metadata)
        with self._session() as session:
            row = AgentCheckpoint(household_id=parts[0], session_id=parts[1], run_id=parts[2], thread_id=thread_id,
                                  checkpoint_ns=namespace, checkpoint_id=checkpoint_id, parent_checkpoint_id=parent_id,
                                  checkpoint_blob=checkpoint_type.encode() + b"\0" + checkpoint_blob,
                                  metadata_blob=metadata_type.encode() + b"\0" + metadata_blob)
            session.add(row); session.commit()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id}}

    def put_writes(self, config, writes, task_id, task_path=""):
        thread_id, namespace, parts = _parts(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._session() as session:
            for index, (channel, value) in enumerate(writes):
                value_type, value_blob = self.serde.dumps_typed(value)
                existing = session.scalar(select(AgentCheckpointWrite).where(
                    AgentCheckpointWrite.thread_id == thread_id, AgentCheckpointWrite.checkpoint_ns == namespace,
                    AgentCheckpointWrite.checkpoint_id == checkpoint_id, AgentCheckpointWrite.task_id == task_id,
                    AgentCheckpointWrite.write_index == index))
                if existing:
                    session.delete(existing); session.flush()
                session.add(AgentCheckpointWrite(household_id=parts[0], session_id=parts[1], run_id=parts[2],
                            thread_id=thread_id, checkpoint_ns=namespace, checkpoint_id=checkpoint_id,
                            task_id=task_id, task_path=task_path, write_index=index, channel=channel,
                            value_blob=value_type.encode() + b"\0" + value_blob))
            session.commit()

    def _loads(self, blob):
        type_name, payload = blob.split(b"\0", 1)
        return self.serde.loads_typed((type_name.decode(), payload))

    def get_tuple(self, config):
        thread_id, namespace, _parts_value = _parts(config)
        requested = config.get("configurable", {}).get("checkpoint_id")
        with self._session() as session:
            statement = select(AgentCheckpoint).where(AgentCheckpoint.thread_id == thread_id, AgentCheckpoint.checkpoint_ns == namespace)
            if requested:
                statement = statement.where(AgentCheckpoint.checkpoint_id == requested)
            else:
                statement = statement.order_by(AgentCheckpoint.created_at.desc())
            row = session.scalar(statement)
            if not row:
                return None
            writes = session.scalars(select(AgentCheckpointWrite).where(
                AgentCheckpointWrite.thread_id == thread_id, AgentCheckpointWrite.checkpoint_ns == namespace,
                AgentCheckpointWrite.checkpoint_id == row.checkpoint_id).order_by(AgentCheckpointWrite.write_index)).all()
            result_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": row.checkpoint_id}}
            parent = None if not row.parent_checkpoint_id else {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": row.parent_checkpoint_id}}
            pending = [(item.task_id, item.channel, self._loads(item.value_blob)) for item in writes]
            return CheckpointTuple(result_config, self._loads(row.checkpoint_blob), self._loads(row.metadata_blob), parent, pending)

    def list(self, config, *, filter=None, before=None, limit=None):
        item = self.get_tuple(config) if config else None
        if item:
            yield item

    def delete_thread(self, thread_id):
        with self._session() as session:
            session.execute(delete(AgentCheckpointWrite).where(AgentCheckpointWrite.thread_id == thread_id))
            session.execute(delete(AgentCheckpoint).where(AgentCheckpoint.thread_id == thread_id)); session.commit()


def checkpoint_saver_for(engine, session_factory):
    if engine.dialect.name == "sqlite":
        return InMemorySaver()
    return SqlAlchemyCheckpointSaver(session_factory)
