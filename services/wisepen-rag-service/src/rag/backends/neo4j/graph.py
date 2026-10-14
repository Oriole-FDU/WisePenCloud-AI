"""Neo4j 单库来源替换与拓扑读写，不负责 ACL、版本选择或模型生成。"""

import json
import re
from dataclasses import asdict

from neo4j import AsyncDriver, AsyncTransaction
from pydantic import TypeAdapter

from rag.core.schema import GraphEdge, GraphNode
from rag.core.schema.filters import MetadataFilters
from rag.storage.graph import GraphDirection, GraphSourceProjection, GraphStorage

from .filters import compile_filters

_node_adapter = TypeAdapter(GraphNode)
_edge_adapter = TypeAdapter(GraphEdge)


class Neo4jGraphStorage(GraphStorage):
    """共享图元只存身份，来源关联各自存事实；不同实例可绑定不同物理图。

    每批来源替换使用一个显式事务，不自动重试。Schema 必须由调用方显式创建。
    无权/旧版本来源过滤属于 application；读回数据仍然只是待授权候选。
    """

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str = "neo4j",
        graph_name: str = "rag_v4",
        batch_size: int = 64,
    ) -> None:
        # 图名必须是合法 Cypher 标识符
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", graph_name):
            raise ValueError("graph_name must be a Cypher identifier")
        if not database.strip() or type(batch_size) is not int or batch_size <= 0:
            raise ValueError("database and positive batch size are required")
        self._driver = driver
        self._database = database
        self._graph_name = graph_name
        self._batch_size = batch_size
        self._entity = f"{graph_name}_Entity"
        self._relation = f"{graph_name}_Relation"
        self._source = f"{graph_name}_Source"

    async def ensure_schema(self) -> None:
        statements = [
            f"CREATE CONSTRAINT {self._graph_name}_entity_id IF NOT EXISTS FOR (n:{self._entity}) REQUIRE n.node_id IS UNIQUE",
            f"CREATE CONSTRAINT {self._graph_name}_relation_id IF NOT EXISTS FOR (r:{self._relation}) REQUIRE r.edge_id IS UNIQUE",
            f"CREATE CONSTRAINT {self._graph_name}_source_id IF NOT EXISTS FOR (s:{self._source}) REQUIRE s.chunk_id IS UNIQUE",
            f"CREATE INDEX {self._graph_name}_source_revision IF NOT EXISTS FOR (s:{self._source}) ON (s.meta_resource_id, s.meta_content_revision)",
        ]
        async with self._driver.session(database=self._database) as session:
            for statement in statements:
                result = await session.run(statement)
                await result.consume()

    async def replace_sources(self, projections: list[GraphSourceProjection]) -> None:
        if not projections:
            return
        rows = []
        # 全部先序列化；不可表示的事实在任何写入前报错，不用 str 掩盖类型问题。
        for projection in projections:
            if any(not key or "." in key for key in projection.metadata):
                raise ValueError("Neo4j source metadata requires flat keys")
            rows.append(
                {
                    "chunk_id": projection.chunk_id,
                    "properties": {
                        f"meta_{key}": value
                        for key, value in projection.metadata.items()
                    },
                    "nodes": [
                        {
                            "node_id": node.id,
                            "payload": json.dumps(
                                asdict(node), ensure_ascii=False, allow_nan=False
                            ),
                        }
                        for node in projection.nodes
                    ],
                    "edges": [
                        {
                            "edge_id": edge.id,
                            "source_id": edge.source_id,
                            "target_id": edge.target_id,
                            "relation_type": edge.relation_type,
                            "payload": json.dumps(
                                asdict(edge), ensure_ascii=False, allow_nan=False
                            ),
                        }
                        for edge in projection.edges
                    ],
                }
            )

        for start in range(0, len(rows), self._batch_size):
            batch = rows[start : start + self._batch_size]
            async with (
                self._driver.session(database=self._database) as session,
                await session.begin_transaction() as transaction,
            ):
                # 先移除本批旧来源关联
                affected = await self._remove_sources(
                    transaction,
                    "s.chunk_id IN $chunk_ids",
                    {"chunk_ids": [row["chunk_id"] for row in batch]},
                )

                # 空输入来源只移除旧贡献，不保留无事实的来源占位节点。
                active = [row for row in batch if row["nodes"] or row["edges"]]

                if active:
                    # 写入来源节点属性
                    result = await transaction.run(
                        f"""// graph:upsert_sources
                        UNWIND $rows AS row
                        MERGE (s:{self._source} {{chunk_id: row.chunk_id}})
                        SET s += row.properties
                        """,
                        rows=active,
                    )
                    await result.consume()

                    # 写入节点事实
                    node_count = sum(len(row["nodes"]) for row in active)
                    if node_count:
                        result = await transaction.run(
                            f"""// graph:upsert_nodes
                            UNWIND $rows AS row
                            MATCH (s:{self._source} {{chunk_id: row.chunk_id}})
                            UNWIND row.nodes AS item
                            MERGE (n:{self._entity} {{node_id: item.node_id}})
                            MERGE (s)-[fact:RAG_NODE_FACT]->(n)
                            SET fact.payload = item.payload
                            RETURN count(*) AS written
                            """,
                            rows=active,
                        )
                        written = await result.single(strict=True)
                        await result.consume()
                        # 写入条数必须与预期一致
                        if written["written"] != node_count:
                            raise RuntimeError("Neo4j did not write all node facts")

                    # 写入关系事实
                    edge_count = sum(len(row["edges"]) for row in active)
                    if edge_count:
                        result = await transaction.run(
                            f"""// graph:upsert_edges
                            UNWIND $rows AS row
                            MATCH (s:{self._source} {{chunk_id: row.chunk_id}})
                            UNWIND row.edges AS item
                            MATCH (source:{self._entity} {{node_id: item.source_id}})
                            MATCH (target:{self._entity} {{node_id: item.target_id}})
                            MERGE (r:{self._relation} {{edge_id: item.edge_id}})
                            SET r.relation_type = item.relation_type
                            MERGE (r)-[:RAG_FROM]->(source)
                            MERGE (r)-[:RAG_TO]->(target)
                            MERGE (s)-[fact:RAG_EDGE_FACT]->(r)
                            SET fact.payload = item.payload
                            RETURN count(*) AS written
                            """,
                            rows=active,
                        )
                        written = await result.single(strict=True)
                        await result.consume()
                        # 写入条数必须与预期一致
                        if written["written"] != edge_count:
                            raise RuntimeError(
                                "Neo4j did not write all relationship facts"
                            )

                # 清理本次移除后遗留的孤立图元
                await self._cleanup_orphans(transaction, affected)
                # 等待 commit；前置异常回滚。提交网络异常可能结果未知，传播后由幂等重跑恢复。
                await transaction.commit()

    async def delete_sources(self, filters: MetadataFilters) -> None:
        expression, parameters = compile_filters(filters)
        async with (
            self._driver.session(database=self._database) as session,
            await session.begin_transaction() as transaction,
        ):
            affected = await self._remove_sources(transaction, expression, parameters)
            await self._cleanup_orphans(transaction, affected)
            await transaction.commit()

    async def _remove_sources(
        self,
        transaction: AsyncTransaction,
        expression: str,
        parameters: dict[str, object],
    ) -> dict:
        """删除匹配的来源节点，返回被解除引用的节点/边 ID。"""
        result = await transaction.run(
            f"""// graph:remove_sources
            MATCH (s:{self._source}) WHERE {expression}
            OPTIONAL MATCH (s)-->(target)
            WITH collect(DISTINCT s) AS sources,
                collect(DISTINCT CASE WHEN target:{self._entity} THEN target.node_id END) AS node_ids,
                collect(DISTINCT CASE WHEN target:{self._relation} THEN target.edge_id END) AS edge_ids
            FOREACH (source IN sources | DETACH DELETE source)
            RETURN node_ids, edge_ids
            """,
            **parameters,
        )
        affected = await result.single(strict=True)
        await result.consume()
        return dict(affected)

    async def _cleanup_orphans(
        self, transaction: AsyncTransaction, affected: dict
    ) -> None:
        """只清理本次触碰的图元，不做全图垃圾扫描。"""

        # 无人引用的事实边直接删除
        result = await transaction.run(
            f"""// graph:cleanup_relations
            MATCH (r:{self._relation}) WHERE r.edge_id IN $edge_ids
            AND NOT EXISTS {{ MATCH (:{self._source})-[:RAG_EDGE_FACT]->(r) }}
            DETACH DELETE r
            """,
            edge_ids=affected["edge_ids"],
        )
        await result.consume()

        # 无人引用的节点直接删除
        result = await transaction.run(
            f"""// graph:cleanup_nodes
            MATCH (n:{self._entity}) WHERE n.node_id IN $node_ids
            AND NOT EXISTS {{ MATCH (:{self._source})-[:RAG_NODE_FACT]->(n) }}
            AND NOT EXISTS {{ MATCH (:{self._relation})-[:RAG_FROM|RAG_TO]->(n) }}
            DELETE n
            """,
            node_ids=affected["node_ids"],
        )
        await result.consume()

    async def _read(self, query: str, parameters: dict[str, object]) -> list[dict]:
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, **parameters)
            records = await result.data()
            await result.consume()
        return records

    async def get_nodes(
        self, node_ids: list[str], *, filters: MetadataFilters | None = None
    ) -> list[GraphNode]:
        if not node_ids:
            return []
        # 未给 filters 时用 true 占位，避免拼接分支
        expression, parameters = (
            compile_filters(filters) if filters is not None else ("true", {})
        )
        records = await self._read(
            f"""// graph:get_nodes
            MATCH (s:{self._source})-[fact:RAG_NODE_FACT]->(n:{self._entity})
            WHERE n.node_id IN $ids AND {expression}
            RETURN n.node_id AS id, s.chunk_id AS chunk_id, fact.payload AS payload
            """,
            {**parameters, "ids": list(dict.fromkeys(node_ids))},
        )
        # 保持请求顺序，再按 chunk_id 稳定排序
        positions = {
            node_id: index for index, node_id in enumerate(dict.fromkeys(node_ids))
        }
        records.sort(key=lambda record: (positions[record["id"]], record["chunk_id"]))
        nodes = []
        for record in records:
            node = _node_adapter.validate_json(
                record["payload"], strict=True, extra="forbid"
            )
            # payload 中的身份必须与拓扑一致
            if node.id != record["id"] or node.source_id != record["chunk_id"]:
                raise RuntimeError(
                    "Neo4j node fact identity does not match its topology"
                )
            nodes.append(node)
        return nodes

    async def get_relations(
        self, edge_ids: list[str], *, filters: MetadataFilters | None = None
    ) -> list[GraphEdge]:
        if not edge_ids:
            return []
        expression, parameters = (
            compile_filters(filters) if filters is not None else ("true", {})
        )
        records = await self._read(
            f"""// graph:get_relations
            MATCH (s:{self._source})-[fact:RAG_EDGE_FACT]->(r:{self._relation})
            WHERE r.edge_id IN $ids AND {expression}
            RETURN r.edge_id AS id, s.chunk_id AS chunk_id, fact.payload AS payload
            """,
            {**parameters, "ids": list(dict.fromkeys(edge_ids))},
        )
        positions = {
            edge_id: index for index, edge_id in enumerate(dict.fromkeys(edge_ids))
        }
        records.sort(key=lambda record: (positions[record["id"]], record["chunk_id"]))
        return self._decode_edges(records)

    async def get_neighbors(
        self,
        node_ids: list[str],
        *,
        direction: GraphDirection = GraphDirection.OUTGOING,
        relation_types: list[str] | None = None,
        filters: MetadataFilters | None = None,
        limit: int = 100,
    ) -> list[GraphEdge]:
        direction = GraphDirection(direction)
        if type(limit) is not int or limit <= 0:
            raise ValueError("neighbor limit must be a positive integer")
        expression, parameters = (
            compile_filters(filters) if filters is not None else ("true", {})
        )
        # 空节点或空关系类型直接短路
        if not node_ids or relation_types == []:
            return []

        # 根据方向选择端点匹配条件
        endpoint = {
            GraphDirection.OUTGOING: "source.node_id IN $ids",
            GraphDirection.INCOMING: "target.node_id IN $ids",
            GraphDirection.BOTH: "(source.node_id IN $ids OR target.node_id IN $ids)",
        }[direction]

        records = await self._read(
            f"""// graph:get_neighbors
            MATCH (r:{self._relation})-[:RAG_FROM]->(source:{self._entity}),
                (r)-[:RAG_TO]->(target:{self._entity}),
                (s:{self._source})-[:RAG_EDGE_FACT]->(r)
            WHERE {endpoint} AND ($relation_types IS NULL OR r.relation_type IN $relation_types)
            AND {expression}
            WITH DISTINCT r ORDER BY r.edge_id LIMIT $limit
            MATCH (s:{self._source})-[fact:RAG_EDGE_FACT]->(r)
            WHERE {expression}
            RETURN r.edge_id AS id, s.chunk_id AS chunk_id, fact.payload AS payload
            ORDER BY id, chunk_id
            """,
            {
                **parameters,
                "ids": list(dict.fromkeys(node_ids)),
                "relation_types": relation_types,
                "limit": limit,
            },
        )
        return self._decode_edges(records)

    @staticmethod
    def _decode_edges(records: list[dict]) -> list[GraphEdge]:
        edges = []
        for record in records:
            edge = _edge_adapter.validate_json(
                record["payload"], strict=True, extra="forbid"
            )
            # payload 中的身份必须与拓扑一致
            if edge.id != record["id"] or edge.source_ids != [record["chunk_id"]]:
                raise RuntimeError(
                    "Neo4j relationship fact identity does not match its topology"
                )
            edges.append(edge)
        return edges
