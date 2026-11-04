"""DOG demo: the lead asked "approve v2 or send marks?" while the user's sketch marks were
already waiting in its queue (they arrive only when the turn ends), so the user had to answer
a question they had already answered. ui_ask_user now hands back the queued messages instead
of asking."""
from types import SimpleNamespace

from mises.ui_tools import AskArgs, ui_ask_user


class _Rt:
    def __init__(self, queued):
        self.lead_queues = {"r1": queued} if queued else {}
        self.asked = []

    async def ask(self, run_id, question, options, **kw):
        self.asked.append(question)
        return "yes"


def _ctx(rt, tmp_path):
    return SimpleNamespace(services={"runtime": rt}, run_id="r1", workdir=tmp_path,
                           events=None)


async def test_queued_user_messages_answer_instead_of_asking(tmp_path):
    rt = _Rt([{"message_id": "msg_1", "shown": "Sketch feedback v1: 1 mark and a comment"}])
    out = await ui_ask_user.handler(_ctx(rt, tmp_path), AskArgs(question="Approve v2?",
                                                                options=["Approve", "Revise"]))
    assert rt.asked == []
    assert out.data["asked"] is False
    assert out.data["queued_messages"][0]["text"].startswith("Sketch feedback v1")
    assert "end this turn" in out.summary.lower()


async def test_no_queue_asks_as_before(tmp_path):
    rt = _Rt([])
    out = await ui_ask_user.handler(_ctx(rt, tmp_path), AskArgs(question="Approve v2?"))
    assert rt.asked == ["Approve v2?"] and out.data["answer"] == "yes"
