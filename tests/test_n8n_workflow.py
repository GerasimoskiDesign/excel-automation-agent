"""Structural checks of the importable n8n workflow (Docker is not needed for these)."""

import json
import re
from collections import deque

import pytest

from tests.conftest import ROOT

WORKFLOW = ROOT / "integrations" / "n8n" / "excel_agent_workflow.json"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def test_nodes_are_well_formed(workflow):
    nodes = workflow["nodes"]
    names = [n["name"] for n in nodes]
    ids = [n["id"] for n in nodes]
    assert len(set(names)) == len(names)
    assert len(set(ids)) == len(ids)
    for node in nodes:
        assert node["type"].startswith("n8n-nodes-base.")
        assert isinstance(node["typeVersion"], int | float)
        assert len(node["position"]) == 2


def test_connections_reference_existing_nodes(workflow):
    names = {n["name"] for n in workflow["nodes"]}
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names
                assert target["type"] == "main"


def test_every_node_is_reachable_from_the_email_trigger(workflow):
    triggers = [n["name"] for n in workflow["nodes"] if n["type"].endswith("emailReadImap")]
    assert len(triggers) == 1
    seen, queue = set(triggers), deque(triggers)
    while queue:
        for branch in workflow["connections"].get(queue.popleft(), {}).get("main", []):
            for target in branch:
                if target["node"] not in seen:
                    seen.add(target["node"])
                    queue.append(target["node"])
    assert seen == {n["name"] for n in workflow["nodes"]}


def test_flow_calls_the_api_contract(workflow):
    by_name = {n["name"]: n for n in workflow["nodes"]}
    assert by_name["Create job"]["parameters"]["url"].endswith("/jobs/json")
    assert by_name["Create job"]["parameters"]["method"] == "POST"
    assert "/jobs/{{" in by_name["Get job status"]["parameters"]["url"]
    assert by_name["Download tool zip"]["parameters"]["url"].endswith("/download")
    email = by_name["Email the tool back"]["parameters"]
    assert email["options"]["attachments"] == "deliverable"
    assert "assumptions" in email["text"]
    # The polling loop goes back to Wait when the job is neither done nor failed.
    failed_branches = workflow["connections"]["Failed?"]["main"]
    assert failed_branches[1][0]["node"] == "Wait"


def test_expression_node_references_exist(workflow):
    names = {n["name"] for n in workflow["nodes"]}
    text = json.dumps(workflow)
    for ref in re.findall(r"\$\('([^']+)'\)", text):
        assert ref in names, ref


def test_no_real_credentials_in_export(workflow):
    for node in workflow["nodes"]:
        for cred in node.get("credentials", {}).values():
            assert cred["id"] == "replace-me"
