import pytest

from mises.onshape.adapter import DocRef, parse_url
from mises.types import ErrorCode, MisesError

D, W, E = "a" * 24, "b" * 24, "c" * 24


def test_parse_url():
    r = parse_url(f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}")
    assert r == DocRef(did=D, wvm="w", wvmid=W, eid=E)
    r = parse_url(f"https://acme.onshape.com/documents/{D}/v/{W}/e/{E}?renderMode=0#x")
    assert (r.wvm, r.eid) == ("v", E)
    assert parse_url(f"https://cad.onshape.com/documents/{D}/m/{W}").eid is None
    for bad in ["https://evil.com/documents/{D}/w/{W}/e/{E}",
                f"https://cad.onshape.com/documents/{D}/x/{W}/e/{E}",
                f"http://cad.onshape.com/documents/{D}/w/{W}/e/{E}",
                f"https://cad.onshape.com.evil.com/documents/{D}/w/{W}/e/{E}",
                "not a url"]:
        with pytest.raises(MisesError) as e:
            parse_url(bad)
        assert e.value.code == ErrorCode.INVALID_INPUT


async def test_microversion(fake, adapter, run):
    ref = parse_url(fake.url())
    assert await adapter.microversion(ref, run.id) == fake.mv()
    m = DocRef(did=fake.did, wvm="m", wvmid="f" * 24, eid=fake.eid)
    assert await adapter.microversion(m, run.id) == "f" * 24
    assert fake.count("currentmicroversion") == 1


async def test_features_cached_by_microversion(fake, adapter, run):
    ref = parse_url(fake.url())
    a = await adapter.features(ref, run.id)
    b = await adapter.features(ref, run.id)
    assert a == b and a["features"][0]["name"] == "Base plate"
    assert fake.count(r"/features$") == 1
    fake.bump()
    await adapter.features(ref, run.id)
    assert fake.count(r"/features$") == 2


async def test_variables_cached_by_microversion(fake, adapter, run):
    ref = parse_url(fake.url())
    v = await adapter.variables(ref, run.id)
    assert v == fake.workspaces[fake.main]["variables"]
    await adapter.variables(ref, run.id)
    assert fake.count(r"/variables$") == 1


async def test_branch_created_from_ref_and_reused(fake, adapter, run):
    ref = parse_url(fake.url())
    b1 = await adapter.ensure_branch(ref, "designer", run.id)
    assert b1.wvm == "w" and b1.wvmid != fake.main and b1.eid == fake.eid
    assert fake.workspaces[b1.wvmid]["name"] == "agent/designer"
    b2 = await adapter.ensure_branch(ref, "designer", run.id)
    assert b2 == b1
    assert sum(1 for m, p in fake.requests if m == "POST" and p.endswith("/workspaces")) == 1
    assert len([w for w in fake.workspaces.values() if w["name"] == "agent/designer"]) == 1


async def test_branch_reused(fake, adapter, run):
    from .fake_onshape import new_id
    wid = new_id()
    fake._add_ws(wid, "agent/designer", features=[], variables=[])
    b = await adapter.ensure_branch(parse_url(fake.url()), "designer", run.id)
    assert b.wvmid == wid
    assert not any(m == "POST" for m, _ in fake.requests)


async def _branch(fake, adapter, run):
    return await adapter.ensure_branch(parse_url(fake.url()), "designer", run.id)


async def test_propose_updates_and_reports_changes(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    src = fake.mv(b.wvmid)
    out = await adapter.propose(b, run.id, source_microversion=src,
                                variables=None,
                                feature_updates=[{"featureId": "FaaA", "parameters": [
                                    {"btType": "BTMParameterQuantity-147",
                                     "parameterId": "depth", "expression": "20 mm"}]}])
    upd = next(bd for (m, p), bd in zip(fake.requests, fake.bodies) if p.endswith("/features/updates"))
    assert upd["sourceMicroversion"] == src and upd["rejectMicroversionSkew"] is True
    assert upd["btType"] == "BTUpdateFeaturesCall-1748"
    assert fake.count(r"/features/updates$") == 1          # one batched call
    ws = fake.workspaces[b.wvmid]
    assert ws["features"][0]["parameters"][0]["expression"] == "20 mm"
    assert fake.workspaces[fake.main]["features"][0]["parameters"][0]["expression"] == "10 mm"
    assert out["microversion"] == fake.mv(b.wvmid)
    assert set(out["changes"]) >= {"mass", "volume", "bbox"}
    assert out["changes"]["bbox"]["highX"] == 0.1


async def test_propose_stale_version(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    src = fake.mv(b.wvmid)
    fake.bump(b.wvmid)                       # someone else edited the branch
    with pytest.raises(MisesError) as e:
        await adapter.propose(b, run.id, source_microversion=src, variables=None,
                              feature_updates=[{"featureId": "FaaA", "parameters": []}])
    assert e.value.code == ErrorCode.STALE_VERSION and "re-read" in e.value.hint


async def test_propose_variables_only_checks_microversion(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    src = fake.mv(b.wvmid)
    fake.bump(b.wvmid)
    with pytest.raises(MisesError) as e:
        await adapter.propose(b, run.id, source_microversion=src,
                              variables={"rib_height": "1 mm"}, feature_updates=None)
    assert e.value.code == ErrorCode.STALE_VERSION
    assert not any(m == "POST" and p.endswith("/variables") for m, p in fake.requests)


@pytest.mark.parametrize("variables,updates", [
    ({"Rib 2": "3 mm"}, None),
    (None, [{"featureId": "FaaA", "name": "Rib 2", "parameters": []}]),
    ({"bad name!": "3 mm"}, None),
])
async def test_propose_rejects_numbered_name(fake, adapter, run, variables, updates):
    b = await _branch(fake, adapter, run)
    before = len(fake.requests)
    with pytest.raises(MisesError) as e:
        await adapter.propose(b, run.id, source_microversion=fake.mv(b.wvmid),
                              variables=variables, feature_updates=updates)
    assert e.value.code == ErrorCode.INVALID_INPUT
    assert len(fake.requests) == before        # zero requests sent


async def test_propose_refuses_non_workspace(fake, adapter, run):
    v = DocRef(did=fake.did, wvm="v", wvmid=fake.main, eid=fake.eid)
    with pytest.raises(MisesError) as e:
        await adapter.propose(v, run.id, source_microversion="x", variables=None,
                              feature_updates=[{"featureId": "FaaA"}])
    assert e.value.code == ErrorCode.INVALID_INPUT and fake.requests == []


async def test_merge_preview_and_merge(fake, adapter, run):
    target = parse_url(fake.url())
    b = await _branch(fake, adapter, run)
    await adapter.propose(b, run.id, source_microversion=fake.mv(b.wvmid), variables=None,
                          feature_updates=[{"featureId": "FaaA", "name": "Thick plate"}])
    p = await adapter.merge_preview(b, target, run.id)
    assert p["sourceMicroversionId"] == fake.mv(b.wvmid)
    m = await adapter.merge(b, target, run.id)
    assert fake.merges == [{"target": fake.main, "type": "workspace", "id": b.wvmid,
                            "defaultMergeStrategy": "MERGE"}]
    assert m["resultDocumentMicroversionId"] == fake.mv()
    assert fake.workspaces[fake.main]["features"][0]["name"] == "Thick plate"


async def test_merge_refuses_if_branch_moved_after_preview(fake, adapter, run):
    target = parse_url(fake.url())
    b = await _branch(fake, adapter, run)
    await adapter.merge_preview(b, target, run.id)
    fake.bump(b.wvmid)
    with pytest.raises(MisesError) as e:
        await adapter.merge(b, target, run.id)
    assert e.value.code == ErrorCode.STALE_VERSION and fake.merges == []


async def test_merge_requires_preview(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    with pytest.raises(MisesError) as e:
        await adapter.merge(b, parse_url(fake.url()), run.id)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL and fake.merges == []


async def test_propose_variables(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    out = await adapter.propose(b, run.id, source_microversion=fake.mv(b.wvmid),
                                variables={"rib_height": "12 mm", "thickness": "4 mm"},
                                feature_updates=None)
    names = {v["name"]: v for v in fake.workspaces[b.wvmid]["variables"]}
    assert names["rib_height"]["type"] == "LENGTH" and names["thickness"]["expression"] == "4 mm"
    assert out["microversion"] == fake.mv(b.wvmid)


async def test_propose_keeps_existing_variable_type(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    await adapter.propose(b, run.id, source_microversion=fake.mv(b.wvmid),
                          variables={"thickness": "#base * 2", "ratio": "#base * 2"},
                          feature_updates=None)
    names = {v["name"]: v for v in fake.workspaces[b.wvmid]["variables"]}
    assert names["thickness"]["type"] == "LENGTH"          # existing type kept
    assert names["thickness"]["expression"] == "#base * 2"
    assert names["ratio"]["type"] == "ANY"                 # new: inferred


async def test_propose_rejects_variables_with_feature_updates(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    before = len(fake.requests)
    with pytest.raises(MisesError) as e:
        await adapter.propose(b, run.id, source_microversion=fake.mv(b.wvmid),
                              variables={"rib_height": "3 mm"},
                              feature_updates=[{"featureId": "FaaA", "parameters": []}])
    assert e.value.code == ErrorCode.INVALID_INPUT and "feature_updates" in e.value.hint
    assert len(fake.requests) == before                     # no partial write


async def test_propose_variables_on_non_variable_studio_writes_nothing(fake, adapter, run):
    b = await _branch(fake, adapter, run)
    path = f"/api/v17/variables/d/{fake.did}/w/{b.wvmid}/e/{fake.eid}/variables"
    fake.fail[path] = [400]            # Onshape refuses: element is not a Variable Studio
    with pytest.raises(MisesError) as e:
        await adapter.propose(b, run.id, source_microversion=fake.mv(b.wvmid),
                              variables={"rib_height": "3 mm"}, feature_updates=None)
    assert e.value.code == ErrorCode.INVALID_INPUT and "feature_updates" in e.value.hint
    assert not any(m == "POST" for m, p in fake.requests if p == path)
    assert not any(p.endswith("/features/updates") for _, p in fake.requests)
