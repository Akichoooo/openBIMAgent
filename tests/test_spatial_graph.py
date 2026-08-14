"""三维空间拓扑图谱引擎单元测试 (Spatial BIM Graph Tests)。"""

from __future__ import annotations


from openbimagent.utility import (
    CompiledUtilityIR,
    SpatialBIMGraph,
)
from test_compiled_utility_ir import solved_payload


def _make_sample_ir() -> CompiledUtilityIR:
    payload = solved_payload()
    # 增加第 3 个节点
    payload["nodes"].append(
        {
            "node_id": "mh-003",
            "system_id": "sys-sewage",
            "node_type": "manhole",
            "position": {"x_m": 20.0, "y_m": 0.0, "z_m": 11.0},
            "ports": [
                {
                    "port_id": "mh-003-in",
                    "direction": "inlet",
                    "position": {"x_m": 20.0, "y_m": 0.0, "z_m": 9.94},
                    "ifc_class": "IfcDistributionPort",
                }
            ],
            "ground_elevation_m": 11.0,
            "ifc_class": "IfcDistributionChamberElement",
            "ifc_predefined_type": "MANHOLE",
        }
    )
    # 为 mh-002 增加出口 port
    payload["nodes"][1]["ports"].append(
        {
            "port_id": "mh-002-out",
            "direction": "outlet",
            "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
            "ifc_class": "IfcDistributionPort",
        }
    )
    # 增加第 2 个管段
    payload["segments"].append(
        {
            "segment_id": "pipe-002",
            "system_id": "sys-sewage",
            "start_port_id": "mh-002-out",
            "end_port_id": "mh-003-in",
            "centerline": [
                {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
                {"x_m": 20.0, "y_m": 0.0, "z_m": 9.94},
            ],
            "horizontal_length_m": 10.0,
            "start_invert_m": 9.97,
            "end_invert_m": 9.94,
            "slope": 0.003,
            "diameter_mm": 300.0,
            "material": "concrete",
            "min_cover_depth_m": 0.7,
            "ifc_class": "IfcPipeSegment",
            "ifc_predefined_type": "RIGIDSEGMENT",
        }
    )
    return CompiledUtilityIR.model_validate(payload)


def test_spatial_graph_build_and_dag() -> None:
    ir = _make_sample_ir()
    graph = SpatialBIMGraph.build_from_ir(ir)

    assert len(graph.nodes) == 3
    assert len(graph.segments) == 2
    assert graph.calculate_total_network_length() == 20.0

    is_dag, topo_order = graph.check_hydraulic_dag()
    assert is_dag is True
    assert topo_order == ["mh-001", "mh-002", "mh-003"]


def test_spatial_graph_radius_query() -> None:
    ir = _make_sample_ir()
    graph = SpatialBIMGraph.build_from_ir(ir)

    # 查 (0, 0) 周围 5m
    nearby = graph.find_nodes_in_radius((0.0, 0.0), 5.0)
    assert len(nearby) == 1
    assert nearby[0].node_id == "mh-001"

    # 查 (10, 0) 周围 12m（包含 mh-001, mh-002, mh-003）
    nearby_all = graph.find_nodes_in_radius((10.0, 0.0), 12.0)
    assert len(nearby_all) == 3
    assert [n.node_id for n in nearby_all] == ["mh-002", "mh-001", "mh-003"]


def test_spatial_graph_serialization() -> None:
    ir = _make_sample_ir()
    graph = SpatialBIMGraph.build_from_ir(ir)
    data = graph.to_dict()

    assert data["node_count"] == 3
    assert data["segment_count"] == 2
    assert data["total_length_m"] == 20.0
    assert data["is_hydraulic_dag"] is True
    assert len(data["nodes"]) == 3
    assert len(data["segments"]) == 2
