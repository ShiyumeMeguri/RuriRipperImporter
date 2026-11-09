"""Small public node-building vocabulary shared by host-side material projections."""


def math_node(tree, operation, a, b=None, label=""):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    node.label = label
    for index, value in enumerate((a, b)):
        if value is not None:
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                tree.links.new(value, node.inputs[index])
    return node.outputs[0]


def vector_node(tree, operation, a, b=None):
    node = tree.nodes.new("ShaderNodeVectorMath")
    node.operation = operation
    for index, value in enumerate((a, b)):
        if value is not None:
            if isinstance(value, (tuple, list)):
                node.inputs[index].default_value = value
            else:
                tree.links.new(value, node.inputs[index])
    return node.outputs["Value" if operation == "DOT_PRODUCT" else "Vector"]
