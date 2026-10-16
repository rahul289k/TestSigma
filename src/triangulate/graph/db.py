"""Neo4j access: schema, scoped wipe, batched writes and read helpers.

Only Triangulate's own labels are ever deleted, so the database can be shared safely.
"""

from __future__ import annotations

from typing import Any, Iterable

from neo4j import Driver, GraphDatabase

from ..config import Secrets

LABELS = ["CrawlRun", "Feature", "Section", "Requirement", "Gap", "Page", "State", "Element", "Flow",
          "Component", "File", "Symbol", "I18nKey", "Route", "PullRequest", "ReviewItem"]

KEYS = {"CrawlRun": "id", "Feature": "id", "Section": "id", "Requirement": "id", "Gap": "id", "Page": "route",
        "State": "fp", "Element": "id", "Flow": "id", "Component": "id", "File": "path", "Symbol": "id",
        "I18nKey": "key", "Route": "path", "PullRequest": "number", "ReviewItem": "id"}

FULLTEXT = {
    "elementText": ("Element", ["search_text"]),
    "keyText": ("I18nKey", ["text"]),
    "componentText": ("Component", ["search_name"]),
    "requirementText": ("Requirement", ["statement", "quote"]),
}


class Graph:
    def __init__(self, secrets: Secrets, database: str | None = None):
        self.database = database or secrets.neo4j_database
        self.driver: Driver = GraphDatabase.driver(secrets.neo4j_uri, auth=(secrets.neo4j_user, secrets.neo4j_password))
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params, database_=self.database)
        return [r.data() for r in records]

    def ensure_schema(self) -> None:
        for label, key in KEYS.items():
            self.run(f"CREATE CONSTRAINT {label.lower()}_{key} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")
        for name, (label, props) in FULLTEXT.items():
            fields = ", ".join(f"n.{p}" for p in props)
            self.run(f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) ON EACH [{fields}]")
        self.run("CALL db.awaitIndexes(120)")

    def wipe(self) -> None:
        with self.driver.session(database=self.database) as s:
            s.run("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
                  "CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS", labels=LABELS).consume()

    def merge_nodes(self, label: str, rows: list[dict], batch: int = 2000) -> None:
        key = KEYS[label]
        for i in range(0, len(rows), batch):
            self.run(f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.{key}}}) SET n += row", rows=rows[i:i + batch])

    def merge_rels(self, rel: str, a_label: str, b_label: str, rows: Iterable[dict], batch: int = 2000,
                   rel_key: str | None = None) -> None:
        """rows: {"a": key of start node, "b": key of end node, "props": {...}}"""
        ak, bk = KEYS[a_label], KEYS[b_label]
        rows = list(rows)
        merge = f"MERGE (a)-[r:{rel} {{{rel_key}: row.props.{rel_key}}}]->(b)" if rel_key else f"MERGE (a)-[r:{rel}]->(b)"
        for i in range(0, len(rows), batch):
            self.run(f"UNWIND $rows AS row MATCH (a:{a_label} {{{ak}: row.a}}) MATCH (b:{b_label} {{{bk}: row.b}}) "
                     f"{merge} SET r += row.props", rows=rows[i:i + batch])

    def fulltext(self, index: str, query: str, limit: int) -> list[dict]:
        return self.run("CALL db.index.fulltext.queryNodes($index, $q, {limit: $limit}) YIELD node, score "
                        "RETURN node, score", index=index, q=query, limit=limit)


def lucene_query(terms: list[str]) -> str:
    """OR-query of escaped terms; multi-word terms become phrases."""
    special = '+-&|!(){}[]^"~*?:\\/'
    out = []
    for t in terms:
        t = "".join("\\" + c if c in special else c for c in t.strip())
        if not t:
            continue
        out.append(f'"{t}"' if " " in t else t)
    return " OR ".join(out)
