"""FWC M1: part_show of a catalog item inserts it into the build only when the run may
(catalog_insert's scope rule: `*` or `catalog:<item>` in its edit scope). A sub-agent scoped to
one part still shows the card, but does not change the build's part list or assembly."""
from mises.types import Caps, SafetyClass as C

ITEM = "servo_mg996r_standard"


def _scoped_run(env, edit_scope):
    run = env.store.create_run("engineer", "fake", "m", "t")
    env.store.update_run(run.id, project_id=env.project)
    env.store.create_policy(run.id, edit_scope, ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
    return run


async def _show(env, run):
    return await env.gate.call(run.id, "part_show", {"catalog_id": ITEM}, call_id=f"s{run.id}",
                               workdir=env.tmp)


async def test_a_part_scoped_sub_agent_shows_the_card_without_inserting(env, web):
    run = _scoped_run(env, ["bracket"])
    out = await _show(env, run)
    card = env.cards.get(out.data["card_id"])
    assert card["catalog_backed"] and card.get("part_id") is None
    assert out.data["part_id"] is None
    assert "outside this run's scope" in out.summary
    assert env.store.list_part_rows(env.project) == []          # the build is unchanged


async def test_a_run_scoped_to_the_item_or_everything_inserts_it(env, web):
    for scope in ([f"catalog:{ITEM}"], ["*"]):
        run = _scoped_run(env, scope)
        out = await _show(env, run)
        assert out.data["part_id"], scope
        assert "outside this run's scope" not in out.summary
