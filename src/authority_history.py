"""Validity intervals from explicitly observed successful authority actions."""

from itertools import combinations

from src.candidate_registry import valid_wallet

APPROVALS = {"approve_agent", "subaccount", "multisig_signer"}


def _snapshot_changes(events, as_of_ms):
    snapshots, output, previous = [], [], {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") != "agent_snapshot":
            output.append(event)
        elif (event.get("success") is True and isinstance(event.get("agents"), list)
              and isinstance(event.get("ts_ms"), int) and event["ts_ms"] <= as_of_ms):
            snapshots.append(event)
    for event in sorted(snapshots, key=lambda e: (e["ts_ms"], str(e.get("event_id")))):
        from src.agent_links import normalise_agents
        account = event.get("account")
        current = {a["address"]: a for a in normalise_agents(event["agents"])}
        prior = previous.get(account, {})
        output.extend({**event, "authority": authority, "kind": "approve_agent",
                           "event_id": f"{event.get('event_id')}:{authority}",
                           "valid_until_ms": current[authority].get("valid_until"),
                           "boundaries_observed_only": True} for authority in current.keys() - prior.keys())
        if event.get("complete"):
            output.extend({**event, "authority": authority, "kind": "revoke_agent",
                               "event_id": f"{event.get('event_id')}:{authority}",
                               "boundaries_observed_only": True} for authority in prior.keys() - current.keys())
            previous[account] = current
        else:
            previous[account] = {**prior, **current}
    return output


def index_authority(events, as_of_ms=None):
    as_of_ms = as_of_ms if as_of_ms is not None else 2**63 - 1
    valid, seen, rejected = [], set(), 0
    for event in _snapshot_changes(events, as_of_ms):
        try:
            if event.get("success") is not True or not event.get("event_id"):
                raise ValueError("unverified action")
            ts = int(event["ts_ms"])
            expiry = event.get("valid_until_ms")
            if expiry is not None:
                expiry = int(expiry)
            account, authority = valid_wallet(event["account"]), valid_wallet(event["authority"])
            if event["kind"] not in APPROVALS | {"revoke_agent", "revoke_multisig_signer"}:
                raise ValueError("unsupported action")
            if ts > as_of_ms or event["event_id"] in seen:
                continue
            seen.add(event["event_id"])
            valid.append({**event, "account": account, "authority": authority, "ts_ms": ts,
                          "valid_until_ms": expiry})
        except (KeyError, ValueError, TypeError):
            rejected += 1
    valid.sort(key=lambda e: (e["ts_ms"], str(e["event_id"])))
    intervals, active = [], {}
    for event in valid:
        kind = event["kind"]
        relation = "approve_agent" if "agent" in kind else "multisig_signer" if "multisig" in kind else "subaccount"
        key = (event["account"], relation, event.get("slot", event["authority"]))
        previous = active.pop(key, None)
        if previous:
            previous["valid_to_ms"] = min(previous.get("valid_to_ms") or event["ts_ms"], event["ts_ms"])
            previous["parent_event_ids"].append(event["event_id"])
        if kind.startswith("revoke"):
            continue
        expiry = event.get("valid_until_ms")
        row = {"account": event["account"], "authority": event["authority"], "relationship": relation,
               "boundaries_observed_only": bool(event.get("boundaries_observed_only")),
               "valid_from_ms": event["ts_ms"], "valid_to_ms": int(expiry) if expiry is not None else None,
               "parent_event_ids": [event["event_id"]], "confirms_owner": False}
        active[key] = row
        intervals.append(row)
    by_authority = {}
    for row in intervals:
        by_authority.setdefault(row["authority"], []).append(row)
    shared = []
    for authority, rows in by_authority.items():
        for left, right in combinations(rows, 2):
            start = max(left["valid_from_ms"], right["valid_from_ms"])
            end = min(left["valid_to_ms"] or as_of_ms, right["valid_to_ms"] or as_of_ms, as_of_ms)
            if left["account"] != right["account"] and start < end:
                shared.append({"authority": authority, "accounts": sorted([left["account"], right["account"]]),
                               "overlap_start_ms": start, "overlap_end_ms": end,
                               "assertion": "shared_operator", "confirms_owner": False,
                               "parent_event_ids": sorted(set(left["parent_event_ids"] + right["parent_event_ids"]))})
    return {"intervals": intervals, "shared_authority": shared, "rejected": rejected,
            "coverage": "only explicitly supplied successful actions; missing history remains unknown"}
