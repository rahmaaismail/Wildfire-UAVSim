"""What the case leaves out: which probe feeds which indicator, and which claims the fleet has to balance.

Two things a Socrates export cannot say, and the managing system cannot do without.

An indicator in the export has a 'path' field that looks as though it should say where its data comes from,
and in this case it is empty on every indicator, so the link from an indicator to the code that measures it
is made here instead, by the indicator's name. It is a file in the repository, not a field in Socrates,
which means it is reviewed with the code it binds to.

The top-level claims are named for the same reason the nodes are looked up by lid (see case.py): the
export's 'isRoot' flag is true of five nodes in this case, because it also marks the roots of sub-arguments
shared between claims. Inferring "the two claims being balanced" from it would pick the wrong ones, so the
file says which they are.

See specs/managing/balance.md, MGT-BAL-5 and MGT-BAL-7.
"""

# python libraries

import json
from dataclasses import dataclass

# own python modules

from .errors import CaseError


@dataclass(frozen=True)
class Resolution:
    """The outcome of binding a case's indicators to probes.

    'bound' maps an indicator's name to the name of its probe, and 'unbound' is the indicators nothing was
    bound to. Those are left unrated rather than guessed at, which the belief assessment counts as a gap in
    what the case knows.
    """

    bound: dict
    unbound: tuple


class Bindings:
    """The probe for each indicator, and the claims to balance."""

    def __init__(self, probes, top_level):
        self.probes = dict(probes)
        self.top_level_lids = dict(top_level)

    # --- reading -------------------------------------------------------------

    @classmethod
    def load(cls, path):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as error:
            raise CaseError(f"cannot read the bindings file {path}: {error}") from error
        except json.JSONDecodeError as error:
            raise CaseError(f"{path} is not valid JSON: {error}") from error
        return cls.parse(payload)

    @classmethod
    def parse(cls, payload):
        if not isinstance(payload, dict):
            raise CaseError("the bindings are not a JSON object")

        probes = payload.get("probes", {})
        top_level = payload.get("top_level", {})
        if not isinstance(probes, dict) or not all(isinstance(name, str) for name in probes.values()):
            raise CaseError("'probes' in the bindings must map an indicator's name to the name of a probe")
        if not isinstance(top_level, dict) or not all(isinstance(lid, int) for lid in top_level.values()):
            raise CaseError("'top_level' in the bindings must map a role to the lid of a claim")
        return cls(probes, top_level)

    # --- using them ----------------------------------------------------------

    # binds every indicator of the case to its probe, checking the bindings against both sides: the probes
    # that exist (given as names, so that this does not have to import them) and the indicators the case has.
    #
    # A binding to a probe that does not exist is an error here, at the time the system is built, because
    # the alternative is finding out on the step the indicator is first needed. So is a binding for an
    # indicator the case does not have: it is almost always a misspelt name, and the real indicator would
    # then be reported as unbound with nothing to say why. An indicator with no binding is not an error --
    # a case may carry indicators this managing system has no way to measure -- and is reported once, so a
    # run does not repeat it at every step.
    def resolve(self, case, probe_names, log=None):
        available = set(probe_names)
        names = {indicator.name for indicator in case.indicators}

        for indicator_name, probe in self.probes.items():
            if probe not in available:
                raise CaseError(f"the indicator {indicator_name!r} is bound to the probe {probe!r}, which "
                                f"does not exist; the probes are {', '.join(sorted(available)) or 'none'}")
            if indicator_name not in names:
                raise CaseError(f"a probe is bound to the indicator {indicator_name!r}, which the case "
                                f"does not have")

        unbound = tuple(indicator.name for indicator in case.indicators if indicator.name not in self.probes)
        if log is not None:
            for name in unbound:
                log.warning("the indicator %r has no probe bound to it and will stay unrated", name)

        return Resolution(bound=dict(self.probes), unbound=unbound)

    # the claims to balance, by role, as nodes of the case: {"safety": Node, "performance": Node}
    def top_level(self, case):
        claims = {}
        for role, lid in self.top_level_lids.items():
            if lid not in case.nodes:
                raise CaseError(f"the {role} claim is bound to node {lid}, which is not in the case")
            claims[role] = case.nodes[lid]
        return claims
