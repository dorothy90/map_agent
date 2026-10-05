from langgraph.graph import StateGraph, START, END
from .types import RunState
from .nodes import Nodes


def build_harness(*, model, registry, store, checkpointer, control, instructions):
    nodes = Nodes(model, registry, control, instructions)
    graph = StateGraph(RunState)
    graph.add_node("model", nodes.think)
    graph.add_node("execute", nodes.execute)
    graph.add_node("ask", nodes.ask)
    graph.add_node("verify", nodes.verify)
    graph.add_edge(START, "model")

    def route(state):
        if state.get("status") in ("completed", "partial", "failed", "cancelled"):
            return END
        if state.get("question"):
            return "ask"
        if state.get("pending"):
            return "execute"
        if state.get("candidate"):
            return "verify"
        return "model"

    for name in ("model", "execute", "ask", "verify"):
        graph.add_conditional_edges(name, route, ["model", "execute", "ask", "verify", END])
    return graph.compile(checkpointer=checkpointer)
