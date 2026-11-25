"""The case can be fetched from a web address, and a bad address costs the run its adaptation quality, not the run.

The point of the address is that the case is edited in Socrates and exported, and a managing system that can
only read a file somebody remembered to copy into the repository is reading whatever was copied last. The
tests serve the real export from a server on the local machine, so they exercise the real fetching and
parsing and need no network, and they check the three things that go wrong with an address: it serves nothing,
it serves something that is not a case, and it is not a web address at all.
"""

# python libraries

import http.server
import json
import pathlib
import threading

import pytest

# see the note in sim/policy/random_policy.py about importing config as a module
import config

# own python modules

from sim.managing.analyze.balance import BalanceAnalyzer
from sim.managing.assurance import CaseError, JsonCaseSource, UrlCaseSource

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_CASE = REPO_ROOT / "assurance" / "scout_case.json"


@pytest.fixture
def server():
    """A web server on this machine serving what the test sets in `served`, and noting the requests it got."""
    served = {"status": 200, "body": REAL_CASE.read_bytes(), "seen": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            served["seen"].append({"path": self.path, "authorization": self.headers.get("Authorization")})
            self.send_response(served["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(served["body"])

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    served["url"] = f"http://127.0.0.1:{httpd.server_address[1]}/case.json"
    yield served
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.verifies("MGT-BAL-19")
def test_the_case_served_at_an_address_is_the_case_that_the_file_holds(server):
    fetched = UrlCaseSource(server["url"]).load()
    from_file = JsonCaseSource(REAL_CASE).load()
    assert fetched.nodes == from_file.nodes and fetched.indicators == from_file.indicators
    assert fetched.source == server["url"]


@pytest.mark.verifies("MGT-BAL-19")
def test_an_edited_case_is_what_the_next_load_reads(server):
    payload = json.loads(REAL_CASE.read_bytes())
    payload["name"] = "edited since"
    server["body"] = json.dumps(payload).encode()
    assert UrlCaseSource(server["url"]).load().name == "edited since"


@pytest.mark.verifies("MGT-BAL-19")
@pytest.mark.parametrize("status", [404, 500])
def test_an_address_that_answers_with_an_error_is_refused(server, status):
    server["status"], server["body"] = status, b"{}"
    with pytest.raises(CaseError, match="cannot fetch"):
        UrlCaseSource(server["url"]).load()


@pytest.mark.verifies("MGT-BAL-19")
def test_something_that_is_not_json_or_not_a_case_is_refused(server):
    server["body"] = b"<html>sign in</html>"
    with pytest.raises(CaseError, match="not valid JSON"):
        UrlCaseSource(server["url"]).load()
    server["body"] = b'{"schema": {"version": 99}}'
    with pytest.raises(CaseError, match="schema version"):
        UrlCaseSource(server["url"]).load()


@pytest.mark.verifies("MGT-BAL-19")
def test_an_address_nobody_answers_at_is_refused_and_does_not_hang():
    with pytest.raises(CaseError, match="cannot fetch"):
        UrlCaseSource("http://127.0.0.1:9/case.json", timeout=2).load()


@pytest.mark.verifies("MGT-BAL-19")
@pytest.mark.parametrize("address", ["file:///etc/passwd", "ftp://example.com/case.json", "/etc/passwd", ""])
def test_only_web_addresses_are_accepted(address):
    with pytest.raises(CaseError, match="not an http or https address"):
        UrlCaseSource(address).load()


@pytest.mark.verifies("MGT-BAL-19")
def test_a_token_is_sent_as_a_bearer_token_and_only_when_there_is_one(server):
    UrlCaseSource(server["url"]).load()
    UrlCaseSource(server["url"], token="secret").load()
    assert [request["authorization"] for request in server["seen"]] == [None, "Bearer secret"]


@pytest.mark.verifies("MGT-BAL-19")
def test_the_token_is_not_part_of_how_the_source_prints():
    assert "secret" not in str(UrlCaseSource("http://example.invalid/case.json", token="secret"))
    assert "secret" not in repr(UrlCaseSource("http://example.invalid/case.json", token="secret"))


@pytest.mark.verifies("MGT-BAL-19")
def test_the_analyser_reads_the_address_in_the_config_and_not_the_file(server, monkeypatch):
    monkeypatch.setattr(config, "BALANCE_CASE_URL", server["url"])
    monkeypatch.setattr(config, "BALANCE_CASE_FILE", "assurance/does_not_exist.json")
    analyzer = BalanceAnalyzer()
    assert analyzer.failure is None and analyzer.case.source == server["url"]
    assert len(server["seen"]) == 1


@pytest.mark.verifies("MGT-BAL-19")
def test_the_token_comes_from_the_environment(server, monkeypatch):
    monkeypatch.setattr(config, "BALANCE_CASE_URL", server["url"])
    monkeypatch.setenv("BALANCE_CASE_TOKEN", "from-the-environment")
    BalanceAnalyzer()
    assert server["seen"][0]["authorization"] == "Bearer from-the-environment"


@pytest.mark.verifies("MGT-BAL-17")
def test_an_unreachable_address_costs_the_adaptation_quality_and_not_the_run(server, monkeypatch):
    server["status"] = 500
    monkeypatch.setattr(config, "BALANCE_CASE_URL", server["url"])
    analyzer = BalanceAnalyzer()
    assert analyzer.case is None and "cannot fetch" in analyzer.failure
