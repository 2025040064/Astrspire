"""Typed graph and explicit execution seams; injected transports only."""
import copy
import pytest

from qx_stc_isp.vofa.workflow import WorkflowProgram, WorkflowRunner, WorkflowInput


def graph(nodes, edges, **extra):
    return {"version": 1, "revision": 1, "nodes": nodes,
            "edges": [{"from": a, "out": port, "to": b, "in": "value"} for a, port, b in edges], **extra}


def node(identifier, node_type, **config):
    return {"id": identifier, "type": node_type, "config": config}


def test_number_pipeline_compiles_freezes_and_derives_without_modifying_input():
    document = graph([node("source", "Source", output_type="number"),
                      node("math", "Math", operation="multiply", operand=2), node("end", "End")],
                     [("source", "value", "math"), ("math", "value", "end")])
    program = WorkflowProgram.compile(document)
    document["nodes"][1]["config"]["operand"] = 100
    results = []
    runner = WorkflowRunner(on_result=results.append)
    runner.start(program, connection_id="board", generation=2)
    value = WorkflowInput("board", 2, 1, 4)
    runner.feed(value)
    assert results[-1].value == 8
    assert results[-1].connection_id == "board"
    assert results[-1].revision == 1
    assert value.value == 4
    assert not runner.active


def sending_graph(trigger=None, wait=False):
    nodes = [node("source", "Source", output_type="fields"),
             node("template", "Template", template_id="ping", revision=1, parameters={"n": 7}),
             node("send", "Send")]
    edges = [("source", "value", "template"), ("template", "value", "send")]
    if wait:
        nodes.append(node("wait", "Wait", field="ack", equals=7, timeout_ms=200))
        edges.append(("send", "value", "wait"))
    nodes.append(node("end", "End"))
    edges.append(("wait" if wait else "send", "value", "end"))
    return graph(nodes, edges, trigger=trigger or {"mode": "manual", "interval_ms": 1000})


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d["edges"][0].update({"out": "unknown"}), "端口"),
    (lambda d: d["nodes"][0]["config"].update({"output_type": "text"}), "类型"),
    (lambda d: d["edges"].append({"from": "math", "out": "value", "to": "source", "in": "value"}), "Source|循环"),
    (lambda d: d["edges"][0].update({"to": "missing"}), "不存在"),
])
def test_compiler_rejects_invalid_references_types_and_cycles(mutation, message):
    document = graph([node("source", "Source", output_type="number"),
                      node("math", "Math"), node("end", "End")],
                     [("source", "value", "math"), ("math", "value", "end")])
    mutation(document)
    with pytest.raises(ValueError, match=message):
        WorkflowProgram.compile(document)


@pytest.mark.parametrize("arm, source", [(False, "live"), (True, "replay"), (True, "swd"), (True, "preview")])
def test_send_requires_explicit_arm_and_live_writable_source(arm, source):
    sent = []
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: b"PING")
    with pytest.raises(ValueError, match="发送|只读"):
        runner.start(sending_graph(), connection_id="board", generation=2, arm_sends=arm, source_kind=source)
    assert sent == []


def test_request_waits_for_valid_matching_ack_not_merely_driver_queue():
    now = [10.]
    sent, results = [], []
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: b"PING", clock=lambda: now[0], on_result=results.append)
    runner.start(sending_graph(wait=True), connection_id="board", generation=2, arm_sends=True)
    runner.feed(WorkflowInput("board", 2, 1, {}, {}))
    assert len(sent) == 1 and runner.state == "waiting"
    assert not any(item.kind == "result" for item in results)
    runner.feed(WorkflowInput("board", 2, 2, {}, {"ack": 7}, valid=False))
    runner.feed(WorkflowInput("other", 2, 3, {}, {"ack": 7}))
    runner.feed(WorkflowInput("board", 2, 4, {}, {"ack": 8}))
    assert runner.active and len(sent) == 1
    runner.feed(WorkflowInput("board", 2, 5, {}, {"ack": 7}))
    assert not runner.active and runner.state == "complete"
    assert results[-1].message == "设备响应匹配"


def test_wait_timeout_does_not_resend_and_cancel_revokes_job():
    now = [0.]
    sent, cancelled = [], []
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: b"P", clock=lambda: now[0],
                            cancel_job=lambda *args: cancelled.append(args))
    job = runner.start(sending_graph(wait=True), connection_id="board", generation=4, arm_sends=True)
    runner.feed(WorkflowInput("board", 4, 1, {}))
    now[0] = .3
    runner.poll()
    assert runner.state == "failed" and "超时" in runner.error
    assert len(sent) == 1 and cancelled == [("board", job)]


def test_generation_change_or_guard_failure_stops_before_any_later_send():
    enabled = [True]
    sent = []
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: b"P", guard=lambda *args: enabled[0])
    runner.start(sending_graph({"mode": "timer", "interval_ms": 100}), connection_id="board", generation=1, arm_sends=True)
    enabled[0] = False
    runner.feed(WorkflowInput("board", 1, 1, {}))
    assert not runner.active and sent == []
    enabled[0] = True
    runner.start(sending_graph(), connection_id="board", generation=1, arm_sends=True)
    runner.feed(WorkflowInput("board", 2, 1, {}))
    assert not runner.active and sent == []


def test_onchange_baseline_no_send_and_sender_has_fixed_target_and_frozen_template():
    sent, template = [], [b"OLD"]
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: template[0])
    document = sending_graph({"mode": "onChange", "interval_ms": 100})
    runner.start(document, connection_id="fixed-board", generation=7, arm_sends=True)
    template[0] = b"NEW"
    runner.feed(WorkflowInput("fixed-board", 7, 1, {"value": 1}))
    assert sent == []
    runner.feed(WorkflowInput("fixed-board", 7, 2, {"value": 1}))
    assert sent == []
    runner.feed(WorkflowInput("fixed-board", 7, 3, {"value": 2}))
    assert sent[0][:3] == ("fixed-board", 7, b"OLD")


def test_send_quota_queue_failure_and_cancellation_are_final_not_retries():
    now = [0.]
    sent = []
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: b"P", clock=lambda: now[0])
    runner.start(sending_graph({"mode": "onChange", "interval_ms": 100}), connection_id="board", generation=1, arm_sends=True)
    for index in range(12):
        runner.feed(WorkflowInput("board", 1, index, {"value": index}))
    assert len(sent) == 10 and not runner.active and "10" in runner.error
    runner.enqueue = lambda *_: False
    runner.start(sending_graph(), connection_id="board", generation=1, arm_sends=True)
    runner.feed(WorkflowInput("board", 1, 0, {}))
    assert runner.state == "failed" and runner.sent_count == 0
    runner.poll()
    assert len(sent) == 10


def test_timed_repeat_delay_never_bursts_and_cancel_removes_future_steps():
    now = [0.]
    sent, results = [], []
    document = sending_graph()
    document["nodes"].insert(2, node("repeat", "Repeat", count=3, interval_ms=100))
    document["edges"][1] = {"from": "template", "out": "value", "to": "repeat", "in": "value"}
    document["edges"].append({"from": "repeat", "out": "value", "to": "send", "in": "value"})
    runner = WorkflowRunner(enqueue=lambda *args: sent.append(args) or True,
                            template_resolver=lambda *args: b"P", clock=lambda: now[0], on_result=results.append)
    runner.start(document, connection_id="board", generation=1, arm_sends=True)
    runner.feed(WorkflowInput("board", 1, 0, {}))
    assert len(sent) == 1 and runner.active
    now[0] = .1
    runner.poll()
    assert len(sent) == 2
    runner.stop()
    now[0] = 1
    runner.poll()
    assert len(sent) == 2


def test_typed_select_condition_string_and_filter_keep_result_provenance():
    document = graph([node("source", "Source", output_type="fields"),
        node("select", "Select", field="channels.1", output_type="number"),
        node("condition", "Condition", operator="ge", operand=2),
        node("filter", "Filter", kind="exponential", alpha=.5),
        node("format", "String", operation="format", format="value={value}"), node("end", "End")],
        [("source", "value", "select"), ("select", "value", "condition"),
         ("condition", "true", "filter"), ("filter", "value", "format"), ("format", "value", "end")],
        trigger={"mode": "onChange", "interval_ms": 100})
    result = []
    runner = WorkflowRunner(on_result=result.append)
    runner.start(document, connection_id="board", generation=1)
    runner.feed(WorkflowInput("board", 1, 1, {"channels": [1, 0]}))
    runner.feed(WorkflowInput("board", 1, 2, {"channels": [1, 2]}))
    runner.feed(WorkflowInput("board", 1, 3, {"channels": [1, 4]}))
    assert [item.value for item in result] == ["value=2.0", "value=3.0"]
    assert result[-1].sequence == 3 and result[-1].revision == 1


def test_fft_node_requires_series_and_delay_waits_for_injected_clock():
    document = graph([node("source", "Source", output_type="series"),
                      node("fft", "FFT", sample_rate=4, window="rect"),
                      node("delay", "Delay", milliseconds=100), node("end", "End")],
                     [("source", "value", "fft"), ("fft", "value", "delay"), ("delay", "value", "end")])
    now, result = [0.], []
    runner = WorkflowRunner(clock=lambda: now[0], on_result=result.append)
    runner.start(document, connection_id="board", generation=1)
    runner.feed(WorkflowInput("board", 1, 1, [0, 1, 0, -1]))
    assert result == [] and runner.active
    now[0] = .1
    runner.poll()
    assert result[-1].value.values[1] == pytest.approx(1)


def test_bounded_total_duration_timer_and_template_revision_are_frozen():
    class Templates:
        revision = 1
        def freeze(self, template_id, revision):
            if revision != self.revision:
                raise ValueError("模板修订不存在")
            prefix = self.revision
            return lambda parameters: bytes([prefix, parameters["x"]])
    template = Templates()
    document = sending_graph({"mode": "timer", "interval_ms": 100})
    document["nodes"][1]["config"]["bindings"] = {"x": "fields.x"}
    document["limits"] = {"per_second": 10, "total": 2, "seconds": 1}
    now, sent = [0.], []
    runner = WorkflowRunner(clock=lambda: now[0], template_resolver=template,
                            enqueue=lambda *args: sent.append(args) or True)
    runner.start(document, connection_id="board", generation=1, arm_sends=True)
    template.revision = 2
    runner.feed(WorkflowInput("board", 1, 1, {}, {"x": 3}))
    assert sent == []
    now[0] = .1
    runner.poll()
    now[0] = .2
    runner.poll()
    assert [item[2] for item in sent] == [bytes([1, 3]), bytes([1, 3])]
    now[0] = .3
    runner.poll()
    assert runner.state == "failed" and len(sent) == 2


def test_expired_automatic_run_stops_without_new_source_events():
    now = [0.]
    runner = WorkflowRunner(clock=lambda: now[0])
    document = graph([node("s", "Source"), node("e", "End")], [("s", "value", "e")],
                     trigger={"mode": "onChange", "interval_ms": 100}, limits={"seconds": 1})
    runner.start(document, connection_id="board", generation=1)
    now[0] = 1
    runner.poll()
    assert not runner.active and "时限" in runner.error


def test_input_payload_is_detached_and_immutable():
    source = {"values": [1, 2]}
    event = WorkflowInput("c", 1, 1, source, source)
    source["values"][0] = 9
    assert event.value["values"] == (1, 2)
    with pytest.raises(TypeError):
        event.fields["other"] = 2


def test_lossy_integer_math_is_rejected_and_named_field_stays_exact():
    result = []
    document = graph([node("s", "Source", output_type="fields"),
                      node("n", "Select", field="large", output_type="number"),
                      node("m", "Math"), node("e", "End")],
                     [("s", "value", "n"), ("n", "value", "m"), ("m", "value", "e")])
    runner = WorkflowRunner(on_result=result.append)
    runner.start(document, connection_id="board", generation=1)
    event = WorkflowInput("board", 1, 1, {"large": 2 ** 60 + 1})
    runner.feed(event)
    assert result == [] and runner.state == "failed" and "精度" in runner.error
    assert event.value["large"] == 2 ** 60 + 1


