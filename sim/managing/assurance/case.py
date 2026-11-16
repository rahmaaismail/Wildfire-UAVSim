"""The assurance case, and where it is read from.

The BALANCE method needs the argument itself at run time: which claims there are, what supports each of them,
and which measurements are attached where. Socrates is where the argument is written, and it can export it
as JSON, so that is what a managing system reads. The source is an interface (CaseSource) because the file
is the least that works, not the only way there is: a source that asks a live Socrates for the case would
be a second implementation of the same two lines, and nothing else would have to know.

The Socrates Python API is not used here and must not be. It is licensed to the people who need it for
their projects and is not to be published, and this repository is public. The export is plain JSON and
carries none of that code; see MGT-BAL-16.

The export is a flat list of nodes that name each other, and it names them by two different numbers. Every
node has an 'id', which is Socrates's internal key, and a 'lid', which is the number shown next to the node
in the tree (the 98 of C0098). A node's 'parents' are ids and its 'children' are lids, and an indicator's
'nodes' are lids. Reading one of them as the other links every node to the wrong neighbour and raises no
error at all, so the loader resolves both to lids and then checks that the two directions agree: if a node
names a parent, that parent has to name the node as a child.

See specs/managing/balance.md, MGT-BAL-1, MGT-BAL-2 and MGT-BAL-16.
"""

# python libraries

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# own python modules

from .errors import CaseError
from .indicators import COMBINATORS, Category, Condition, DataPoint, Indicator, Rule

# the export schema this reader was written against. Another version may have renamed what this reads, and
# a case read wrongly would make the managing system confident for no reason, so another version is refused
# rather than read as far as it happens to parse.
SUPPORTED_SCHEMA_VERSIONS = (14,)


# --- the case ----------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    """One node of the argument.

    'children' and 'parents' are both lids, resolved when the case was read. 'is_root' is the flag Socrates
    exports, and it cannot be used to find the claims a case is about: it is true of every node that has no
    parent in the argument it was drawn in, which includes the roots of sub-arguments that are shared, so
    the current case has five of them. The top-level claims are named in the bindings instead (see
    bindings.py).
    """

    lid: int
    id: int
    node_type: str
    text: str
    children: tuple = ()
    parents: tuple = ()
    is_root: bool = False
    archived: bool = False

    def __post_init__(self):
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "parents", tuple(self.parents))

    # a claim is what the argument is about; the other types are the argument's machinery
    def is_claim(self):
        return self.node_type == "CLAIM"


@dataclass(frozen=True)
class Case:
    """The whole argument: its nodes, its indicators, and the categories the indicators are rated in."""

    name: str
    nodes: dict
    indicators: tuple
    categories: dict
    schema_version: int = 0
    # which file it came from, for the messages that have to say so
    source: str = field(default="", compare=False)

    def __post_init__(self):
        object.__setattr__(self, "nodes", dict(self.nodes))
        object.__setattr__(self, "indicators", tuple(self.indicators))
        object.__setattr__(self, "categories", dict(self.categories))

    # one node, by the lid Socrates shows for it
    def node(self, lid):
        try:
            return self.nodes[lid]
        except KeyError:
            raise CaseError(f"the case has no node {lid}") from None

    def children(self, lid):
        return tuple(self.node(child) for child in self.node(lid).children)

    def parents(self, lid):
        return tuple(self.node(parent) for parent in self.node(lid).parents)

    # every indicator attached to a node, which is what the belief assessment asks for node by node
    def indicators_on(self, lid):
        return tuple(indicator for indicator in self.indicators if lid in indicator.nodes)

    def indicator(self, name):
        for indicator in self.indicators:
            if indicator.name == name:
                return indicator
        raise CaseError(f"the case has no indicator named {name!r}")


# --- where it comes from -----------------------------------------------------


class CaseSource(ABC):
    """Somewhere an assurance case can be read from."""

    @abstractmethod
    def load(self):
        """Return the Case, or raise CaseError saying why it cannot be trusted."""


def _need(mapping, key, where):
    if not isinstance(mapping, dict) or key not in mapping:
        raise CaseError(f"{where} has no {key!r}")
    return mapping[key]


class JsonCaseSource(CaseSource):
    """Reads a case from a Socrates JSON export on disk.

    parse() does the work on a dictionary and load() only fetches the file, so that every rule about what
    makes a case acceptable can be tried against a few lines of JSON without a file to put them in.
    """

    def __init__(self, path):
        self.path = str(path)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as error:
            raise CaseError(f"cannot read the case file {self.path}: {error}") from error
        except json.JSONDecodeError as error:
            raise CaseError(f"{self.path} is not valid JSON: {error}") from error
        case = self.parse(payload)
        return Case(name=case.name, nodes=case.nodes, indicators=case.indicators,
                    categories=case.categories, schema_version=case.schema_version, source=self.path)

    @classmethod
    def parse(cls, payload):
        schema = _need(_need(payload, "schema", "the export"), "version", "the schema")
        if schema not in SUPPORTED_SCHEMA_VERSIONS:
            raise CaseError(f"the export is Socrates schema version {schema}; this reader was written for "
                            f"{', '.join(str(version) for version in SUPPORTED_SCHEMA_VERSIONS)}")

        categories = cls.read_categories(_need(payload, "indicatorRuleCategories", "the export"))
        nodes = cls.read_nodes(_need(payload, "nodes", "the export"))
        indicators = cls.read_indicators(_need(payload, "indicators", "the export"), nodes, categories)

        return Case(name=str(payload.get("name", "")), nodes=nodes, indicators=indicators,
                    categories=categories, schema_version=schema)

    # --- the parts -----------------------------------------------------------

    @staticmethod
    def read_categories(entries):
        categories = {}
        for entry in entries:
            category = Category(id=int(_need(entry, "id", "a rule category")),
                                name=str(_need(entry, "name", "a rule category")),
                                priority=int(_need(entry, "priority", "a rule category")),
                                color=str(entry.get("color") or ""))
            categories[category.id] = category
        return categories

    @staticmethod
    def read_nodes(entries):
        by_id = {}
        for entry in entries:
            lid, node_id = int(_need(entry, "lid", "a node")), int(_need(entry, "id", "a node"))
            if lid in by_id.values() or node_id in by_id:
                raise CaseError(f"two nodes share the lid {lid} or the id {node_id}")
            by_id[node_id] = lid

        known = set(by_id.values())
        nodes = {}
        for entry in entries:
            lid = int(entry["lid"])

            children = []
            for child in entry.get("children") or []:
                if int(child) not in known:
                    raise CaseError(f"node {lid} has the child {child}, which is not a node in the case "
                                    f"(children are lids)")
                children.append(int(child))

            parents = []
            for parent in entry.get("parents") or []:
                if int(parent) not in by_id:
                    raise CaseError(f"node {lid} has the parent {parent}, which is not a node in the case "
                                    f"(parents are ids)")
                parents.append(by_id[int(parent)])

            nodes[lid] = Node(lid=lid, id=int(entry["id"]), node_type=str(entry.get("nodeType", "")),
                              text=str(entry.get("text") or ""), children=children, parents=parents,
                              is_root=bool(entry.get("isRoot", False)),
                              archived=bool(entry.get("archived", False)))

        # the two directions have to agree. A reader that took 'parents' for lids would have got every one
        # of these wrong and raised nothing, which is the failure this check exists to make loud.
        for node in nodes.values():
            for parent in node.parents:
                if node.lid not in nodes[parent].children:
                    raise CaseError(f"node {node.lid} names node {parent} as a parent, but node {parent} "
                                    f"does not list it as a child")
            for child in node.children:
                if node.lid not in nodes[child].parents:
                    raise CaseError(f"node {node.lid} lists node {child} as a child, but node {child} "
                                    f"does not name it as a parent")
        return nodes

    @staticmethod
    def read_indicators(entries, nodes, categories):
        indicators = []
        for entry in entries:
            name = str(_need(entry, "name", "an indicator"))

            attached = [int(lid) for lid in _need(entry, "nodes", f"the indicator {name!r}")]
            for lid in attached:
                if lid not in nodes:
                    raise CaseError(f"the indicator {name!r} is attached to node {lid}, which is not a "
                                    f"node in the case (an indicator's nodes are lids)")

            rules = []
            for rule in _need(entry, "rules", f"the indicator {name!r}"):
                category_id = int(_need(rule, "category", f"a rule of {name!r}"))
                if category_id not in categories:
                    raise CaseError(f"a rule of the indicator {name!r} names the category {category_id}, "
                                    f"which the case does not define")
                combinator = str(rule.get("combinator", "OR"))
                if combinator not in COMBINATORS:
                    raise CaseError(f"a rule of the indicator {name!r} has the combinator {combinator!r}, "
                                    f"expected one of {', '.join(COMBINATORS)}")
                conditions = [Condition(comparator=str(_need(condition, "comparator", "a condition")),
                                        value=float(_need(condition, "value", "a condition")))
                              for condition in rule.get("conditions") or []]
                rules.append(Rule(name=str(rule.get("name") or ""), category=categories[category_id],
                                  conditions=conditions, combinator=combinator))

            data = [DataPoint(created=str(_need(point, "created", "a data point")),
                              value=float(_need(point, "value", "a data point")))
                    for point in entry.get("indicatorData") or []]

            indicators.append(Indicator(id=int(entry.get("id", 0)), name=name, nodes=attached,
                                        rules=rules, data=data,
                                        description=str(entry.get("description") or ""),
                                        path=entry.get("path"), archived=bool(entry.get("archived", False)),
                                        creator=str(entry.get("creator") or "")))
        return tuple(indicators)
