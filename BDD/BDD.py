from __future__ import annotations

from collections import deque
from typing import Optional, Any, Type


class BDDNode:
    def __init__(self, var=None, negative_child=None, positive_child=None, parent=None, value=None,
                 assignment: Optional[list[dict]] = None):
        self.var = var  # The variable for decision (None for terminal nodes)
        self.negative_child = negative_child
        self.positive_child = positive_child
        self.parent = parent
        self.value = value  # Terminal value (True or False for leaf nodes)
        if assignment is None:
            self.assignment = []
        else:
            self.assignment = assignment
        self.drawn = False

    def isLeaf(self):
        # Check if the node is a terminal node (leaf with True/False)
        return self.value is not None

    def hasChildren(self):
        return self.negative_child or self.positive_child

    def __eq__(self, other):
        if other is None or not isinstance(other, BDDNode):
            return False
        if self.isLeaf() and other.isLeaf():
            return self.value == other.value
        return (
                self.var == other.var and
                self.negative_child == other.negative_child and
                self.positive_child == other.positive_child
        )

    def __hash__(self):
        # Hash für Leaf-Nodes basierend auf ihrem Wert, ansonsten auf (var, left, right)
        if self.isLeaf():
            return hash(self.value)
        return hash((self.var, self.negative_child, self.positive_child))


def evaluate_expression(expr, assignment):
    return eval(expr, {}, assignment)


class BDD:
    def __init__(self, expression, variables: list[str]):
        self.variables = variables  # List of variables
        self.expression = expression
        self.evaluation = {}  #dict of all evaluations
        self.leafs = {False: BDDNode(value=False), True: BDDNode(value=True)}
        self.root = None

    def build_new(self):
        empty_dict = {}
        self.root = self.build(0, empty_dict)

    def build(self, var_index, current_assignment: dict):
        # end of recursion if node is a leaf
        if var_index == len(self.variables):
            current_assignment = {var: val for var, val in current_assignment.items()}  # copies current_assignment
            value = evaluate_expression(self.expression, current_assignment)
            self.evaluation[tuple(current_assignment.items())] = value
            leaf = BDDNode(value=value)
            leaf.assignment = [current_assignment]
            return leaf

        #initiate node
        var = self.variables[var_index]
        currentNode = BDDNode(var=var)
        currentNode.assignment = [({var: val for var, val in current_assignment.items()})]
        # Create node for false subtree and true subtree
        current_assignment_negative = current_assignment.copy()
        current_assignment_negative[var] = False
        leftNode = self.build(var_index + 1, current_assignment_negative)
        currentNode.negative_child = leftNode

        current_assignment_positive = current_assignment.copy()
        current_assignment_positive[var] = True
        positive_child = self.build(var_index + 1, current_assignment_positive)
        currentNode.positive_child = positive_child
        return currentNode

    def isOnlyRoot(self):
        return not self.root.hasChildren

    #TODO: needed?
    # # traverses down the diagram to evaluate it
    # def evaluate(self, node, variables):
    #     if node.isLeaf():
    #         return node.value
    #     if variables[node.var] is False:
    #         return self.evaluate(node.left, variables)
    #     else:
    #         return self.evaluate(node.positive_child, variables)

    def reduce(self):
        if not self.root.hasChildren:
            print("BDD only has root.")
            return False
        self.merge_leafs(self.root)
        self.remove_duplicate_subtree(self.root, mem={})
        self.remove_equivalent_child_nodes(self.root)
        print("Reduction done.")
        return True

    def remove_duplicate_subtree(self, node, mem):
        if node.isLeaf():
            return node

        if node in mem:
            mem[node].assignment.extend(node.assignment)
            return mem[node]

        node.negative_child = self.remove_duplicate_subtree(node.negative_child, mem)
        node.positive_child = self.remove_duplicate_subtree(node.positive_child, mem)
        mem[node] = node
        return node

    def merge_leafs(self, node: BDDNode) -> Optional[BDDNode]:
        if node.isLeaf():
            return node

        child_node_negative_child = self.merge_leafs(node.negative_child)
        if child_node_negative_child is not None:
            leaf = self.leafs[child_node_negative_child.value]
            leaf.assignment.extend(child_node_negative_child.assignment.copy())
            node.negative_child = leaf

        child_node_positive_child = self.merge_leafs(node.positive_child)
        if child_node_positive_child is not None:
            leaf = self.leafs[child_node_positive_child.value]
            leaf.assignment.extend(child_node_positive_child.assignment.copy())
            node.positive_child = leaf

        return None

    def remove_equivalent_child_nodes(self, node: BDDNode) -> Optional[BDDNode]:
        if node.negative_child is not None:
            eq_child_negative_child = self.remove_equivalent_child_nodes(node.negative_child)
            if eq_child_negative_child is not None:
                node.negative_child = eq_child_negative_child

        if node.positive_child is not None:
            eq_child_positive_child = self.remove_equivalent_child_nodes(node.positive_child)
            if eq_child_positive_child is not None:
                node.positive_child = eq_child_positive_child

        if node.negative_child is not None and node.positive_child is not None and id(node.negative_child) == id(
                node.positive_child):
            return node.negative_child
        return None

    def negate(self) -> bool:
        if not self.root.hasChildren():
            return False
        #negate solutions
        self.leafs[False].value = True
        self.leafs[True].value = False
        #switch leafs in dictionary
        new_leafs = {False: self.leafs[True], True: self.leafs[False]}
        self.leafs = new_leafs
        return True

    #TODO: test this
    @staticmethod
    def unite(BDD1: BDD, BDD2: BDD, variable_order: list) -> BDD:
        for var in BDD1.variables:
            if var not in variable_order:
                raise Exception("Variable " + var + " from BDD1 not found in variables.")

        for var in BDD2.variables:
            if var not in variable_order:
                raise Exception("Variable " + var + " from BDD2 not found in variables.")

        united = BDD(expression=str(BDD1.expression) + "and" + str(BDD2.expression), variables=variable_order)
        united.root = BDD.__apply(BDD1.root, BDD2.root, variable_order)

        return united


    @staticmethod
    def __apply(BDD1: BDDNode, BDD2: BDDNode, variable_order: list[str]) -> Type[BDDNode]:
        solution = BDDNode
        if BDD1.var not in variable_order or (BDD2.var not in variable_order):
            raise Exception("BDD variables not in variable_order")
        if BDD1.isLeaf() and BDD2.isLeaf():
            solution = BDD1.value and BDD2.value
            return solution
        elif BDD1.var() == BDD2.var():
            solution = BDDNode
            solution.negative_child = BDD.__apply(BDD1.negative_child, BDD2.negative_child)
            solution.positive_child = BDD.__apply(BDD1.positive_child, BDD2.positive_child)
            return solution
        else:
            gen = (var for var in variable_order if var == BDD1.var() or var == BDD2.var())
            higher_variable = next(gen)

            if BDD1.var == higher_variable:
                higher_BDD = BDD1
                lower_BDD = BDD2
            else:
                higher_BDD = BDD2
                lower_BDD = BDD1

            solution = BDDNode
            solution.negative_child = BDD.__apply(higher_BDD.negative_child, lower_BDD, variable_order)
            solution.positive_child = BDD.__apply(higher_BDD.positive_child, lower_BDD, variable_order)
            return solution

    def breadth_first_bottom_up_search(self) -> list[BDDNode]:
        out = []
        queue = deque([self.root])
        visited = set()

        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            out.append(node)

            if node.negative_child:
                queue.append(node.negative_child)
            if node.positive_child:
                queue.append(node.positive_child)
        return out.reverse()

    # Visualation
    def generateDot(self, filename="output"):
        node = self.root
        #out = open(f"C:\\Users\\annan\\PycharmProjects\\SaferThanPerception\\BDD\\out\\{filename}.dot", "w")
        out = open(f"BDD\\out\\{filename}.dot", "w")
        out.write(f"digraph{{\nlabel=\"{self.expression}\\n\\n\"\n{id(node)}[label={node.var}]")
        self.__generate_dot_recursive(node, out)
        out.write("}")
        print("Dot File generated")
        self.reset_draw(self.root)

    def __generate_dot_recursive(self, node, out):
        if not node.drawn:
            # draw negative_child childnode
            if node.negative_child is not None:
                child_node = node.negative_child
                if node.negative_child.var is not None:
                    assignments = "\n".join(str(d) for d in child_node.assignment)
                    out.write(f"{id(child_node)}[label=\"{child_node.var} {assignments}\"]\n")
                    out.write(f"{id(node)} -> {id(child_node)}[style=dashed]\n")
                    self.__generate_dot_recursive(child_node, out)
                elif node.negative_child.value is not None:
                    assignments = "\n".join(str(d) for d in child_node.assignment)
                    out.write(f"{id(child_node)}[label=\"{child_node.value}\n{assignments}\"]\n")
                    out.write(f"{id(node)} -> {id(child_node)}[style=dashed]\n")
            #draw right childnode
            if node.positive_child is not None:
                child_node = node.positive_child
                if node.positive_child.var is not None:
                    assignments = "\n".join(str(d) for d in child_node.assignment)
                    out.write(f"{id(child_node)}[label=\"{child_node.var} {assignments}\"]\n")
                    out.write(f"{id(node)} -> {id(child_node)}\n")
                    self.__generate_dot_recursive(node.positive_child, out)
                elif node.positive_child.value is not None:
                    assignments = "\n".join(str(d) for d in child_node.assignment)
                    out.write(f"{id(child_node)}[label=\"{child_node.value}\n{assignments}\"]\n")
                    out.write(f"{id(node)} -> {id(child_node)}\n")
            node.drawn = True

    def reset_draw(self, node):
        if node.isLeaf():
            node.drawn = False
        if node.negative_child is not None:
            self.reset_draw(node.negative_child)
        if node.positive_child is not None:
            self.reset_draw(node.positive_child)
        node.drawn = False


#Example:
e = "(A and B) or C"
e = "((not A or B) and (not B or A)) and ((not C or D) and (not D or C))"
v = ['A', 'B', 'C', 'D']
bdd = BDD(e, v)

print("Binary Decision Diagram (BDD):")
bdd.generateDot(filename="out")
bdd.reduce()
bdd.generateDot(filename="reduced_out")
bdd.negate()
bdd.generateDot(filename="negated_out")
for k, v in bdd.evaluation.items():
    print(f"{k}: {v}")
