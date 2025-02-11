from __future__ import annotations
from collections import deque
import operator
from typing import Optional
import re
import os
import glob
import shutil
import boolean_parser
from gmpy2 import mpq
import pyparsing as pp
from sqlalchemy.sql.operators import is_associative
from collections import defaultdict

variable = pp.Word(pp.alphas)
expr = pp.infix_notation(
    variable,
    [
        ("not", 1, pp.opAssoc.RIGHT),
        ("and", 2, pp.opAssoc.LEFT),
        ("or", 2, pp.opAssoc.LEFT),
    ],
)

ops_lookup = {"and": operator.and_, "or": operator.or_}

# deletes all files from the out folder 
def delete_all_files_from_out():
    files = glob.glob('out/*')
    for obj in files:
        if os.path.isfile(obj):
            os.remove(obj)
        elif os.path.isdir(obj):
            shutil.rmtree(obj)


class BDDNode:
    def __init__(self, var: str = None, value: bool = None, assignments: Optional[list[dict]] = None, is_alt=False,
                negative_child: Optional[BDDNode] = None,
                negative_probability: dict[BDDNode, mpq] = None,
                positive_child: Optional[BDDNode] = None,
                positive_probability: dict[BDDNode, mpq] = None):

        self.variable = var  #None for leaf nodes
        self.is_alt = is_alt #used to differentiate variables and their renamed counterpart
        self.value = value  #None for nodes with children
        
        self.negative_child = negative_child
        self.negative_probability = {} if negative_probability is None else negative_probability
        self.positive_child = positive_child
        self.positive_probability = {} if positive_probability is None else positive_probability

        #TODO: remove assignments
        if assignments is None:
            self.assignments = []
        else:
            self.assignments = assignments
        self.drawn = False

    def isLeaf(self):
        return self.value is not None and self.variable is None

    def hasChildren(self):
        return self.negative_child or self.positive_child
    
    def isEmpty(self):
        return self.variable is None and self.value is None

    #creates new BDD with this node as root and reduces it
    def reduce(self, overarching_tree: BDD):
        temp_bdd = BDD(overarching_tree.expression, overarching_tree.variables, False)
        temp_bdd.root = self
        temp_bdd.reduce()

    def copy_node(self, renaming: bool) -> BDDNode:
        var = None
        value = None
        if self.isLeaf():
            value = self.value
        else:
            var = self.variable
        #node_assignment_copy = self.assignments.copy()
        #for i in range(len(node_assignment_copy)):
        #    node_assignment_copy[i] = {k: v for k, v in node_assignment_copy[i].items()}
        return BDDNode(var=var, value=value, is_alt=renaming)

    def __eq__(self, other):
        if other is None or not isinstance(other, BDDNode):
            return False
        if self.isLeaf() and other.isLeaf():
            return self.value == other.value 
        return (
                self.variable == other.variable and
                self.negative_child == other.negative_child and
                self.positive_child == other.positive_child
        )

    def __hash__(self):
        # Hash für Leaf-Nodes basierend auf ihrem Wert, ansonsten auf (var, left, right)
        if self.isLeaf():
            return hash(self.value)
        return hash((self.variable, self.negative_child, self.positive_child))
#______________________________________________________________________________________________________________________________


class BDD:
    def __init__(self, expression: str, variables: list[str], build_new=True):
        self.variables = variables  # List of variables (alt vars are stored with "_" after the name)
        self.expression = expression
        self.evaluation = {} #dict of all evaluations
        self.leafs = {False: BDDNode(value=False), True: BDDNode(value=True)}
        self.root = None
        self.probabilities_set = False
        if build_new:
            self.build_new()

    def build_new(self):
        ops = expr.parse_string(self.expression).as_list()
        while len(ops) == 1 and isinstance(ops[0], list):
            ops = ops[0]
        root = self.build(ops)
        self.root = root

    def build(self, ops) -> BDDNode:
        #case only variable
        if len(ops) == 1 and isinstance(ops[0], str):
            root = BDDNode(var = ops[0])
            root.negative_child = BDDNode(value = False)
            root.positive_child = BDDNode(value = True)
            return root
        #not case
        elif len(ops) == 2:
            if ops[0] != "not":
                raise Exception("Wrong expression given: "+ops.as_list())
            root = self.build(ops[1])
            bdd = BDD("", self.variables, False)
            bdd.root = root
            bdd.reduce()
            negated_bdd = bdd.negate()
            return negated_bdd.root
        #binary operator case
        elif len(ops) == 3:
            root1 = self.build(ops[0])
            bdd1 = BDD("", self.variables, False)
            bdd1.root = root1
            bdd1.reduce()
            op = ops[1]
            root2 = self.build(ops[2])
            bdd2 = BDD("", self.variables, False)
            bdd2.root = root2
            bdd2.reduce()
            bdd_sol = self.apply_operand(bdd1, bdd2, op, self.variables)
            return bdd_sol.root
        else: raise Exception("Wrong expression given: "+ops.as_list()+ "Parentheses not set correctly?")


    def reduce(self):
        if not self.root.hasChildren:
            print("BDD only has root.")
            return False
        self.__merge_leafs(self.root)
        self.__remove_duplicate_subgraph(self.root, mem=[])
        self.__remove_equivalent_child_nodes(self.root)

        #print("Reduction done.")
        return True

    def __remove_duplicate_subgraph(self, node: BDDNode, mem: list[BDDNode]):
        if node.isLeaf():
            return node
        elif node in mem:
            return mem[mem.index(node)]
        else:
            mem.append(node)
            node.negative_child = self.__remove_duplicate_subgraph(node.negative_child, mem)
            node.positive_child = self.__remove_duplicate_subgraph(node.positive_child, mem)
            return node

    def __merge_leafs(self, node: BDDNode) -> Optional[BDDNode]:
        if node is None:
            raise Exception("unexpected Node is None")

        if node.isLeaf():
            return node

        child_node_negative_child = self.__merge_leafs(node.negative_child)
        if child_node_negative_child is not None:
            leaf = self.leafs[child_node_negative_child.value]
            self.add_assignments(leaf, child_node_negative_child.assignments)
            node.negative_child = leaf

        child_node_positive_child = self.__merge_leafs(node.positive_child)
        if child_node_positive_child is not None and child_node_positive_child not in self.leafs:
            leaf = self.leafs[child_node_positive_child.value]
            self.add_assignments(leaf, child_node_positive_child.assignments)
            node.positive_child = leaf
            
        return None

    def __remove_equivalent_child_nodes(self, node: BDDNode) -> Optional[BDDNode]:
        #if root is reducable reduce it and set new root
        if node == self.root:
            while self.root.negative_child == self.root.positive_child:
                self.root = self.root.negative_child
                node = self.root
        
        if node.negative_child is not None:
            child_of_neg_child = self.__remove_equivalent_child_nodes(node.negative_child)
            #if not None, the children of the neg child node are identical -> original negative child gets skipped over
            if child_of_neg_child is not None:
                node.negative_child = child_of_neg_child

        if node.positive_child is not None:
            child_of_pos_child = self.__remove_equivalent_child_nodes(node.positive_child)
            #equivalent to negative child
            if child_of_pos_child is not None:
                node.positive_child = child_of_pos_child

        #negative child is same as positive child and said child is returned
        if node.negative_child is not None and node.positive_child is not None and id(node.negative_child) == id(
                node.positive_child):
            return node.negative_child
        
        return None

    #TODO: remove
    #adds assignments that are not already in the node
    @staticmethod
    def add_assignments(node: BDDNode, assignments: list[dict]):
        for a in assignments:
            if a not in node.assignments:
                node.assignments.append(a)

    def find_paths(self, target: BDDNode,
                    current_node: BDDNode = None,
                    overall_assignments: list[dict[str, bool]] = None,
                    current_assignments: list[dict[str, bool]] = None,
                    searched_variables: list[str] = None) -> list[dict[str, bool]]:
        #init
        if overall_assignments is None:
            overall_assignments = []
        if current_assignments is None:
            current_assignments = [{}]
        if current_node is None:
            current_node = self.root
        if searched_variables is None:
            searched_variables = self.variables.copy()

        # node is leaf --> target was not found
        if current_node.isLeaf():
            return overall_assignments

        # tree skipped at least one variable -> assignments get copied one sets
        # skipped variable(s) to True the other to false, both are appended
        searched_variables = searched_variables.copy()
        next_var = searched_variables.pop(0)
        while next_var != current_node.variable:
            current_assignments_copies = []
            for assignment in current_assignments:
                assignment_copy = assignment.copy()
                assignment[next_var] = True
                assignment_copy[next_var] = False
                current_assignments_copies.append(assignment_copy)
            current_assignments.extend(current_assignments_copies)
            next_var = searched_variables.pop(0)

        #target found
        if current_node == target:
            overall_assignments.extend(current_assignments)
            return overall_assignments


        #children nodes need to be searched further for target
        current_assignments = [assignment.copy() for assignment in current_assignments]
        for assignment in current_assignments:
            assignment[current_node.variable] = False
        self.find_paths(target, current_node.negative_child, overall_assignments, current_assignments.copy(), searched_variables)
        
        current_assignments = [assignment.copy() for assignment in current_assignments]
        for assignment in current_assignments:
            assignment[current_node.variable] = True
        self.find_paths(target, current_node.positive_child, overall_assignments, current_assignments.copy(), searched_variables)

        return overall_assignments

    def __make_lookup_table_corr_nodes(self, bdd_from: BDD, bdd_to: BDD) -> dict[BDDNode, list[BDDNode]]:
        if bdd_from.variables != bdd_to.variables:
            raise Exception("Both BDDs have to have the same variable priorization!")
        
        #automatically creates empty list in every object
        result = defaultdict(list)
        self.__make_lookup_table_corr_nodes_recursive(bdd_from.variables, bdd_from.root, bdd_to.root, result)
        
        
    #creates a lookup table mapping nodes of bdd1 to corresponding nodes of bdd2 for all non-leaf nodes
    #bdds have to have same variables 
    #needs roots of bdd_from and bdd_to as first node inputs
    def __make_lookup_table_corr_nodes_recursive(self, variables: list[str], node_from: BDDNode = None, node_to: BDDNode = None, result : dict[BDDNode, list[BDDNode]] = None) -> dict[BDDNode, list[BDDNode]]:
        if node_from.isLeaf() or node_to.isLeaf():
            return
                
        #both nodes have the same variable
        if node_from.variable == node_to.variable:
            result[node_from].append(node_to)
            self.make_lookup_table_corr_nodes(node_from.negative_child, node_to.negative_child, result)
            self.make_lookup_table_corr_nodes(node_from.positive_child, node_to.positive_child, result)
        #variables don't match -> one graph is skipping at least one node 
        #node from has higher priority
        elif variables.index(node_from.variable) < variables.index(node_to.variable):
            print("None")
        #node from has lower priority, higher index in variables
        # -> bdd_from skipped a variable
        # -> also skip variable in bdd_to 
        else:
            self.make_lookup_table_corr_nodes(node_from, node_to.negative_child, result)
            self.make_lookup_table_corr_nodes(node_from, node_to.positive_child, result)
        

    #makes a copy of BDD and negates it
    def negate(self):
        negated_BDD = self.copy_bdd()
        #negate leaf values
        false_leaf = negated_BDD.leafs[False]
        false_leaf.value = True
        true_leaf = negated_BDD.leafs[True]
        true_leaf.value = False

        #switch leafs in dictionary
        negated_BDD.leafs[True] = false_leaf
        negated_BDD.leafs[False] = true_leaf

        negated_BDD.expression = "not (" + negated_BDD.expression + ")"
        return negated_BDD

    #TODO: assignment not set properly
    @staticmethod
    def apply_operand(BDD1: BDD, BDD2: BDD, operand: str, variable_order: list) -> BDD:
        for var in BDD1.variables:
            if var not in variable_order:
                raise Exception("Variable " + var + " from BDD1 not found in variables.")

        for var in BDD2.variables:
            if var not in variable_order:
                raise Exception("Variable " + var + " from BDD2 not found in variables.")

        if operand not in ops_lookup:
            raise Exception("Operand " + operand + " not supported.")

        united_bdd = BDD(expression="(" + BDD1.expression + ")and(" + BDD2.expression + ")", variables=variable_order,
                        build_new=False)
        united_bdd.root = BDD.__apply_operand_recursion(BDD1.root, BDD2.root, operand, variable_order, united_bdd)
        united_bdd.reduce()
        return united_bdd

    @staticmethod
    def __apply_operand_recursion(node1: BDDNode, node2: BDDNode, operand: str, variable_order: list[str], united_bdd: BDD) -> BDDNode:
        node1_var = None
        node2_var = None
        if node1.variable:
            #add "_" if var is alt, so it can be looked up in variabole order
            node1_var = node1.variable + "_" if node1.is_alt else node1.variable
            if node1_var not in variable_order:
                raise Exception(f"{node1_var} not in variable order {variable_order}.")
        if node2.variable:
            node2_var = node2.variable + "_" if node2.is_alt else node2.variable
            if node2_var not in variable_order:
                raise Exception(f"{node2_var} not in variable order {variable_order}.")

        # if both nodes are leafs return new leaf with united value
        if node1.isLeaf() and node2.isLeaf():
            solution = BDDNode(value =  ops_lookup[operand](node1.value, node2.value))
            return solution

        # if both nodes are of the same variable unite the negative children and positive children of both bdd
        elif node1_var == node2_var:
            solution = BDDNode(var=node1_var, is_alt=node1.is_alt)
            solution.negative_child = BDD.__apply_operand_recursion(node1.negative_child, node2.negative_child, operand, variable_order, united_bdd)
            solution.positive_child = BDD.__apply_operand_recursion(node1.positive_child, node2.positive_child, operand, variable_order, united_bdd)
            if solution.negative_child is None or solution.positive_child is None:
                raise Exception("Children are None")
            #solution.reduce(united_bdd)
            return solution

        # if variables don't match determine higher priority variable and unite children of higher prio variable with
        # lower prio BDD
        else:
            gen = (var for var in variable_order if var == node1_var or var == node2_var)
            higher_variable = next(gen)

            if node1_var == higher_variable:
                higher_prio = node1
                lower_prio = node2
            else:
                higher_prio = node2
                lower_prio = node1

            solution = BDDNode(var=higher_prio.variable, is_alt=higher_prio.is_alt)
            solution.negative_child = BDD.__apply_operand_recursion(higher_prio.negative_child, lower_prio, operand, variable_order, united_bdd)
            solution.positive_child = BDD.__apply_operand_recursion(higher_prio.positive_child, lower_prio, operand, variable_order, united_bdd)
            if (solution.negative_child is None) or (solution.positive_child is None):
                raise Exception("Children are None")
            #TODO: doesn't work with reduced bdd's 
            #in example child node C without neg or pos child??
            return solution

    #creates a copy of BDD gives it is_alt attribute
    def rename_variables(self) -> BDD:
        if self.root.is_alt:
            raise Exception("BDD already renamed!")
        return self.__copy(True)

    #gives a copy of bdd
    def copy_bdd(self) -> BDD:
        return self.__copy(False)

    def __copy(self, rename: bool) -> BDD:
        expression_copy = self.expression
        if rename:
            var_copy = [var + "_" for var in self.variables.copy()]
            for var in self.variables:
                #replace var in expression if a space follows it
                expression_copy = re.sub(var + " ", var + "_" + " ", expression_copy)
        else:
            var_copy = self.variables.copy()

        if not rename: rename = self.root.is_alt

        bdd_copy = BDD(expression_copy, var_copy, build_new=False)
        bdd_copy.root = self.__replace_children_nodes(self.root, {}, rename)
        bdd_copy.__merge_leafs(bdd_copy.root)
        return bdd_copy

    def __replace_children_nodes(self, original_node: BDDNode, visited_nodes: dict[BDDNode, BDDNode], is_alt: bool):
        #if original node is already copied use the copy
        if original_node in visited_nodes:
            node_copy = visited_nodes[original_node]
            return node_copy

        if original_node.isLeaf():
            node_copy = original_node.copy_node(is_alt)
            return node_copy

        node_copy = original_node.copy_node(is_alt)
        node_copy.negative_child = self.__replace_children_nodes(original_node.negative_child, visited_nodes, is_alt)
        node_copy.positive_child = self.__replace_children_nodes(original_node.positive_child, visited_nodes, is_alt)
        #map copy of Node to the original node
        visited_nodes[original_node] = node_copy
        return node_copy

    #only use if original and alternative Variables are united
    def set_probabilities(self, probabilities: dict[str: list[mpq]]):
        root = self.root
        if root.isLeaf():
            raise Exception("Tree needs at least one Node that isn't a leaf!")
        #root handled separately because it does not have a parent node
        if not root.is_alt:
            #p of only x
            root.negative_probability[root] = probabilities[root.variable][0] + probabilities[root.variable][2]
            #p of only not x
            root.positive_probability[root] = probabilities[root.variable][1] + probabilities[root.variable][3]
        else:
            #p of only x_
            root.negative_probability[root] = probabilities[root.variable][0] + probabilities[root.variable][1]
            #p of only not x_
            root.positive_probability[root] = probabilities[root.variable][1] + probabilities[root.variable][0]
        self.__set_probabilities_recursion(root, probabilities)
        self.probabilities_set = True
        return

    #helper method for set_probabilities
    #sets probabilities of children for each parent node separately
    def __set_probabilities_recursion(self, current_node: BDDNode, probabilities: dict[str: list[mpq]]):
        # example of table/list:
        # x'\x     0        1
        # 0    [0] 0.2   [1] 0.3
        # 1    [2] 0.4   [3] 0.1

        negative_child = current_node.negative_child
        positive_child = current_node.positive_child

        if not negative_child.isLeaf():
            if not current_node.is_alt and current_node.variable == negative_child.variable:
                #child needs to be alt of current node -> current probability affects alt child probability
                p_list = probabilities[current_node.variable]

                # p = (p not x and not x_) / (p not x)
                negative_child.negative_probability[current_node] = p_list[0] / (p_list[0] + p_list[2])
                # p = (p not x and x_) / (p not x)
                negative_child.positive_probability[current_node] = p_list[2] / (p_list[0] + p_list[2])

            #child is not influenced by current node probability
            else:
                p_list = probabilities[negative_child.variable]
                if not negative_child.is_alt:
                    # p = not x
                    negative_child.negative_probability[current_node] = p_list[0] + p_list[2]
                    # p = x
                    negative_child.positive_probability[current_node] = p_list[1] + p_list[3]
                else:
                    #child is alt child but doesn't match variable --> add both alt probabilities
                    # p = not x_
                    negative_child.negative_probability[current_node] = p_list[0] + p_list[1]
                    #p = not x
                    negative_child.positive_probability[current_node] = p_list[2] + p_list[3]
            self.__set_probabilities_recursion(negative_child, probabilities)

        #same for positive child
        if not positive_child.isLeaf():
            if not current_node.is_alt and current_node.variable == positive_child.variable:
                #child needs to be alt of current node -> current probability affects alt child probability
                p_list = probabilities[current_node.variable]

                # p = (p x and not x_) / (p x)
                positive_child.negative_probability[current_node] = p_list[1] / (p_list[1] + p_list[3])
                # p = (p x and x_) / (p x)
                positive_child.positive_probability[current_node] = p_list[3] / (p_list[1] + p_list[3])

            #child is not influenced by current node probability
            else:
                p_list = probabilities[positive_child.variable]

                if not positive_child.is_alt:
                    # p = not x
                    positive_child.negative_probability[current_node] = p_list[0] + p_list[2]
                    # p = x
                    positive_child.positive_probability[current_node] = p_list[1] + p_list[3]
                else:
                    #child is alt child but doesn't match variable --> add both alt probabilities
                    # p = not x_
                    positive_child.negative_probability[current_node] = p_list[0] + p_list[1]
                    #p = not x
                    positive_child.positive_probability[current_node] = p_list[2] + p_list[3]
            self.__set_probabilities_recursion(positive_child, probabilities)

            #end case: both children are leafs
        return

    #only use if probabilities are set
    def sum_probabilities_positive_cases(self):
        if not self.probabilities_set:
            raise Exception("Set the probabilities first.")
        return self.__sum_probabilities_helper(self.root, self.root, path_mul=mpq(1))

    def __sum_probabilities_helper(self, current_node: BDDNode, parent_node: BDDNode, path_mul: mpq) -> mpq:
        #sum of path is complete
        if current_node.isLeaf():
            #don't sum probabilities of paths that end in zero
            if current_node.value == 0:
                return mpq(0)
            else:
                return path_mul
        else:
            negative_child = current_node.negative_child
            positive_child = current_node.positive_child

            mul_negative_path = self.__sum_probabilities_helper(negative_child, current_node,
                                                                path_mul * current_node.negative_probability[
                                                                    parent_node])
            mul_positive_path = self.__sum_probabilities_helper(positive_child, current_node,
                                                                path_mul * current_node.positive_probability[
                                                                    parent_node])

            return mul_negative_path + mul_positive_path

    def sum_all_probability_paths(self):
        self.__sum_all_probability_paths_recursion(current_node=self.root, visited_nodes={self.root: mpq(1)})
        return

    def __sum_all_probability_paths_recursion(self, current_node: BDDNode, visited_nodes: dict[BDDNode, mpq],
                                            all_path_sum: mpq = 0,
                                            path_mul: mpq = 1) -> mpq:
        #visited_nodes.append(current_node.var if not current_node.is_alt else current_node+"_")
        if current_node.isLeaf():
            all_path_sum += path_mul
            out = "Path: "
            for n in visited_nodes:
                if n.isEmpty():
                    continue
                out = out + (n.variable if not n.is_alt else n.variable + "_") + f": {float(visited_nodes[n]):.2f} "
            print(out + "pathprobability = " + f"{float(path_mul):.2f}" + " new sum = " + f"{float(all_path_sum):.2f}")
            return all_path_sum
        else:

            negative_child = current_node.negative_child
            positive_child = current_node.positive_child
            parent_node = list(visited_nodes.keys())[-1]

            temp1 = dict(visited_nodes)
            temp1[current_node] = current_node.negative_probability[parent_node]
            all_path_sum = self.__sum_all_probability_paths_recursion(negative_child, temp1, all_path_sum,
                                                                      path_mul * current_node.negative_probability[
                                                                        parent_node])

            temp2 = dict(visited_nodes)
            temp2[current_node] = current_node.negative_probability[parent_node]
            all_path_sum = self.__sum_all_probability_paths_recursion(positive_child, temp2, all_path_sum,
                                                                      path_mul * current_node.positive_probability[
                                                                        parent_node])

        return all_path_sum

    # returns list of all nodes in breadth first bottom up order
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

        out.reverse()
        return out

    # Visualization
    def generateDot(self, path="output"):
        #add "_" to node label if BDD is alternative version of another BDD
        node = self.root
        alt_str = "_" if node.is_alt else ""
        label = node.value if node.isLeaf() else node.variable + alt_str

        #make file
        path = f"out\\{path}.dot"
        directory = os.path.dirname(path)

        os.makedirs(directory, exist_ok=True)
        out = open(path, "w")

        #write start of the dot file and the root node
        out.write(f"digraph{{\nlabel=\"{self.expression}\\n\\n\"\n{id(node)}[label={label}]")
        self.__generate_dot_recursive(node, out)
        out.write("}")
        #print(f"Dot File generated: {filename}.dot")
        self.__reset_draw(self.root)

    def __generate_dot_recursive(self, node: BDDNode, out):
        if not node.drawn:
            # draw negative_child child node
            if node.negative_child is not None:
                alt_str = "_" if node.negative_child.is_alt else ""
                child_node = node.negative_child
                #get probabilities
                prob_str = ""
                if node.negative_probability is not None:
                    for prob in node.negative_probability:
                        if prob.variable is not None:
                            prob_str = " " + prob_str + (prob.variable if not prob.is_alt else prob.variable + "_") + " "
                        prob_str = prob_str + f"{float(node.negative_probability[prob]):.2f}"
                        prob_str = prob_str + "\\n"
                if child_node.variable is not None:
                    #draw child node
                    assignments = "\n".join(str(d) for d in child_node.assignments)
                    out.write(f"{id(child_node)}[label=\"{child_node.variable + alt_str}\n{assignments}\"]\n")
                    #draw edge node -> child_node
                    out.write(f"{id(node)} -> {id(child_node)}[style=dashed label=\"{prob_str}\" fontcolor = gray]\n")
                    self.__generate_dot_recursive(child_node, out)
                elif node.negative_child.value is not None:
                    #draw leaf node
                    assignments = "\n".join(str(d) for d in child_node.assignments)
                    out.write(f"{id(child_node)}[label=\"{child_node.value}\n{assignments}\"]\n")
                    #draw edge node -> leaf node
                    out.write(f"{id(node)} -> {id(child_node)}[style=dashed label=\"{prob_str}\" fontcolor = gray]\n")
            #draw positive childnode
            if node.positive_child is not None:
                child_node = node.positive_child
                alt_str = "_" if node.positive_child.is_alt else ""
                #get probability
                prob_str = ""
                if node.positive_probability is not None:
                    for prob in node.positive_probability:
                        if prob.variable is not None:
                            prob_str = " " + prob_str + (prob.variable if not prob.is_alt else prob.variable + "_") + " "
                        prob_str = prob_str + f"{float(node.positive_probability[prob]):.2f}"
                        prob_str = prob_str + "\\n"
                if child_node.variable is not None:
                    #draw child node
                    assignments = "\n".join(str(d) for d in child_node.assignments)
                    out.write(f"{id(child_node)}[label=\"{child_node.variable + alt_str}\n{assignments}\"]\n")
                    #draw edge node -> child node
                    out.write(f"{id(node)} -> {id(child_node)} [label=\"{prob_str}\" fontcolor = gray]\n")
                    self.__generate_dot_recursive(child_node, out)
                elif child_node.value is not None:
                    #draw leaf node 
                    assignments = "\n".join(str(d) for d in child_node.assignments)
                    out.write(f"{id(child_node)}[label=\"{child_node.value}\n{assignments}\"]\n")
                    #draw edge node -> leaf node
                    out.write(f"{id(node)} -> {id(child_node)} [label=\"{prob_str}\" fontcolor = gray]\n")
            node.drawn = True

    def __reset_draw(self, node):
        if node.isLeaf():
            node.drawn = False
        if node.negative_child is not None:
            self.__reset_draw(node.negative_child)
        if node.positive_child is not None:
            self.__reset_draw(node.positive_child)
        node.drawn = False

    def __eq__(self, other):
        if other is None or not isinstance(other, BDD):
            return False
        return(
            self.variables == other.variables and
            self.expression == other.expression and
            #checks all child nodes in tree
            self.root == other.root
        )

def evaluate_expression(expr, assignment):
    return eval(expr, {}, assignment)


def main():
    #Example:
    # e = "(A and B) or C"
    # e = "((not A or B) and (not B or A)) and ((not C or D) and (not D or C))"
    # v = ['A', 'B', 'C', 'D']
    # bdd = BDD(e, v)
    #
    # print("Binary Decision Diagram (BDD):")
    # bdd.generateDot(filename="out")
    # bdd.reduce()
    # bdd.generateDot(filename="reduced_out")
    # bdd.negate()
    # bdd.generateDot(filename="negated_out")
    # for k, v in bdd.evaluation.items():
    #     print(f"{k}: {v}")
    #

    delete_all_files_from_out()
    expression1 = "A or B"
    expression2 = "(B or C) and (A and C)"
    variables = ['A', 'B', 'C']

    p = {
        "A": [mpq(0.2), mpq(0.3), mpq(0.4), mpq(0.1)],
        "B": [mpq(0.15), mpq(0.6), mpq(0.13), mpq(0.12)],
        "C": [mpq(0.23), mpq(0.17), mpq(0.2), mpq(0.4)]
    }

    bdd1 = BDD(expression1, variables)
    bdd1.reduce()
    bdd1.generateDot("1_bdd1")

    bdd2 = BDD(expression2, variables)
    bdd2.reduce()
    bdd2.generateDot("2_bdd2")

    bdd2_replaced = bdd2.rename_variables()
    bdd2_replaced.generateDot("3_bdd2_replaced")
    bdd2_replaced = bdd2_replaced.negate()
    bdd2_replaced.generateDot("4_bdd_2_negate")

    united = BDD.apply_operand(bdd1, bdd2_replaced,"and", ["A", "A_", "B", "B_", "C", "C_"])
    united.generateDot(path="5_united")
    united.set_probabilities(p)
    united.generateDot(path="6_united_w_prob")
    print(f"Sum of all positive paths is: {float(united.sum_probabilities_positive_cases()):.2f}\n")
    united.sum_all_probability_paths()


if __name__ == "__main__":
    main()
