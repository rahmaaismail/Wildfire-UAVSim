"""The one way the assurance case can be refused."""


class CaseError(Exception):
    """The assurance case, or something that goes with it, cannot be trusted and has been refused.

    One exception for every way a case can be wrong -- a file that is not JSON, a schema this reader was not
    written for, a link to a node that is not there, a rule in a category nobody defined, a binding to a probe
    that does not exist -- because the response is the same for all of them: the managing system does not use
    the case. It is raised when the case is loaded, never in the middle of a run, and what the loop does
    instead is MGT-BAL-17. It lives in a module of its own so that every part of the package can raise it
    without any of them having to import another to do so.
    """
