"""驱动替身验证事务与参数合同；真实 Cypher 语义另由隔离集成测试验证。"""

import asyncio
import copy
import re

import pytest


class MemoryResult:
    def __init__(self, records, driver, operation):
        self.records = records
        self.driver = driver
        self.operation = operation
        self.consumed = False

    async def single(self, *, strict):
        assert strict and len(self.records) == 1
        return self.records[0]

    async def data(self):
        return copy.deepcopy(self.records)

    async def consume(self):
        self.consumed = True
        if self.driver.consume_failure == self.operation:
            raise OSError("result consumption failed")


def matches(expression, parameters, properties):
    # 只解释 compiler 产生的布尔表达式，参数值从不拼入可执行文本。
    def leaf(match):
        index, operator = match.groups()
        key = parameters[f"key_{index}"]
        expected = parameters[f"value_{index}"]
        actual = properties.get(key)
        if actual is None:
            return "False"
        if operator == "IN":
            return str(actual in expected)
        return str(
            {
                "=": lambda: actual == expected,
                ">": lambda: actual > expected,
                ">=": lambda: actual >= expected,
                "<": lambda: actual < expected,
                "<=": lambda: actual <= expected,
            }[operator]()
        )

    expression = re.sub(
        r"coalesce\(s\[\$key_(\d+)\] (=|IN|>=|<=|>|<) \$value_\d+, false\)",
        leaf,
        expression,
    )
    expression = (
        expression.replace("true", "True")
        .replace("AND", "and")
        .replace("OR", "or")
        .replace("NOT", "not")
    )
    assert re.fullmatch(r"[() TrueFalsandort]+", expression)
    return eval(expression, {"__builtins__": {}}, {})


class MemoryDriver:
    def __init__(self):
        self.state = {"sources": {}, "nodes": set(), "edges": {}}
        self.calls = []
        self.results = []
        self.commits = 0
        self.rollbacks = 0
        self.transactions = 0
        self.fail_transaction = None
        self.fail_operation = None
        self.consume_failure = None
        self.bad_count = None
        self.commit_failure = False
        self.block_operation = None
        self.started = asyncio.Event()

    def session(self, *, database):
        assert database == "neo4j"
        return MemorySession(self)

    async def run(self, query, parameters, state):
        operation = query.splitlines()[0].removeprefix("// graph:")
        self.calls.append((operation, query, copy.deepcopy(parameters)))
        if (
            self.fail_operation == operation
            and self.transactions == self.fail_transaction
        ):
            raise OSError("transaction write failed")
        if self.block_operation == operation:
            self.started.set()
            await asyncio.Event().wait()
        records = []
        if operation == "remove_sources":
            if "chunk_ids" in parameters:
                selected = [
                    key for key in parameters["chunk_ids"] if key in state["sources"]
                ]
            else:
                expression = query.split(" WHERE ", 1)[1].splitlines()[0]
                selected = [
                    key
                    for key, source in state["sources"].items()
                    if matches(expression, parameters, source["properties"])
                ]
            nodes, edges = set(), set()
            for key in selected:
                source = state["sources"].pop(key)
                nodes.update(source["nodes"])
                edges.update(source["edges"])
            records = [{"node_ids": sorted(nodes), "edge_ids": sorted(edges)}]
        elif operation == "upsert_sources":
            for row in parameters["rows"]:
                state["sources"][row["chunk_id"]] = {
                    "properties": row["properties"],
                    "nodes": {},
                    "edges": {},
                }
        elif operation == "upsert_nodes":
            written = 0
            for row in parameters["rows"]:
                for node in row["nodes"]:
                    state["nodes"].add(node["node_id"])
                    state["sources"][row["chunk_id"]]["nodes"][node["node_id"]] = node[
                        "payload"
                    ]
                    written += 1
            records = [{"written": written if self.bad_count != operation else 0}]
        elif operation == "upsert_edges":
            written = 0
            for row in parameters["rows"]:
                for edge in row["edges"]:
                    if (
                        edge["source_id"] in state["nodes"]
                        and edge["target_id"] in state["nodes"]
                    ):
                        state["edges"][edge["edge_id"]] = (
                            edge["source_id"],
                            edge["target_id"],
                            edge["relation_type"],
                        )
                        state["sources"][row["chunk_id"]]["edges"][edge["edge_id"]] = (
                            edge["payload"]
                        )
                        written += 1
            records = [{"written": written if self.bad_count != operation else 0}]
        elif operation == "cleanup_relations":
            for edge_id in parameters["edge_ids"]:
                if not any(
                    edge_id in source["edges"] for source in state["sources"].values()
                ):
                    state["edges"].pop(edge_id, None)
        elif operation == "cleanup_nodes":
            for node_id in parameters["node_ids"]:
                if not any(
                    node_id in source["nodes"] for source in state["sources"].values()
                ) and not any(node_id in edge[:2] for edge in state["edges"].values()):
                    state["nodes"].discard(node_id)
        elif operation in ("get_nodes", "get_relations", "get_neighbors"):
            if operation == "get_neighbors":
                # 查询方向取自实际 WHERE，契约测试再核对方向语义。
                seed = set(parameters["ids"])
                candidates = []
                for edge_id, (source, target, relation_type) in state["edges"].items():
                    outgoing = "source.node_id IN $ids" in query and source in seed
                    incoming = "target.node_id IN $ids" in query and target in seed
                    if (outgoing or incoming) and (
                        parameters["relation_types"] is None
                        or relation_type in parameters["relation_types"]
                    ):
                        candidates.append(edge_id)
                field = "edges"
            else:
                candidates = parameters["ids"]
                field = "nodes" if operation == "get_nodes" else "edges"
            expression = (
                query.rsplit("WHERE ", 1)[1].splitlines()[0]
                if operation == "get_neighbors"
                else query.split(" AND ", 1)[1].splitlines()[0]
            )
            for chunk_id, source in state["sources"].items():
                if matches(expression, parameters, source["properties"]):
                    records.extend(
                        {"id": fact_id, "chunk_id": chunk_id, "payload": payload}
                        for fact_id, payload in source[field].items()
                        if fact_id in candidates
                    )
            if operation == "get_neighbors":
                allowed = sorted({record["id"] for record in records})[
                    : parameters["limit"]
                ]
                records = sorted(
                    [record for record in records if record["id"] in allowed],
                    key=lambda record: (record["id"], record["chunk_id"]),
                )
        result = MemoryResult(records, self, operation)
        self.results.append(result)
        return result


class MemorySession:
    def __init__(self, driver):
        self.driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def begin_transaction(self):
        self.driver.transactions += 1
        return MemoryTransaction(self.driver)

    async def run(self, query, **parameters):
        return await self.driver.run(query, parameters, self.driver.state)


class MemoryTransaction:
    def __init__(self, driver):
        self.driver = driver
        self.state = copy.deepcopy(driver.state)
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.driver.rollbacks += 1
        return False

    async def run(self, query, **parameters):
        return await self.driver.run(query, parameters, self.state)

    async def commit(self):
        if self.driver.commit_failure:
            raise OSError("commit failed")
        self.driver.state = self.state
        self.driver.commits += 1
        self.committed = True


@pytest.fixture
def driver():
    return MemoryDriver()


@pytest.fixture
def filter_matches():
    return matches
