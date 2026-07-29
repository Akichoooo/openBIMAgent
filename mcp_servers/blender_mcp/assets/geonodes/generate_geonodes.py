"""生成预置 Damage GeoNodes 修改器资产库。

3 个 Geometry Nodes 节点组（非破坏性修改器）：
- DamageEdgeWear   边缘磨损（按法线 + Noise 偏移顶点）
- DamageRustSpots  锈斑（散布小凹陷点）
- DamageScratches  划痕（程序化刻痕）

全部基于 Geometry Nodes 内置节点，参数化（Intensity/Seed/Density 等）。
Blender 5.2 兼容：用 group.interface.new_socket()（非旧版 group.inputs.new()）。

用法：
    D:/devloop/blender/blender.exe --background --factory-startup --python generate_geonodes.py
产物：damage_geonodes.blend（与脚本同目录）。
"""

from __future__ import annotations

import bpy


def _add_socket(group, name, in_out, socket_type, default_value=None):
    """5.x 兼容：用 group.interface.new_socket() 创建接口并设默认值。"""
    sock = group.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default_value is not None:
        try:
            sock.default_value = default_value
        except (AttributeError, TypeError):
            pass
    return sock


def _new_geo_group(name):
    """新建 GeometryNodeTree 节点组，预置 Input/Output 节点。"""
    group = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    g_in = group.nodes.new('NodeGroupInput')
    g_in.location = (-600, 0)
    g_out = group.nodes.new('NodeGroupOutput')
    g_out.location = (600, 0)
    return group, g_in, g_out


def _set_position_node(group, location=(0, 0)):
    """Set Position 节点：用于偏移顶点制造磨损。"""
    node = group.nodes.new('GeometryNodeSetPosition')
    node.location = location
    return node


def create_damage_edge_wear():
    """边缘磨损：按法线方向 + Noise 偏移顶点（磨圆/缺口感）。"""
    group, g_in, g_out = _new_geo_group("DamageEdgeWear")
    _add_socket(group, "Geometry", 'INPUT', 'NodeSocketGeometry')
    _add_socket(group, "Intensity", 'INPUT', 'NodeSocketFloat', 0.4)
    _add_socket(group, "Seed", 'INPUT', 'NodeSocketInt', 42)
    _add_socket(group, "Geometry", 'OUTPUT', 'NodeSocketGeometry')

    set_pos = _set_position_node(group, (200, 0))
    # Position 节点取当前顶点位置
    pos_node = group.nodes.new('GeometryNodeInputPosition')
    pos_node.location = (-400, -200)
    # Normal 节点取法线（边缘方向）
    normal_node = group.nodes.new('GeometryNodeInputNormal')
    normal_node.location = (-400, -400)
    # Noise Texture 给随机偏移
    noise = group.nodes.new('ShaderNodeTexNoise')
    noise.location = (-400, -600)
    noise.inputs["Scale"].default_value = 15.0
    # Seed 接到 noise 的 Detail 不合适，用 Random Value 更直接
    # 用 Noise * Normal * Intensity 作为偏移量
    mul_noise_normal = group.nodes.new('ShaderNodeVectorMath')
    mul_noise_normal.operation = 'MULTIPLY'
    mul_noise_normal.location = (0, -400)
    mul_intensity = group.nodes.new('ShaderNodeVectorMath')
    mul_intensity.operation = 'MULTIPLY'
    mul_intensity.location = (100, -200)
    add_offset = group.nodes.new('ShaderNodeVectorMath')
    add_offset.operation = 'ADD'
    add_offset.location = (0, 0)

    group.links.new(g_in.outputs["Geometry"], set_pos.inputs["Geometry"])
    group.links.new(pos_node.outputs["Position"], add_offset.inputs[0])
    group.links.new(normal_node.outputs["Normal"], mul_noise_normal.inputs[0])
    # noise Fac 是 float，需转 Vector：用 Vector Math SCALE 或直接乘
    # 这里用 mul_noise_normal 的第二个输入接 noise（自动 broadcast? 否）
    # 改用 Mapping：noise → Vector Math SCALE (1,1,1) * noise → 再乘 Normal
    # 简化：用 Math 节点乘
    noise_to_vec = group.nodes.new('ShaderNodeCombineXYZ')
    noise_to_vec.location = (-200, -600)
    group.links.new(noise.outputs["Fac"], noise_to_vec.inputs["X"])
    group.links.new(noise.outputs["Fac"], noise_to_vec.inputs["Y"])
    group.links.new(noise.outputs["Fac"], noise_to_vec.inputs["Z"])
    group.links.new(noise_to_vec.outputs["Vector"], mul_noise_normal.inputs[1])
    group.links.new(mul_noise_normal.outputs["Vector"], mul_intensity.inputs[0])
    # Intensity 接 Vector Math MULTIPLY 第二输入（Vector）
    # Intensity 是 float，需转 Vector：用 Combine 或直接用 Math
    int_to_vec = group.nodes.new('ShaderNodeCombineXYZ')
    int_to_vec.location = (-200, -200)
    # 接 Intensity 到 X/Y/Z
    group.links.new(g_in.outputs["Intensity"], int_to_vec.inputs["X"])
    group.links.new(g_in.outputs["Intensity"], int_to_vec.inputs["Y"])
    group.links.new(g_in.outputs["Intensity"], int_to_vec.inputs["Z"])
    group.links.new(int_to_vec.outputs["Vector"], mul_intensity.inputs[1])
    group.links.new(add_offset.outputs["Vector"], set_pos.inputs["Offset"])
    group.links.new(mul_intensity.outputs["Vector"], add_offset.inputs[1])
    # Seed 通过 Math 转 float 接 noise 的 Distortion（5.x Noise 无 Offset 输入）
    seed_math = group.nodes.new('ShaderNodeMath')
    seed_math.operation = 'MULTIPLY'
    seed_math.location = (-600, -700)
    seed_math.inputs[1].default_value = 0.1
    group.links.new(g_in.outputs["Seed"], seed_math.inputs[0])
    group.links.new(seed_math.outputs[0], noise.inputs["Distortion"])
    group.links.new(set_pos.outputs["Geometry"], g_out.inputs["Geometry"])
    return group


def create_damage_rust_spots():
    """锈斑：在表面散布小凹陷点（实例化小球并合并）。"""
    group, g_in, g_out = _new_geo_group("DamageRustSpots")
    _add_socket(group, "Geometry", 'INPUT', 'NodeSocketGeometry')
    _add_socket(group, "Density", 'INPUT', 'NodeSocketFloat', 0.3)
    _add_socket(group, "Size", 'INPUT', 'NodeSocketFloat', 0.05)
    _add_socket(group, "Seed", 'INPUT', 'NodeSocketInt', 7)
    _add_socket(group, "Geometry", 'OUTPUT', 'NodeSocketGeometry')

    # Distribute Points on Faces
    dist = group.nodes.new('GeometryNodeDistributePointsOnFaces')
    dist.location = (0, 0)
    try:
        dist.distribute_method = 'RANDOM'
    except (TypeError, AttributeError):
        pass
    # Icosphere 作为锈斑实例
    ico = group.nodes.new('GeometryNodeMeshIcoSphere')
    ico.location = (0, -300)
    # Instances 节点
    inst = group.nodes.new('GeometryNodeInstanceOnPoints')
    inst.location = (250, 0)
    # Join Geometry 合并原几何 + 锈斑
    join = group.nodes.new('GeometryNodeJoinGeometry')
    join.location = (450, 0)

    group.links.new(g_in.outputs["Geometry"], dist.inputs["Mesh"])
    # 5.2 输入名容错：Density Max / Density
    density_in = dist.inputs.get("Density Max") or dist.inputs.get("Density")
    if density_in is not None:
        group.links.new(g_in.outputs["Density"], density_in)
    seed_in = dist.inputs.get("Seed")
    if seed_in is not None:
        group.links.new(g_in.outputs["Seed"], seed_in)
    group.links.new(dist.outputs["Points"], inst.inputs["Points"])
    group.links.new(ico.outputs["Mesh"], inst.inputs["Instance"])
    radius_in = ico.inputs.get("Radius")
    if radius_in is not None:
        group.links.new(g_in.outputs["Size"], radius_in)
    group.links.new(g_in.outputs["Geometry"], join.inputs[0])
    group.links.new(inst.outputs["Instances"], join.inputs[0])
    group.links.new(join.outputs["Geometry"], g_out.inputs["Geometry"])
    return group


def create_damage_scratches():
    """划痕：程序化刻痕（用 Noise 选顶点偏移，制造细长凹陷）。"""
    group, g_in, g_out = _new_geo_group("DamageScratches")
    _add_socket(group, "Geometry", 'INPUT', 'NodeSocketGeometry')
    _add_socket(group, "Count", 'INPUT', 'NodeSocketInt', 50)
    _add_socket(group, "Length", 'INPUT', 'NodeSocketFloat', 0.1)
    _add_socket(group, "Seed", 'INPUT', 'NodeSocketInt', 11)
    _add_socket(group, "Geometry", 'OUTPUT', 'NodeSocketGeometry')

    set_pos = _set_position_node(group, (300, 0))
    pos_node = group.nodes.new('GeometryNodeInputPosition')
    pos_node.location = (-400, -200)
    # Noise 选条纹方向
    noise = group.nodes.new('ShaderNodeTexNoise')
    noise.location = (-400, -400)
    noise.inputs["Scale"].default_value = 50.0
    # 用 noise > threshold 选刻痕区域（Math GREATER_THAN）
    thr = group.nodes.new('ShaderNodeMath')
    thr.operation = 'GREATER_THAN'
    thr.location = (-200, -400)
    thr.inputs[1].default_value = 0.7
    # 刻痕方向：沿 X 轴偏移（细长）
    # 偏移量 = -threshold_mask * Length（向内凹）
    mul_len = group.nodes.new('ShaderNodeMath')
    mul_len.operation = 'MULTIPLY'
    mul_len.location = (0, -400)
    # 转 Vector（沿 -Z 凹陷）
    to_vec = group.nodes.new('ShaderNodeCombineXYZ')
    to_vec.location = (150, -400)
    # 偏移加到 Position
    add = group.nodes.new('ShaderNodeVectorMath')
    add.operation = 'ADD'
    add.location = (150, 0)

    group.links.new(g_in.outputs["Geometry"], set_pos.inputs["Geometry"])
    group.links.new(pos_node.outputs["Position"], add.inputs[0])
    group.links.new(noise.outputs["Fac"], thr.inputs[0])
    group.links.new(thr.outputs[0], mul_len.inputs[0])
    group.links.new(g_in.outputs["Length"], mul_len.inputs[1])
    group.links.new(mul_len.outputs[0], to_vec.inputs["Z"])
    # Seed 通过 Math 转 float 接 noise Distortion（5.x Noise 无 Offset 输入）
    seed_math = group.nodes.new('ShaderNodeMath')
    seed_math.operation = 'MULTIPLY'
    seed_math.location = (-600, -500)
    seed_math.inputs[1].default_value = 0.1
    group.links.new(g_in.outputs["Seed"], seed_math.inputs[0])
    group.links.new(seed_math.outputs[0], noise.inputs["Distortion"])
    group.links.new(to_vec.outputs["Vector"], add.inputs[1])
    group.links.new(add.outputs["Vector"], set_pos.inputs["Offset"])
    group.links.new(set_pos.outputs["Geometry"], g_out.inputs["Geometry"])
    return group


def save_geonodes():
    """创建全部 3 个 GeoNodes 节点组并保存到 damage_geonodes.blend。"""
    create_damage_edge_wear()
    create_damage_rust_spots()
    create_damage_scratches()

    names = sorted(n.name for n in bpy.data.node_groups)
    print(f"[generate_geonodes] created node_groups: {names}")

    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(script_dir, "damage_geonodes.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"[generate_geonodes] saved: {out_path}")


if __name__ == "__main__":
    save_geonodes()
